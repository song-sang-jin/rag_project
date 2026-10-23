"""외부 요청의 간격·일시 오류 재시도를 공통 처리한다. 인증 예외는 밖에 노출하지 않는다."""
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
import requests
from .config import settings

_lock = threading.Lock()
_last_request = {}
_blocked_until = {}
RETRYABLE = {429, 500, 502, 503, 504}


def request(session, method, url, *, deadline=None, **kwargs):
    cfg = settings()
    attempts = max(1, int(cfg.get("external_retries", 1)) + 1)
    interval = float(cfg.get("request_interval", 1))
    host = urlparse(url).netloc
    kwargs.setdefault("timeout", (3, 6))
    for attempt in range(attempts):
        with _lock:
            if _blocked_until.get(host, 0) > time.monotonic():
                raise RuntimeError("출처가 요청한 대기 시간이 남아 있어 외부 요청을 생략했습니다.")
            delay = max(0, interval - (time.monotonic() - _last_request.get(host, 0)))
            if delay:
                if deadline is not None and time.monotonic() + delay >= deadline:
                    raise RuntimeError("외부 요청 제한 시간을 초과했습니다.")
                time.sleep(delay)
            _last_request[host] = time.monotonic()
        try:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("외부 요청 제한 시간을 초과했습니다.")
                timeout = kwargs["timeout"]
                kwargs["timeout"] = tuple(min(t, remaining) for t in timeout) if isinstance(timeout, tuple) else min(timeout, remaining)
            response = getattr(session, method.lower())(url, **kwargs)
        except requests.RequestException:
            if attempt + 1 == attempts:
                raise RuntimeError("외부 요청이 제한 시간 내에 연결되지 않았습니다.") from None
        else:
            if response.status_code not in RETRYABLE or attempt + 1 == attempts:
                return response
            after = response.headers.get("Retry-After", "")
            try:
                delay = max(0, float(after))
            except (ValueError, TypeError):
                try:
                    delay = max(0, (parsedate_to_datetime(after) - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    delay = 2 ** attempt
            # 긴 Retry-After는 짧게 잘라 재요청하지 않고 이번 요청을 실패로 종료한다.
            if delay > float(cfg.get("retry_max_wait", 2)) or (deadline is not None and time.monotonic()+delay >= deadline):
                with _lock:
                    _blocked_until[host] = time.monotonic() + delay
                return response
            response.close()
            time.sleep(delay)
            continue
        time.sleep(min(2 ** attempt, float(cfg.get("retry_max_wait", 2))))
    raise RuntimeError("외부 요청을 완료하지 못했습니다.")
