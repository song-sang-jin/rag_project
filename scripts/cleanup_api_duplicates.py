"""이전 RAG 2의 API 복제 청크만 백업 후 정리한다. 기본 실행은 조회만 한다."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youth_rag.config import DATA
from youth_rag.data import write_json, now
from youth_rag.store import Store


def cleanup(store, backup_path, apply=False):
    rows = store.docs.get(where={"source_type": "온통청년 API 상세"},
                          include=["documents", "metadatas", "embeddings"])
    indices = [i for i, meta in enumerate(rows["metadatas"])
               if meta.get("doc_id") == "api-" + str(meta.get("policy_id"))]
    result = {"found": len(indices), "removed": 0,
              "metadata_count": store.meta.count(), "external_before": store.docs.count()}
    if apply and indices:
        records = [{"id": rows["ids"][i], "document": rows["documents"][i],
                    "metadata": rows["metadatas"][i], "embedding": list(map(float, rows["embeddings"][i]))}
                   for i in indices]
        write_json(Path(backup_path), {"created_at": now(), "collection": store.docs.name, "records": records})
        store.docs.delete(ids=[r["id"] for r in records])
        result.update(removed=len(records), backup=str(backup_path))
    result["external_after"] = store.docs.count()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = DATA / "backups" / ("api_duplicates_" + now().replace(":", "-") + ".json")
    import json
    print(json.dumps(cleanup(Store(), path, args.apply), ensure_ascii=False))
