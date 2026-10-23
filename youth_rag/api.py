import hashlib
import json
import time
from pathlib import Path
import requests
from .config import API_URL, DATA, settings, youth_key
from .data import in_scope, now, validate, write_json


class YouthAPI:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "YouthPolicyLab/1.0 educational-project"
        self.last_request = 0.0

    def get(self, **params):
        if not youth_key():
            raise ValueError(".env에 YOUTH_API_KEY 또는 API_KEY가 필요합니다.")
        query = {"rtnType": "json", "pageNum": 1, "pageSize": 100, **params}
        for attempt in range(5):
            time.sleep(max(0, settings()["request_interval"] - (time.monotonic() - self.last_request)))
            try:
                self.last_request = time.monotonic()
                response = self.session.get(API_URL, params={**query, "apiKeyNm": youth_key()}, timeout=45)
                if response.status_code in (429, 500, 502, 503, 504):
                    delay = response.headers.get("Retry-After", "")
                    time.sleep(min(float(delay) if delay.replace('.', '', 1).isdigit() else 2 ** (attempt + 1), 30))
                    continue
                if response.status_code != 200:
                    raise ValueError("정책 API 인증 또는 요청 오류입니다. 키 승인 상태를 확인하세요.")
                payload = response.json()
                if str(payload.get("resultCode")) != "200":
                    raise ValueError("정책 API가 성공 응답을 반환하지 않았습니다. 키와 사용 한도를 확인하세요.")
                return payload["result"]
            except (requests.RequestException, json.JSONDecodeError):
                if attempt == 4:
                    break
                time.sleep(2 ** attempt)
        # 요청 예외에는 인증키를 포함한 URL이 있을 수 있어 외부로 전달하지 않는다.
        raise RuntimeError("정책 API 연결이 지연됩니다. 잠시 후 다시 시도하세요.") from None

    def detail(self, policy_id):
        items = self.get(plcyNo=policy_id, pageType=2)["youthPolicyList"]
        return next((p for p in items if str(p.get("plcyNo")) == str(policy_id)), None)

    def search(self, query):
        return self.get(plcyNm=query, pageSize=10)["youthPolicyList"]


def collect_all(progress=lambda message: None, refresh=False):
    api = YouthAPI()
    raw, page, total = {}, 1, 1
    while len(raw) < total:
        cache = DATA / "pages" / f"{page:04}.json"
        if cache.exists() and not refresh:
            result = json.loads(cache.read_text(encoding="utf-8"))
        else:
            result = api.get(pageNum=page, pageSize=settings()["page_size"])
            write_json(cache, result)
        total = int(result["pagging"]["totCount"])
        items = result["youthPolicyList"]
        if not items:
            break
        before = len(raw)
        raw.update({str(p["plcyNo"]): p for p in items if p.get("plcyNo")})
        progress(f"정책 수집 {len(raw):,} / {total:,}건")
        page += 1
        if len(raw) == before:
            raise RuntimeError("API 페이지가 반복됩니다. 수집을 중단했습니다.")
    if len(raw) < total:
        raise RuntimeError("전체 정책 수집이 완료되지 않았습니다. 다시 시도하면 저장된 페이지부터 재사용합니다.")
    all_items = list(raw.values())
    selected = [p for p in all_items if in_scope(p)]
    write_json(DATA / "all_policies.json", all_items)
    write_json(DATA / "policies.json", selected)
    report = validate(selected)
    report.update({"all_count": len(all_items), "scope": "중앙부처 + 광주 법정시군구 코드", "collected_at": now()})
    write_json(DATA / "validation.json", report)
    return selected, report
