"""실제 공급자 연결 검증. 원문 예외/키는 출력하지 않는다."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youth_rag.config import DATA
from youth_rag.data import load_policies, write_json, now
from youth_rag.models import chat, embeddings
from youth_rag.service import PolicyService


def main():
    try:
        vector = embeddings().embed_query("광주 청년 주거 지원")
        print("embedding_dimensions", len(vector), flush=True)
        reply = chat().invoke("연결 확인입니다. 확인이라고만 답하세요.")
        print("chat_connected", bool(reply.content), flush=True)
        policies = load_policies()
        p = next((p for p in policies if "구직활동수당" in p["plcyNm"]), policies[0])
        svc = PolicyService()
        result = svc.answer(p, "이 정책의 지원 내용과 신청할 때 확인할 조건을 알려줘.", progress=lambda m: print(m, flush=True))
        record = {"checked_at": now(), "policy_id": p["plcyNo"], "policy": p["plcyNm"],
                  "answer": result["answer"], "source_count": len(result["sources"]),
                  "evidence_count": len(result["evidence"]), "trace": result["trace"]}
        write_json(DATA / "live_smoke.json", record)
        print(json.dumps(record, ensure_ascii=False, indent=2), flush=True)
        repeat = svc.answer(p, "이 정책의 지원 내용과 신청할 때 확인할 조건을 알려줘.")
        print("repeat_cache_hits", repeat["trace"]["cache_hits"], flush=True)
        print("counts", svc.store.meta.count(), svc.store.docs.count(), flush=True)
    except Exception as exc:
        print("LIVE_CHECK_FAILED", type(exc).__name__, flush=True)
        # 공급자 API 에러 메시지 자체에는 키를 포함한 URL이 있을 수 있어 저장하지 않음.
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
