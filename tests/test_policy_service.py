import json
import pytest
from types import SimpleNamespace
from pathlib import Path
from youth_rag.data import in_scope, policy_text, policy_categories
from youth_rag.store import Store
from youth_rag.service import PolicyService, cited_sources
from youth_rag.documents import Collector


class Embedding:
    def embed_documents(self, texts):
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text):
        return [float(text.count("주거") + 1), float(text.count("취업") + 1), 1.0]


def policy(pid="one", group="0054002", region="29110"):
    return {"plcyNo": pid, "plcyNm": "주거 지원 " + pid, "plcySprtCn": "주거 지원 조건",
            "pvsnInstGroupCd": group, "zipCd": region, "rgtrHghrkInstCd": "6130000"}


def test_scope_uses_exact_codes_not_institution_name():
    assert in_scope(policy())
    assert in_scope(policy(group="0054001", region=""))
    p = policy(region="12780")
    p["sprvsnInstCdNm"] = "전남광주통합특별시 강진군"
    assert not in_scope(p)
    assert not in_scope(policy(region="129110"))
    other = policy()
    other["rgtrHghrkInstCd"] = "other-city"
    assert not in_scope(other)


def test_empty_fields_are_not_eligibility():
    assert "미기재(확인 필요)" in policy_text(policy())
    assert "재직자" in policy_text({**policy(), "jobCd": "0013001"})


def test_multiple_categories_and_explore_filter():
    p = {**policy(), "lclsfNm": " 일자리,교육,교육, , "}
    assert policy_categories(p) == ["일자리", "교육"]
    assert policy_categories({"lclsfNm": None}) == []
    assert p["lclsfNm"] == " 일자리,교육,교육, , "
    class FakeStore:
        meta = object()
        def search(self, collection, question, k, where):
            assert where == {"policy_id": {"$in": ["one"]}}
            return [{"id": "one", "text": "교육 지원"}]
    class FakeChat:
        def invoke(self, messages):
            return SimpleNamespace(content='{"matches":[{"id":"one","eligible":true,"reason":"교육 지원"}]}')
    svc = PolicyService(store=FakeStore(), model=FakeChat(), collector=object())
    for category in ["일자리", "교육"]:
        assert svc.explore("지원", [p], category)[0]["policy"] == p
    assert svc.explore("지원", [p], "주거") == []


def test_explore_requires_explicit_positive_selection():
    class FakeStore:
        meta = object()
        def search(self, *args):
            return [{"id": p, "text": "지원"} for p in ["one", "two", "three", "four", "five"]]
    class FakeChat:
        def invoke(self, messages):
            return SimpleNamespace(content=json.dumps({"matches": [
                {"id": "one", "eligible": True, "reason": "주거 지원 후보"},
                {"id": "two", "eligible": False, "reason": "조건 불일치로 제외"},
                {"id": "three", "reason": "판단 누락"},
                {"id": "four", "eligible": "true", "reason": "잘못된 형식"},
                {"id": "five", "eligible": True, "reason": "기간 종료로 제외되어야 함"}]}))
    svc = PolicyService(store=FakeStore(), model=FakeChat(), collector=object())
    result = svc.explore("지원", [policy(p) for p in ["one", "two", "three", "four", "five"]])
    assert [r["policy"]["plcyNo"] for r in result] == ["one"]


def test_index_idempotence_policy_filter_and_rechunk(tmp_path):
    store = Store(tmp_path / "db", Embedding())
    store.index_policies([policy(), policy("two")])
    store.index_policies([policy(), policy("two")])
    assert store.meta.count() == 2
    doc = {"policy_id": "one", "doc_id": "shared", "title": "공고", "text": "주거 지원 " * 700,
           "source": "https://example.org/notice", "source_type": "공식 문서"}
    store.index_document(doc)
    first_count = store.docs.count()
    store.index_document(doc)
    assert store.docs.count() == first_count
    store.index_document({**doc, "policy_id": "two"})
    rows = store.retrieve("one", "주거", 6, "mmr")
    assert rows[0]["id"] == "one"
    assert all(row["meta"]["policy_id"] == "one" for row in rows)
    store.index_document({**doc, "text": "주거 지원 한 문장"})
    assert len(store.docs.get(where={"policy_id": "one"})["ids"]) == 1


def test_citations_only_show_real_used_evidence():
    evidence = [{"id": "a"}, {"id": "b"}]
    assert cited_sources("확인 [근거 2] [근거 2] [근거 99]", evidence) == [{"number": 2, "id": "b"}]
    assert cited_sources("확인 불가", evidence) == []


