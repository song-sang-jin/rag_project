"""원본 API 응답의 의미를 유지하는 도메인 변환. 빈 값은 무조건 허용으로 해석하지 않는다."""
import json
import re
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import API_GUIDE, DATA, settings

FIELDS = {
    "plcyNm": "정책명", "plcyNo": "정책번호", "lclsfNm": "분야", "mclsfNm": "세부분야",
    "plcyKywdNm": "키워드", "plcyExplnCn": "설명", "plcySprtCn": "지원 내용",
    "sprvsnInstCdNm": "주관기관", "operInstCdNm": "운영기관", "zipCd": "거주지역코드",
    "sprtTrgtAgeLmtYn": "연령제한 코드(원본)", "sprtTrgtMinAge": "최소연령(원본)",
    "sprtTrgtMaxAge": "최대연령(원본)", "earnCndSeCd": "소득조건 코드(원본)",
    "earnMinAmt": "소득최소금액(원본)", "earnMaxAmt": "소득최대금액(원본)",
    "earnEtcCn": "소득 상세조건", "jobCd": "취업요건 코드(원본)", "schoolCd": "학력요건 코드(원본)",
    "addAplyQlfcCndCn": "추가 자격조건", "ptcpPrpTrgtCn": "참여 제한 대상",
    "aplyYmd": "신청 기간", "bizPrdBgngYmd": "사업 시작일", "bizPrdEndYmd": "사업 종료일",
    "bizPrdEtcCn": "기간 기타사항", "plcyAplyMthdCn": "신청 방법", "srngMthdCn": "심사 방법",
    "sbmsnDcmntCn": "제출서류", "etcMttrCn": "기타사항", "lastMdfcnDt": "원본 최종수정일"
}
CODEBOOK = json.loads(Path(__file__).with_name("codebook.json").read_text(encoding="utf-8"))


def now():
    return datetime.now(ZoneInfo("Asia/Seoul")).isoformat(timespec="seconds")


def clean(value):
    return "" if value is None else str(value).strip()


def policy_categories(p):
    """복수 분야를 분리하되 원본 분류명은 유지한다."""
    return list(dict.fromkeys(part.strip() for part in clean(p.get("lclsfNm")).split(",")
                              if part.strip()))


def policy_url(p):
    return "https://www.youthcenter.go.kr/youthPolicy/ythPlcyTotalSearch/ythPlcyDetail/" + str(p["plcyNo"])


def in_scope(p, cfg=None):
    cfg = cfg or settings()
    if clean(p.get("pvsnInstGroupCd")) == cfg["central_group"]:
        return True
    # 시군구 정확 코드만 사용. '전남광주' 기관명으로 다른 군까지 광주로 편입하지 않는다.
    codes = set(re.findall(r"(?<!\d)\d{5}(?!\d)", clean(p.get("zipCd"))))
    institution = clean(p.get("rgtrHghrkInstCd"))
    local_institution = institution in cfg["gwangju_institution_codes"]
    return local_institution and bool(codes.intersection(cfg["gwangju_codes"]))


def policy_text(p):
    lines = []
    for field, label in FIELDS.items():
        value = clean(p.get(field))
        mapping = CODEBOOK.get(field)
        decoded = ""
        if value and mapping:
            decoded = " / ".join(mapping.get(code, f"미해석 코드 {code}") for code in re.split(r"[,\s]+", value) if code)
        lines.append(f"{label}: {value or '미기재(확인 필요)'}" + (f" [공식 코드표: {decoded}]" if decoded else ""))
    return "\n".join(lines)


def metadata(p, source_type="항목 메타데이터"):
    return {"policy_id": str(p["plcyNo"]), "title": clean(p.get("plcyNm")),
            "source": policy_url(p), "source_type": source_type,
            "region": clean(p.get("zipCd")), "group": clean(p.get("pvsnInstGroupCd")),
            "category": clean(p.get("lclsfNm")), "collected_at": now()}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def load_policies():
    path = DATA / "policies.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def validate(policies):
    fields = ["plcyNm", "plcySprtCn", "aplyYmd", "plcyAplyMthdCn", "sbmsnDcmntCn", "refUrlAddr1"]
    return {"count": len(policies), "unique_ids": len({p["plcyNo"] for p in policies}),
            "central": sum(p.get("pvsnInstGroupCd") == "0054001" for p in policies),
            "missing": {f: sum(not clean(p.get(f)) for p in policies) for f in fields},
            "checked_at": now(), "source": API_GUIDE}
