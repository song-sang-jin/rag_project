import re
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from .data import now, policy_text, policy_categories
from .documents import Collector
from .models import ask_json, chat, recent_history
from .prompts import ANSWER, EXPLORE, CHECKLIST
from .store import Store
from .config import settings
from .grounding import requested_scopes, missing_scopes, unavailable


class State(TypedDict, total=False):
    question: str
    policy: dict
    history: list
    top_k: int
    method: str
    evidence: list
    trace: dict
    answer: str
    sources: list


def cited_sources(answer, evidence):
    numbers = {int(n) for n in re.findall(r"\[근거\s+(\d+)\]", answer)}
    return [{"number": i, **row} for i, row in enumerate(evidence, 1) if i in numbers]


class PolicyService:
    def __init__(self, store=None, model=None, collector=None):
        self.store = store or Store()
        self.model = model
        self.collector = collector or Collector(self.store, model=model)

    def graph(self, progress=lambda msg: None):
        def retrieve(state):
            trace = self.collector.collect(state["policy"], state["question"], state.get("history"), progress)
            progress("선택 정책의 근거를 검색하고 있습니다.")
            evidence = self.store.retrieve(state["policy"]["plcyNo"], state["question"], state.get("top_k"), state.get("method"))
            return {"trace": trace, "evidence": evidence}

        def generate(state):
            evidence = state["evidence"]
            if not evidence:
                return {"answer": "문서에서 확인되지 않습니다. 색인과 원문을 확인해 주세요.", "sources": []}
            scopes = requested_scopes(state["question"], state["policy"]["plcyNm"])
            missing = missing_scopes(scopes, evidence)
            if missing:
                return {"answer": unavailable(missing), "sources": []}
            progress("근거를 바탕으로 답변을 작성하고 있습니다.")
            context = "\n\n".join(f"[근거 {i}] {r['meta']['title']}\n출처 유형: {r['meta']['source_type']}\n적용 범위: {r['meta'].get('applicability') or r['meta']['title']}\n{r['text']}" for i, r in enumerate(evidence, 1))
            import json
            payload = {"today": now()[:10], "selected_policy": state["policy"]["plcyNm"],
                       "question": state["question"], "history": recent_history(state.get("history"), 8), "evidence": context}
            if "체크리스트" in state["question"]:
                result = ask_json(CHECKLIST, payload, self.model or chat(max_output_tokens=1100))
                lines = []
                for item in result.get("items", [])[:7]:
                    if not isinstance(item, dict):
                        continue
                    text = str(item.get("text", "")).strip()
                    numbers = list(dict.fromkeys(n for n in item.get("citations", [])
                                                 if type(n) is int and 1 <= n <= len(evidence)))
                    if not text or (not numbers and "확인되지" not in text):
                        continue
                    label = str(item.get("label", "준비사항")).strip()
                    lines.append(f"- **{label}**: {text} " + " ".join(f"[근거 {n}]" for n in numbers))
                answer = "\n".join(lines) or "문서에서 확인되지 않습니다. 신청 준비사항을 정리할 근거가 부족합니다."
            else:
                response = (self.model or chat(max_output_tokens=settings().get("answer_max_tokens", 1400))).invoke([("system", ANSWER), ("human", json.dumps(payload, ensure_ascii=False))])
                if getattr(response, "response_metadata", {}).get("finish_reason") == "length":
                    return {"answer": "답변 길이 제한으로 생성을 마치지 못했습니다. 제출서류·자격·신청순서 중 하나를 골라 다시 질문해 주세요.", "sources": []}
                answer = str(response.content)
            valid = set(range(1, len(evidence)+1))
            answer = re.sub(r"\[근거\s+(\d+)\]", lambda m: m[0] if int(m[1]) in valid else "[근거 확인 불가]", answer)
            sources = cited_sources(answer, evidence)
            missing = missing_scopes(scopes, sources)
            if missing:
                return {"answer": unavailable(missing), "sources": []}
            return {"answer": answer, "sources": sources}

        builder = StateGraph(State)
        builder.add_node("retrieve", retrieve)
        builder.add_node("generate", generate)
        builder.add_edge(START, "retrieve")
        builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", END)
        return builder.compile()

    def answer(self, policy, question, history=None, top_k=6, method="similarity", progress=lambda msg: None):
        return self.graph(progress).invoke({"policy": policy, "question": question, "history": history or [],
                                            "top_k": top_k, "method": method})

    def explore(self, question, policies, category="전체", group="전체"):
        # 먼저 카테고리/기관 하드필터, 이후 의미검색, 마지막 LLM 조건 선별.
        allowed = {str(p["plcyNo"]): p for p in policies
                   if (category == "전체" or category in policy_categories(p))
                   and (group == "전체" or p.get("pvsnInstGroupCd") == group)}
        if not allowed:
            return []
        where = {"policy_id": {"$in": list(allowed)}}
        rows = self.store.search(self.store.meta, question, 20, where)
        candidates = [{"id": r["id"], "text": r["text"]} for r in rows]
        selected = ask_json(EXPLORE, {"today": now()[:10], "conditions": question, "candidates": candidates}, self.model)
        found, seen = [], set()
        returned_ids = {r["id"] for r in rows}
        for match in selected.get("matches", []):
            # 모델이 제외 항목을 목록에 섞거나 판단을 누락하면 추천하지 않는다.
            if match.get("eligible") is not True:
                continue
            reason = str(match.get("reason", ""))
            if "제외" in reason or "부합하지 않" in reason or "매치 불가" in reason:
                continue
            pid = str(match.get("id", ""))
            if pid in returned_ids and pid in allowed and pid not in seen:
                seen.add(pid)
                found.append({"policy": allowed[pid], "reason": reason})
        return found