def test_legacy_duplicates_are_excluded_and_cleanup_preserves_external_docs(tmp_path):
    from scripts.cleanup_api_duplicates import cleanup
    store = Store(tmp_path / "db", Embedding())
    p = policy()
    store.index_policies([p])
    own = {"policy_id": "one", "doc_id": "api-one", "title": p["plcyNm"],
           "text": policy_text(p), "source": "https://example.org/own", "source_type": "온통청년 API 상세"}
    store.index_document(own)
    store.index_document({**own, "doc_id": "notice", "text": "추가 공식 안내 주거 서류 " * 20,
                          "source_type": "공식 웹문서"})
    before = store.docs.get()["ids"]
    evidence = store.retrieve("one", "주거", 6)
    assert evidence[0]["id"] == "one"
    assert not any(r["meta"].get("doc_id") == "api-one" for r in evidence)
    assert any(r["meta"].get("doc_id") == "notice" for r in evidence)
    backup = tmp_path / "backup.json"
    assert cleanup(store, backup)["removed"] == 0
    assert store.docs.get()["ids"] == before
    result = cleanup(store, backup, apply=True)
    assert result["removed"] > 0 and store.meta.count() == 1
    saved = json.loads(backup.read_text(encoding="utf-8"))
    assert len(saved["records"]) == result["removed"]
    assert all(r["metadata"]["doc_id"] == "api-one" for r in saved["records"])
    assert all(m["doc_id"] == "notice" for m in store.docs.get(include=["metadatas"])["metadatas"])
    assert cleanup(store, backup, apply=True)["removed"] == 0


def test_graph_always_includes_meta_and_hides_fake_citation(tmp_path):
    store = Store(tmp_path / "db", Embedding())
    p = policy()
    store.index_policies([p])
    class FakeCollector:
        def collect(self, *args):
            return {"cache_hits": 0}
    class FakeChat:
        def invoke(self, *args):
            return SimpleNamespace(content="지원 내용입니다 [근거 1]. 추가내용 [근거 99]")
    svc = PolicyService(store, FakeChat(), FakeCollector())
    graph = svc.graph()
    assert {"retrieve", "generate"}.issubset(graph.get_graph().nodes)
    result = svc.answer(p, "지원 내용")
    assert result["sources"][0]["id"] == "one"
    assert "[근거 99]" not in result["answer"]


def test_collector_reuses_metadata_without_api_call_or_duplicate_index(tmp_path, monkeypatch):
    import youth_rag.documents as module
    monkeypatch.setattr(module, "DATA", tmp_path / "cache")
    p = policy()
    import youth_rag.api as api_module
    monkeypatch.setattr(api_module.YouthAPI, "detail", lambda *a: pytest.fail("상세 API 재호출 금지"))
    class FakeSearch:
        calls = 0
        def available(self):
            return True
        def search(self, q):
            self.calls += 1
            return [{"url": "https://example.org/notice", "title": "다른 연도 공고", "summary": "다른 정책"}]
    class FakeChat:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return SimpleNamespace(content=json.dumps({"queries": ["주거"], "selected_ids": [], "reason": "관련 없음"}))
    store = Store(tmp_path / "db", Embedding())
    store.index_policies([p])
    searcher = FakeSearch()
    model = FakeChat()
    collector = Collector(store, model, searcher)
    trace = collector.collect(p, "질문")
    assert trace["selected"] == []
    assert trace["reused_metadata"]["id"] == "one"
    web_before = searcher.calls
    collector.collect(p, "질문")
    assert searcher.calls == web_before
    assert model.calls == 2  # 검색어 추출 1회 + 후보 선별 1회. 반복 질문은 두 결과 모두 재사용.
    assert store.docs.count() == 0
    assert store.retrieve("one", "질문")[0]["id"] == "one"


def test_web_selection_fetches_body_and_caches_it(tmp_path, monkeypatch):
    import youth_rag.documents as module
    from youth_rag.store import fingerprint
    monkeypatch.setattr(module, "DATA", tmp_path / "cache")
    p = policy()
    url = "https://example.org/notice.pdf"
    doc_id = "web-" + fingerprint(url)[:20]
    fetched = []
    def fetch(url):
        fetched.append(url)
        return "공식 PDF", [{"page": 2, "text": "해당 정책의 제출서류 안내 " * 50}]
    monkeypatch.setattr(module, "fetch_public", fetch)
    class API:
        def detail(self, pid):
            return p
        def search(self, q):
            raise AssertionError("정책명 검색 금지")
    class Search:
        calls = 0
        def available(self):
            return True
        def search(self, query):
            self.calls += 1
            return [{"title": "지원사업 공식 모집 공고", "summary": "제출서류 안내", "url": url}]
    class Chat:
        def invoke(self, messages):
            payload = json.loads(messages[1][1])
            if "candidates" in payload:
                assert payload["candidates"][0]["url"] == url
                return SimpleNamespace(content=json.dumps({"selected_ids": [doc_id, doc_id, "invented"]}))
            return SimpleNamespace(content=json.dumps({"queries": ["주거 제출서류 공고"]}))
    store = Store(tmp_path / "db", Embedding())
    searcher = Search()
    collector = Collector(store, Chat(), searcher)
    trace = collector.collect(p, "서류는?")
    assert trace["queries"] == ["주거 제출서류 공고"]
    assert searcher.calls == 1
    assert trace["selected"] == [doc_id]
    assert trace["indexed"] == [doc_id]
    assert fetched == [url]
    count, calls = store.docs.count(), searcher.calls
    collector.collect(p, "서류는?")
    assert store.docs.count() == count
    assert searcher.calls == calls
    assert fetched == [url]
    rows = store.docs.get(include=["metadatas"])
    assert any(m["source"] == url + "#page=2" and m["policy_id"] == "one" for m in rows["metadatas"])


