"""저장된 정책의 참고 URL과 웹 검색으로 외부 문서 본문만 수집한다."""
import ipaddress
import json
import re
import socket
import time
from urllib.parse import urlparse, urljoin
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup
from .http import request
from .pdf_reader import extract_pdf
from .config import DATA, settings
from .data import now, policy_url, write_json
from .models import ask_json, recent_history
from .prompts import SEARCH, SELECT
from .store import fingerprint
from .web_search import WebSearch


def public_url(url):
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    if parts.port not in (None, 80, 443):
        return False
    try:
        return all(ipaddress.ip_address(info[4][0]).is_global for info in socket.getaddrinfo(parts.hostname, None))
    except (ValueError, OSError):
        return False


def fetch_public(url, *, allow_short=False):
    """차단/로그인 페이지를 우회하지 않으며 리다이렉트도 매번 검증한다."""
    headers = {"User-Agent": "YouthPolicyLab/1.0"}
    session = requests.Session()
    cfg = settings()
    deadline = time.monotonic() + cfg.get("document_timeout", 15)
    for _ in range(4):
        if not public_url(url):
            raise ValueError("공개 HTTP(S) 문서 주소만 수집할 수 있습니다.")
        origin = urlparse(url)
        robot_url = f"{origin.scheme}://{origin.netloc}/robots.txt"
        robot_response = request(session, "get", robot_url, headers=headers, deadline=deadline,
                                 timeout=(3, 5), allow_redirects=False)
        if robot_response.status_code == 200:
            robot = RobotFileParser()
            robot.parse(robot_response.text.splitlines())
            if not robot.can_fetch(headers["User-Agent"], url):
                raise ValueError("해당 출처의 자동 수집 규칙에 따라 수집하지 않았습니다.")
        elif robot_response.status_code not in (404, 410):
            raise ValueError("출처의 수집 규칙을 확인할 수 없습니다.")
        with request(session, "get", url, headers=headers, deadline=deadline,
                     timeout=(3, 6), allow_redirects=False, stream=True) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                url = urljoin(url, response.headers["Location"])
                continue
            if response.status_code != 200:
                raise ValueError("원문 페이지를 가져올 수 없습니다.")
            pieces, size = [], 0
            for piece in response.iter_content(65536):
                if time.monotonic() >= deadline:
                    raise ValueError("본문 다운로드가 제한 시간을 초과했습니다.")
                size += len(piece)
                if size > 8_000_000:
                    raise ValueError("8MB를 넘는 문서는 수동 자료 등록을 사용하세요.")
                pieces.append(piece)
            data = b"".join(pieces)
            content_type = response.headers.get("Content-Type", "")
            if "pdf" in content_type or data.startswith(b"%PDF"):
                pages = extract_pdf(data, timeout=cfg.get("pdf_parse_timeout", 5),
                                    max_pages=cfg.get("pdf_max_pages", 80))
                return "공식 PDF", pages
            if "html" not in content_type:
                raise ValueError("현재 자동 수집은 HTML과 텍스트 PDF를 지원합니다.")
            soup = BeautifulSoup(data, "html.parser")
            for el in soup.select("script,style,nav,header,footer,form,noscript"):
                el.decompose()
            body = soup.select_one("article") or soup.select_one("main") or soup.body or soup
            text = body.get_text("\n", strip=True)
            if len(text) < (1 if allow_short else 100):
                raise ValueError("본문이 짧거나 동적으로 표시되어 수집하지 못했습니다.")
            return "공식 웹문서", [{"page": 0, "text": text if allow_short else text[:80000]}]
    raise ValueError("리다이렉트가 너무 많습니다.")


