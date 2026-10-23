from streamlit.testing.v1 import AppTest
from pathlib import Path
from types import SimpleNamespace


def test_app_loads_without_network():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert app.title[0].value == "🌱 청년이음"
    assert len(app.tabs) == 3


def test_answer_visible_and_progress_cleared_after_completion(monkeypatch):
    import streamlit as st
    import youth_rag.service as service_module
    import youth_rag.data as data_module
    import youth_rag.config as config_module
    st.cache_resource.clear()
    p = {"plcyNo": "ui-test", "plcyNm": "테스트 정책"}
    store = SimpleNamespace(meta=SimpleNamespace(count=lambda: 1), docs=SimpleNamespace(count=lambda: 0),
                            one_policy=lambda pid: {"id": pid})
    def answer(*args):
        args[-1]("근거를 바탕으로 답변을 작성하고 있습니다.")
        return {"answer": "완성된 답변은 펼치지 않아도 보입니다.", "sources": [],
                "trace": {"cache_hits": 0, "warnings": []}}
    monkeypatch.setattr(service_module, "PolicyService", lambda: SimpleNamespace(store=store, answer=answer))
    monkeypatch.setattr(data_module, "load_policies", lambda: [p])
    monkeypatch.setattr(config_module, "youth_key", lambda: "test-key")
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30).run()
    app.chat_input[0].set_value("지원 내용은?").run()
    assert not app.exception
    assert any(m.value == "완성된 답변은 펼치지 않아도 보입니다." for m in app.markdown)
    assert not any("답변을 작성하고 있습니다" in m.value for m in app.markdown)
    statuses = app.get("status")
    assert statuses[0].proto.label == "답변 완료"
    assert statuses[0].proto.expanded is False
    # 답변을 담은 markdown이 접히는 status 내부에 들어 있지 않아야 한다.
    assert "완성된 답변" not in str(statuses[0])
    st.cache_resource.clear()
