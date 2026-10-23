"""F2001~F2012 실데이터 검증. 키/공급자 예외 원문은 출력하지 않는다."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youth_rag.config import DATA
from youth_rag.data import load_policies, now, write_json
from youth_rag.api import YouthAPI
from youth_rag.service import PolicyService


def main():
    report = {"checked_at": now(), "checks": {}}
    def record(name, result):
        report["checks"][name] = result
        write_json(DATA / "requirements_audit.json", report)
        print(name, json.dumps(result, ensure_ascii=False), flush=True)
    svc = PolicyService()
    policies = load_policies()
    ids = {str(p["plcyNo"]) for p in policies}
    stored = set(svc.store.meta.get()["ids"])
    record("F2001_F2002_counts", {"policies": len(policies), "unique": len(ids),
                                  "indexed": len(stored), "exact_ids": ids == stored,
                                  "external_chunks": svc.store.docs.count()})
    if ids == stored:
        svc.store.index_policies(policies)
        record("F2002_repeat_index", {"before": len(stored), "after": svc.store.meta.count()})
    p = next(p for p in policies if "구직활동수당" in p["plcyNm"])
    try:
        detail = YouthAPI().detail(p["plcyNo"])
        record("policy_api_live", {"found": bool(detail), "same_id": bool(detail) and detail["plcyNo"] == p["plcyNo"]})
    except Exception as exc:
        record("policy_api_live", {"error_type": type(exc).__name__})
    try:
        found = svc.explore("광주에 거주하는 청년을 위한 주거 지원 정책을 찾고 있어요.", policies)
        write_json(DATA / "audit_explore.json", found)
        record("F2011_live", {"count": len(found), "policies": [r["policy"]["plcyNm"] for r in found],
                              "all_known_ids": all(str(r["policy"]["plcyNo"]) in ids for r in found)})
    except Exception as exc:
        record("F2011_live", {"error_type": type(exc).__name__})
    try:
        # 동일 retrieve/generate 그래프에 이미 저장된 근거만 전달하여 생성 동작을 분리 검증.
        evidence = svc.store.retrieve(p["plcyNo"], "제출서류 신청 조건", 6)
        class ExistingEvidence:
            def retrieve(self, *args):
                return evidence
        class NoCollection:
            def collect(self, *args):
                return {"cache_hits": 0}
        service = PolicyService(ExistingEvidence(), collector=NoCollection())
        for label, question in [
            ("F2010_checklist", "이 정책의 신청 준비 체크리스트를 제출서류와 신청 순서 중심으로 간결하게 알려줘."),
            ("F2007_unknown", "이 정책 담당자의 개인 휴대전화 번호와 다음 모집 추첨 당첨번호를 알려줘.")]:
            result = service.answer(p, question)
            write_json(DATA / (label + ".json"), {"answer": result["answer"], "evidence": evidence, "sources": result["sources"]})
            record(label, {"answer": result["answer"], "sources": len(result["sources"])})
    except Exception as exc:
        record("generation_live", {"error_type": type(exc).__name__})


if __name__ == "__main__":
    main()