def test_missing_web_key_keeps_api_evidence_and_reports_warning(tmp_path, monkeypatch):
    import youth_rag.documents as module
    monkeypatch.setattr(module, "DATA", tmp_path / "cache")
    class API:
        def detail(self, pid):
            return policy()
    class Search:
        def available(self):
            return False
        def search(self, query):
            raise AssertionError("키가 없으면 검색하지 않아야 함")
    class Chat:
        def invoke(self, messages):
            return SimpleNamespace(content='{"queries": ["주거 지원 공고"]}')
    store = Store(tmp_path / "db", Embedding())
    trace = Collector(store, Chat(), Search()).collect(policy(), "질문")
    assert trace["warnings"]
    assert trace["indexed"] == []
    assert trace["candidates"] == []


def test_api_reference_sites_are_fetched_before_search_and_cached(tmp_path, monkeypatch):
    import youth_rag.documents as module
    monkeypatch.setattr(module, "DATA", tmp_path / "cache")
    urls = ["https://example.org/reference", "https://example.org/blocked"]
    p = {**policy(), "refUrlAddr1": urls[0], "refUrlAddr2": urls[1]}
    events = []
    class API:
        def detail(self, pid):
            return p
    class Search:
        def available(self):
            return True
        def search(self, query):
            events.append("search")
            return [{"url": urls[0], "title": "重複", "summary": "同一資料"}]
    class Chat:
        def invoke(self, messages):
            return SimpleNamespace(content='{"queries":["공고"]}')
    def fetch(url):
        events.append(url)
        if url == urls[1]:
            raise ValueError("접속 불가")
        return "공식 웹문서", [{"page": 0, "text": "참고 사이트의 실제 본문 " * 50}]
    monkeypatch.setattr(module, "fetch_public", fetch)
    store = Store(tmp_path / "db", Embedding())
    collector = Collector(store, Chat(), Search())
    trace = collector.collect(p, "질문")
    assert events == [*urls, "search"]
    assert [r["status"] for r in trace["reference_sites"]] == ["indexed", "fetch_failed"]
    assert trace["candidates"] == []  # 참고 URL을 웹 후보로 중복 수집하지 않음
    assert trace["warnings"]
    assert len(trace["indexed"]) == 1
    collector.collect(p, "질문")
    assert events.count(urls[0]) == 1
    assert events.count(urls[1]) == 1  # 실패 URL도 짧은 재시도 대기 캐시로 반복 지연 방지.


def test_general_question_keeps_multiple_helpful_sources_and_individual_reasons(tmp_path, monkeypatch):
    import youth_rag.documents as module
    from youth_rag.store import fingerprint
    monkeypatch.setattr(module, "DATA", tmp_path / "cache")
    urls = ["https://example.org/common-faq", "https://example.org/related-rule", "https://example.org/unrelated"]
    ids = ["web-" + fingerprint(url)[:20] for url in urls]
    class API:
        def detail(self, pid):
            return policy()
    class Search:
        def available(self):
            return True
        def search(self, q):
            return [{"title": title, "summary": title, "url": url} for title, url in zip(
                ["공통 신청 안내", "다른 지원사업 중복참여 규정", "무관한 행사"], urls)]
    class Chat:
        def invoke(self, messages):
            payload = json.loads(messages[1][1])
            if "candidates" not in payload:
                return SimpleNamespace(content='{"queries":["신청 자격 안내"]}')
            return SimpleNamespace(content=json.dumps({
                "selected_ids": ids[:2], "reason": "신청 절차와 중복참여를 보완",
                "decisions": [
                    {"id": ids[0], "reason": "공통 신청 절차 설명", "applicability": "공통 안내"},
                    {"id": ids[1], "reason": "중복참여 예외 설명", "applicability": "다른 사업의 규정이며 해당 여부 확인 필요"},
                    {"id": ids[2], "reason": "질문과 무관한 행사", "applicability": "적용 안 됨"}]}))
    monkeypatch.setattr(module, "fetch_public", lambda url: ("공식 웹문서", [{"page": 0, "text": "근거 본문 " * 80}]))
    store = Store(tmp_path / "db", Embedding())
    store.index_policies([policy()])
    trace = Collector(store, Chat(), Search()).collect(policy(), "신청 자격은?")
    assert trace["selected"] == ids[:2]
    assert trace["rejected"] == [ids[2]]
    assert trace["selection_details"][0]["mandatory"] is True
    assert trace["selection_details"][1]["reason"] == "공통 신청 절차 설명"
    assert trace["candidates"][2]["selected"] is False
    assert trace["candidates"][2]["reason"] == "질문과 무관한 행사"
    rows = store.retrieve("one", "신청", 6)
    assert rows[0]["id"] == "one"
    assert not any(row["meta"].get("doc_id") == "api-one" for row in rows)
    assert any(row["meta"].get("applicability") == "공통 안내" for row in rows)


