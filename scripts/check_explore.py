import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youth_rag.service import PolicyService
from youth_rag.data import load_policies, write_json, now
from youth_rag.config import DATA

try:
    svc = PolicyService()
    result = svc.explore("광주에 거주하는 청년을 위한 주거 지원 정책을 찾고 있어요.", load_policies())
    write_json(DATA / "explore_smoke.json", {"checked_at": now(), "matches": result})
    print("matches", len(result))
    print("policy_ids", [r["policy"]["plcyNo"] for r in result])
except Exception as exc:
    print("EXPLORE_FAILED", type(exc).__name__)
    raise SystemExit(1) from None
