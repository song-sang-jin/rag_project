import hashlib
import json
import numpy as np
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .config import DATA, settings
from .data import metadata, policy_text
from .models import embeddings


def fingerprint(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Store:
    def __init__(self, path=None, embedder=None):
        self.cfg = settings()
        self.embedder = embedder
        self.client = chromadb.PersistentClient(path=str(path or DATA / "chroma"))
        # 모델 변경 시 이전 임베딩 공간과 섞지 않는다.
        suffix = fingerprint(self.cfg["embedding_model"])[:8]
        self.meta = self.client.get_or_create_collection(f"policy_meta_{suffix}", embedding_function=None)
        self.docs = self.client.get_or_create_collection(f"policy_docs_{suffix}", embedding_function=None)

    def embed(self):
        if self.embedder is None:
            self.embedder = embeddings()
        return self.embedder

    def upsert(self, collection, records):
        changed = []
        for start in range(0, len(records), 100):
            batch = records[start:start + 100]
            old = collection.get(ids=[r["id"] for r in batch], include=["metadatas"])
            hashes = {i: m.get("content_hash") for i, m in zip(old["ids"], old["metadatas"])}
            changed.extend(r for r in batch if hashes.get(r["id"]) != fingerprint(r["text"]))
        for start in range(0, len(changed), 32):
            batch = changed[start:start + 32]
            vectors = self.embed().embed_documents([r["text"] for r in batch])
            collection.upsert(ids=[r["id"] for r in batch], documents=[r["text"] for r in batch],
                              embeddings=vectors, metadatas=[{**r["meta"], "content_hash": fingerprint(r["text"])} for r in batch])
        return len(changed)

    def index_policies(self, policies, progress=lambda msg: None):
        for start in range(0, len(policies), 32):
            batch = policies[start:start + 32]
            self.upsert(self.meta, [{"id": str(p["plcyNo"]), "text": policy_text(p), "meta": metadata(p)} for p in batch])
            progress(f"정책 색인 {min(start + 32, len(policies)):,} / {len(policies):,}건")
        current = {str(p["plcyNo"]) for p in policies}
        stale = set(self.meta.get()["ids"]) - current
        if stale:
            self.meta.delete(ids=list(stale))
            for pid in stale:
                self.docs.delete(where={"policy_id": pid})

    def index_document(self, document):
        return self.index_documents([document])

    def index_documents(self, documents):
        """PDF 여러 페이지도 한 번에 모아 임베딩 API를 32개 청크씩 호출한다."""
        cfg = settings()
        splitter = RecursiveCharacterTextSplitter(chunk_size=cfg["chunk_size"], chunk_overlap=cfg["chunk_overlap"])
        records = []
        current_ids = {}
        for document in documents:
            base = f"{document['policy_id']}:{document['doc_id']}"
            ids = current_ids.setdefault((document["policy_id"], document["doc_id"]), set())
            for i, text in enumerate(splitter.split_text(document["text"])):
                m = {k: str(document.get(k, "")) for k in ["policy_id", "title", "source", "source_type", "collected_at", "query", "doc_id", "applicability", "selection_reason", "document_role"]}
                m["chunk_index"] = i
                scope = m.get("applicability")
                scoped_text = f"문서 적용 범위: {scope}\n{text}" if scope else text
                record_id = f"{base}:{i}"
                records.append({"id": record_id, "text": scoped_text, "meta": m})
                ids.add(record_id)
        if records:
            self.upsert(self.docs, records)
            for (policy_id, doc_id), ids in current_ids.items():
                old = self.docs.get(where={"$and": [{"policy_id": policy_id}, {"doc_id": doc_id}]})["ids"]
                stale = set(old) - ids
                if stale:
                    self.docs.delete(ids=list(stale))

    def one_policy(self, policy_id):
        result = self.meta.get(ids=[str(policy_id)], include=["documents", "metadatas"])
        return self.rows(result)[0] if result["ids"] else None

    @staticmethod
    def rows(result, nested=False):
        ids = result["ids"][0] if nested else result["ids"]
        texts = result["documents"][0] if nested else result["documents"]
        metas = result["metadatas"][0] if nested else result["metadatas"]
        return [{"id": i, "text": t, "meta": m} for i, t, m in zip(ids, texts, metas)]

    def search(self, collection, question, k, where=None, method="similarity"):
        if not collection.count():
            return []
        vector = self.embed().embed_query(question)
        kwargs = {"where": where} if where else {}
        n = min(collection.count(), max(k, self.cfg["fetch_k"] if method == "mmr" else k))
        result = collection.query(query_embeddings=[vector], n_results=n,
                                  include=["documents", "metadatas", "embeddings"], **kwargs)
        rows = self.rows(result, True)
        if method != "mmr" or len(rows) <= k:
            return rows[:k]
        matrix = np.array(result["embeddings"][0], dtype=float)
        matrix /= np.maximum(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-12)
        q = np.array(vector) / max(np.linalg.norm(vector), 1e-12)
        relevance = matrix @ q
        chosen = []
        while len(chosen) < min(k, len(rows)):
            remaining = [i for i in range(len(rows)) if i not in chosen]
            def score(i):
                diversity = max((float(matrix[i] @ matrix[j]) for j in chosen), default=0)
                return self.cfg["mmr_lambda"] * relevance[i] - (1-self.cfg["mmr_lambda"]) * diversity
            chosen.append(max(remaining, key=score))
        return [rows[i] for i in chosen]

    def retrieve(self, policy_id, question, k=None, method=None):
        cfg = settings()
        rows = []
        own = self.one_policy(policy_id)
        if own:
            rows.append(own)
        # 이전 버전의 API 중복 청크는 정리 전에도 근거로 재사용하지 않는다.
        rows += self.search(self.docs, question, max(1, (k or cfg["top_k"]) - 1),
                            {"$and": [{"policy_id": str(policy_id)},
                                      {"source_type": {"$ne": "온통청년 API 상세"}}]},
                            method or cfg["search_method"])
        seen, unique = set(), []
        for row in rows:
            # 출처 유형이 달라도 동일 본문은 한 번만 전달한다.
            digest = fingerprint(row["text"].strip())
            if digest not in seen:
                unique.append(row)
                seen.add(digest)
        return unique[:k or cfg["top_k"]]