class Collector:
    def __init__(self, store, model=None, searcher=None):
        self.store, self.model = store, model
        self.searcher = searcher or WebSearch()

    def collect(self, policy, question, history=None, progress=lambda msg: None):
        pid = str(policy["plcyNo"])
        cfg = settings()
        # 프롬프트 변경 시 과거 검색 계획 대신 새 지시로 검색어를 생성한다.
        history = recent_history(history)
        plan_key = fingerprint(SEARCH + pid + question + json.dumps(history, ensure_ascii=False))
        plan_file = DATA / "query_plans" / (plan_key + ".json")
        progress("질문에서 웹 검색어를 추출하고 있습니다.")
        if plan_file.exists():
            result = json.loads(plan_file.read_text(encoding="utf-8"))
        else:
            result = ask_json(SEARCH, {"policy": policy["plcyNm"], "question": question,
                                      "history": (history or [])[-6:]}, self.model)
            write_json(plan_file, result)
        queries = [q.strip()[:200] for q in result.get("queries", [])
                   if isinstance(q, str) and q.strip()]
        queries = list(dict.fromkeys(queries))[:cfg["max_queries"]]

        # 선택 정책은 이미 RAG 1에 색인되어 있으므로 재호출·중복 색인하지 않는다.
        # 참고 URL도 현재 정책 원본에서 읽는다.
        own = policy
        candidates, cache_hits, warnings = {}, 0, []
        # API가 제공한 참고 주소는 검색 결과에 다시 등장하지 않아도 우선 수집한다.
        references = []
        seen_urls = set()
        for field in ("refUrlAddr1", "refUrlAddr2"):
            for url in re.findall(r'https?://[^\s<>"\x27]+', str(own.get(field) or "")):
                url = url.rstrip(",;)")
                parts = urlparse(url)
                if not parts.hostname or parts.username or parts.password or url in seen_urls:
                    continue
                seen_urls.add(url)
                item = {"id": "web-" + fingerprint(url)[:20],
                        "title": own.get("plcyNm", policy["plcyNm"]) + " · API 참고 사이트",
                        "url": url, "selected": True, "mandatory": False,
                        "priority": True, "origin": field,
                        "reason": "선택 정책의 API가 제공한 참고 URL이므로 우선 수집합니다.",
                        "applicability": "선택 정책의 API 참고 링크 (본문의 실제 적용 범위 확인 필요)",
                        "status": "pending"}
                progress("API 참고 사이트 본문을 우선 수집하고 있습니다.")
                try:
                    self.index_url(policy, url, item["title"], question,
                                   applicability=item["applicability"], selection_reason=item["reason"])
                    item["status"] = "indexed"
                except (ValueError, requests.RequestException, RuntimeError, OSError):
                    item["status"] = "fetch_failed"
                    warnings.append(f"API 참고 사이트 본문을 수집하지 못했습니다: {url}")
                references.append(item)
        search_config = json.dumps({"domains": cfg.get("web_domains", []),
                                    "limit": cfg.get("web_max_results", 10)}, sort_keys=True)
        for query in queries:
            path = DATA / "web_search_cache" / (fingerprint("tavily:" + search_config + query) + ".json")
            if path.exists():
                found = json.loads(path.read_text(encoding="utf-8"))
                cache_hits += 1
            elif not self.searcher.available():
                warnings.append("TAVILY_API_KEY가 없어 새 웹 검색을 생략했습니다. 정책 기본정보와 기존 근거만 사용합니다.")
                continue
            else:
                progress(f"외부 문서 웹 검색: {query}")
                try:
                    found = self.searcher.search(query)
                except RuntimeError as exc:
                    warnings.append(str(exc))
                    break
                write_json(path, found)
            for item in found:
                url = item.get("url", "").strip()
                if url in seen_urls:
                    continue
                parts = urlparse(url)
                if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
                    continue
                doc_id = "web-" + fingerprint(url)[:20]
                candidates.setdefault(doc_id, {"id": doc_id, "title": item.get("title", url),
                    "summary": item.get("summary", "")[:1000], "url": url})
        preview = list(candidates.values())[:cfg["max_candidates"]]
        chosen, own_chosen, related_chosen, selection = [], [], [], {}
        selection_cache_hit = False
        if preview:
            progress("웹 문서 후보의 관련성을 검토하고 있습니다.")
            payload = {"policy": policy["plcyNm"], "question": question,
                                          "policy_context": {k: policy.get(k, "") for k in
                                              ("plcyNo", "sprvsnInstCdNm", "bizPrdBgngYmd", "bizPrdEndYmd")},
                       "candidates": preview, "max_selected": cfg["max_selected"]}
            selection_file = DATA / "selection_cache" / (fingerprint(SELECT + json.dumps(payload, sort_keys=True, ensure_ascii=False)) + ".json")
            if selection_file.exists():
                selection = json.loads(selection_file.read_text(encoding="utf-8"))
                selection_cache_hit = True
            else:
                selection = ask_json(SELECT, payload, self.model)
                write_json(selection_file, selection)
            allowed = {c["id"] for c in preview}
            own_chosen = list(dict.fromkeys(str(i) for i in selection.get("own_policy_ids", [])
                                            if str(i) in allowed))
            # 자체 문서로 판정했으나 selected_ids에서 누락한 경우에도 보존한다.
            for decision in selection.get("decisions", []):
                if (isinstance(decision, dict) and decision.get("is_policy_document") is True
                        and str(decision.get("id")) in allowed and str(decision["id"]) not in own_chosen):
                    own_chosen.append(str(decision["id"]))
            related_chosen = list(dict.fromkeys(str(i) for i in selection.get("selected_ids", [])
                                                if str(i) in allowed and str(i) not in own_chosen))[:cfg["max_selected"]]
            chosen = own_chosen + related_chosen
        decisions = {}
        for decision in selection.get("decisions", []):
            if isinstance(decision, dict) and str(decision.get("id")) in candidates:
                decisions.setdefault(str(decision["id"]), decision)
        own_id = pid
        selection_details = [{"id": own_id, "title": own.get("plcyNm", policy["plcyNm"]),
                              "url": policy_url(own), "selected": True, "mandatory": True,
                              "reason": "이미 색인된 RAG 1의 선택 정책 기본정보를 필수 근거로 재사용합니다.",
                              "collection": "metadata", "applicability": "현재 선택 정책",
                              "status": "reused"}]
        for candidate in preview:
            decision = decisions.get(candidate["id"], {})
            candidate.update({"selected": candidate["id"] in chosen,
                              "reason": str(decision.get("reason") or "LLM이 개별 이유를 반환하지 않았습니다."),
                              "applicability": str(decision.get("applicability") or "원문 적용 범위 확인 필요")})
            candidate["is_policy_document"] = candidate["id"] in own_chosen
            selection_details.append({**candidate, "mandatory": candidate["id"] in own_chosen,
                                      "status": "pending" if candidate["selected"] else "rejected"})
        titles, indexed, failed = [], [], []
        selection_details.extend(references)
        for reference in references:
            if reference["status"] == "indexed":
                indexed.append(reference["id"])
                titles.append(reference["title"])
            else:
                failed.append(reference["id"])
        for doc_id in chosen:
            item = candidates[doc_id]
            progress(f"선별한 문서 본문 수집: {item['title']}")
            try:
                self.index_url(policy, item["url"], item["title"], question,
                               applicability=item["applicability"], selection_reason=item["reason"],
                               mandatory=doc_id in own_chosen)
            except (ValueError, requests.RequestException, RuntimeError, OSError):
                failed.append(doc_id)
                warnings.append(f"본문을 수집하지 못한 문서: {item['title']}")
                continue
            indexed.append(doc_id)
            titles.append(item["title"])
        for detail in selection_details:
            if detail["id"] in failed:
                detail["status"] = "fetch_failed"
            elif detail["id"] in indexed:
                detail["status"] = "indexed"
        warnings = list(dict.fromkeys(warnings))
        for warning in warnings:
            progress(warning)
        trace = {"queries": queries, "candidates": preview,
                 "reference_sites": references,
                 "selected": [r["id"] for r in references] + chosen,
                 "own_policy_documents": own_chosen, "related_documents": related_chosen,
                 "reused_metadata": {"id": own_id, "collection": "metadata"},
                 "selection_details": selection_details,
                 "indexed": indexed, "failed": failed,
                 "rejected": [c["id"] for c in preview if c["id"] not in chosen],
                 "reason": str(selection.get("reason", "")), "cache_hits": cache_hits,
                 "selection_cache_hit": selection_cache_hit,
                 "titles": titles, "warnings": warnings}
        write_json(DATA / "traces" / (fingerprint(pid + question) + ".json"), trace)
        return trace

    def add_source(self, policy, url):
        return self.index_url(policy, url, policy["plcyNm"], "사용자 확인 원문")

    def index_url(self, policy, url, title, query, applicability="현재 선택 정책 (사용자 연결)", selection_reason="사용자가 추가한 공식 근거", mandatory=False):
        cache = DATA / "web_cache" / (fingerprint(url) + ".json")
        failure_file = DATA / "web_failures" / (fingerprint(url) + ".json")
        if cache.exists():
            content = json.loads(cache.read_text(encoding="utf-8"))
        else:
            if failure_file.exists():
                failure = json.loads(failure_file.read_text(encoding="utf-8"))
                if failure.get("retry_after", 0) > time.time():
                    raise ValueError("최근 수집에 실패한 URL입니다. 잠시 후 다시 시도합니다.")
            try:
                kind, pages = fetch_public(url, allow_short=True) if mandatory else fetch_public(url)
            except (ValueError, requests.RequestException, RuntimeError, OSError):
                write_json(failure_file, {"retry_after": time.time() + settings().get("failed_url_cache_seconds", 300)})
                raise
            content = {"kind": kind, "pages": pages, "collected_at": now()}
            write_json(cache, content)
        if not content["pages"]:
            raise ValueError("추출 가능한 본문이 없습니다.")
        documents = []
        for page in content["pages"]:
            documents.append({"policy_id": str(policy["plcyNo"]),
                "doc_id": fingerprint(url)[:20] + f"-p{page['page']}",
                "title": title + (f" · {page['page']}쪽" if page["page"] else " · 추가 안내"),
                "source": url + (f"#page={page['page']}" if page["page"] else ""), "source_type": content["kind"],
                "text": page["text"], "query": query, "collected_at": content["collected_at"],
                "document_role": "selected_policy" if mandatory else "related",
                "applicability": applicability, "selection_reason": selection_reason})
        self.store.index_documents(documents)
        return len(content["pages"])