def test_own_policy_documents_bypass_related_limit_and_failures_are_recorded(tmp_path, monkeypatch):
    import youth_rag.documents as module
    from youth_rag.store import fingerprint
    monkeypatch.setattr(module, "DATA", tmp_path / "cache")
    urls = [f"https://example.org/notice-{i}" for i in range(8)]
    ids = ["web-" + fingerprint(url)[:20] for url in urls]
    fetched = []
    class Search:
        def available(self):
            return True
        def search(self, query):
            return [{"url": url, "title": "공고 " + str(i), "summary": "안내"}
                    for i, url in enumerate(urls)]
    class Chat:
        def invoke(self, messages):
            payload = json.loads(messages[1][1])
            if "candidates" not in payload:
                return SimpleNamespace(content='{"queries":["선택 정책 공식 공고"]}')
            return SimpleNamespace(content=json.dumps({
                "own_policy_ids": ids[:3] + [ids[0], "invented"],
                "selected_ids": ids[4:] + [ids[0], "invented"],
                "decisions": [{"id": doc_id, "is_policy_document": i < 4,
                               "reason": "동일 정책의 공고" if i < 4 else "추가 안내",
                               "applicability": "현재 선택 정책" if i < 4 else "공통 안내"}
                              for i, doc_id in enumerate(ids)]}))
    def fetch(url, *, allow_short=False):
        fetched.append((url, allow_short))
        if url == urls[1]:
            raise ValueError("사이트 접근 실패")
        return "공식 웹문서", [{"page": 0, "text": "짧은 공고" if allow_short else "추가 안내 " * 30}]
    monkeypatch.setattr(module, "fetch_public", fetch)
    store = Store(tmp_path / "db", Embedding())
    store.index_policies([policy()])
    trace = Collector(store, Chat(), Search()).collect(policy(), "신청은?")
    assert trace["own_policy_documents"] == ids[:4]
    assert trace["related_documents"] == ids[4:7]
    assert trace["selected"] == ids[:7]
    assert trace["failed"] == [ids[1]]
    assert trace["indexed"] == [doc_id for doc_id in ids[:7] if doc_id != ids[1]]
    assert trace["rejected"] == [ids[7]]
    assert fetched == [(url, i < 4) for i, url in enumerate(urls[:7])]
    own_details = [d for d in trace["selection_details"] if d.get("is_policy_document")]
    assert len(own_details) == 4 and all(d["mandatory"] for d in own_details)
    assert own_details[1]["status"] == "fetch_failed"
    metas = store.docs.get(include=["metadatas"])["metadatas"]
    assert sum(m["document_role"] == "selected_policy" for m in metas) == 3
    assert store.meta.count() == 1


@pytest.mark.parametrize("text", ["짧은 공고", ""])
def test_mandatory_fetch_accepts_short_but_not_empty_body(monkeypatch, text):
    import youth_rag.documents as module
    class Response:
        status_code = 200
        headers = {"Content-Type": "text/html"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def iter_content(self, size):
            yield ("<html><body><main>" + text + "</main></body></html>").encode()
    class Session:
        def get(self, url, **kwargs):
            return SimpleNamespace(status_code=404) if url.endswith("robots.txt") else Response()
    monkeypatch.setattr(module, "public_url", lambda url: True)
    monkeypatch.setattr(module.requests, "Session", Session)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    with pytest.raises(ValueError):
        module.fetch_public("https://example.org/notice")
    if text:
        kind, pages = module.fetch_public("https://example.org/notice", allow_short=True)
        assert pages[0]["text"] == text
    else:
        with pytest.raises(ValueError):
            module.fetch_public("https://example.org/notice", allow_short=True)
