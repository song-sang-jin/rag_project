"""PDF F2001~F2012 검증 중 누락됐던 경계/화면 동작 회귀 테스트."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from streamlit.testing.v1 import AppTest
from youth_rag.service import PolicyService
from youth_rag.store import Store
from test_policy_service import Embedding, policy


def test_no_evidence_does_not_call_llm():
    class Chat:
        def invoke(self, messages):
            raise AssertionError("근거 없이 LLM을 호출하면 안 됨")
    store = SimpleNamespace(retrieve=lambda *args: [])
    collector = SimpleNamespace(collect=lambda *args: {})
    result = PolicyService(store, Chat(), collector).answer(policy(), "알려줘")
    assert "문서에서 확인되지 않습니다" in result["answer"]
    assert result["sources"] == []


def test_configurable_chunking_top_k_and_separate_collections(tmp_path, monkeypatch):
    import youth_rag.store as module
    cfg = {**module.settings(), "chunk_size": 100, "chunk_overlap": 20}
    monkeypatch.setattr(module, "settings", lambda: cfg)
    store = Store(tmp_path / "db", Embedding())
    store.index_policies([policy()])
    document = {"policy_id": "one", "doc_id": "notice", "title": "공고",
                "source": "https://example.org", "source_type": "외부 문서",
                "text": "주거 안내와 신청 서류입니다. " * 100}
    store.index_document(document)
    small = store.docs.count()
    assert small > 1 and store.meta.count() == 1
    rows = store.docs.get(include=["documents", "metadatas"])
    assert all(len(text) <= 100 for text in rows["documents"])
    assert all({"title", "source", "policy_id"} <= m.keys() for m in rows["metadatas"])
    assert len(store.retrieve("one", "서류", 2)) == 2
    cfg.update(chunk_size=500, chunk_overlap=50)
    store.index_document(document)
    assert store.docs.count() < small


@pytest.fixture
def ui(monkeypatch):
    import streamlit as st
    import youth_rag.service as service_module
    import youth_rag.data as data_module
    import youth_rag.config as config_module
    st.cache_resource.clear()
    policies = [policy(), policy("two")]
    calls = []
    store = SimpleNamespace(meta=SimpleNamespace(count=lambda: 2), docs=SimpleNamespace(count=lambda: 0),
                            one_policy=lambda pid: {"id": pid})
    def answer(p, question, history, *args):
        calls.append((p["plcyNo"], question, history))
        args[-1]("수집 중")
        return {"answer": "응답:" + p["plcyNo"], "sources": [], "trace": {"cache_hits": 0}}
    svc = SimpleNamespace(store=store, answer=answer,
                          explore=lambda *args: [{"policy": policies[1], "reason": "조건 일치"}])
    monkeypatch.setattr(service_module, "PolicyService", lambda: svc)
    monkeypatch.setattr(data_module, "load_policies", lambda: policies)
    monkeypatch.setattr(config_module, "youth_key", lambda: "test-key")
    monkeypatch.setattr(config_module, "llm_credentials", lambda: ("test-key", None))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
    yield app, svc, calls
    st.cache_resource.clear()


def test_policy_switch_keeps_separate_conversations(ui):
    app, svc, calls = ui
    app.chat_input[0].set_value("첫 정책 질문").run()
    app.selectbox(key="selected_policy").set_value("two").run()
    assert not any(m.value == "응답:one" for m in app.markdown)
    app.chat_input[0].set_value("다른 정책 질문").run()
    assert calls[-1][2] == []
    app.selectbox(key="selected_policy").set_value("one").run()
    assert any(m.value == "응답:one" for m in app.markdown)
    assert len(app.session_state["histories"]["one"]) == 2
    assert len(app.session_state["histories"]["two"]) == 2
    assert not app.exception


def test_checklist_and_uncited_notice(ui):
    app, svc, calls = ui
    next(b for b in app.button if b.label == "신청 준비 질문하기").click().run()
    assert "제출서류" in calls[-1][1] and "근거" in calls[-1][1]
    assert any("인용된 근거가 없습니다" in c.value for c in app.caption)
    assert not app.exception


def test_explore_selection_opens_policy_conversation(ui):
    app, svc, calls = ui
    app.text_area[0].set_value("주거 지원")
    next(b for b in app.button if b.label == "관련 정책 찾기").click().run()
    app.button(key="pick_two").click().run()
    assert app.session_state["selected_policy"] == "two"
    assert any(s.value == "주거 지원 two" for s in app.subheader)
    assert not app.exception


def test_missing_index_and_api_key_notices(ui, monkeypatch):
    import youth_rag.config as config_module
    app, svc, calls = ui
    svc.store.one_policy = lambda pid: None
    app.chat_input[0].set_value("질문").run()
    assert any("먼저 색인" in w.value for w in app.warning)
    svc.store.one_policy = lambda pid: {"id": pid}
    monkeypatch.setattr(config_module, "youth_key", lambda: "")
    def missing():
        raise ValueError("설정 없음")
    monkeypatch.setattr(config_module, "llm_credentials", missing)
    app.chat_input[0].set_value("질문").run()
    assert any("YOUTH_API_KEY" in w.value for w in app.warning)
    assert any("GMS_KEY" in w.value for w in app.warning)
    # 저장된 정책에 질문할 때에는 정책 API 키가 없어도 다시 호출하지 않는다.
    assert len(calls) == 1 and not app.exception
