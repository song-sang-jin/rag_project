import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "policy_service"
API_URL = "https://www.youthcenter.go.kr/go/ythip/getPlcy"
API_GUIDE = "https://www.youthcenter.go.kr/cmnFooter/openapiIntro/oaiDoc"
load_dotenv(ROOT / ".env")


def settings():
    cfg = json.loads((ROOT / "policy_config.json").read_text(encoding="utf-8"))
    if not 0 <= cfg["chunk_overlap"] < cfg["chunk_size"]:
        raise ValueError("청크 중복 길이는 청크 크기보다 작아야 합니다.")
    return cfg


def youth_key():
    return os.getenv("YOUTH_API_KEY") or os.getenv("API_KEY", "")


def llm_credentials():
    key = os.getenv("OPENAI_API_KEY") or os.getenv("GMS_KEY", "")
    base = os.getenv("OPENAI_BASE_URL") or (
        "https://gms.ssafy.io/gmsapi/api.openai.com/v1/" if os.getenv("GMS_KEY") and not os.getenv("OPENAI_API_KEY") else None
    )
    if not key:
        raise ValueError(".env에 GMS_KEY 또는 OPENAI_API_KEY가 필요합니다.")
    return key, base
