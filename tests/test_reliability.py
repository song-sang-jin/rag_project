import io
import json
from types import SimpleNamespace
import pytest
import requests
from pypdf import PdfWriter
from youth_rag.grounding import requested_scopes
from youth_rag.service import PolicyService
from youth_rag.pdf_reader import extract_pdf


def test_year_and_round_guards_reject_unmatched_sources():
    p = {"plcyNo": "one", "plcyNm": "(1차) 2026 광주 청년 구직활동수당"}
    evidence = [{"id":"one", "text":"제출서류: 등본", "meta":{"title":p["plcyNm"],"source_type":"항목 메타데이터"}}]
    class Model:
        calls = 0
        def invoke(self, messages):
            self.calls += 1
            return SimpleNamespace(content="2025년 3차 서류는 등본입니다 [근거 1]")
    model = Model()
    store = SimpleNamespace(retrieve=lambda *a: evidence)
    collector = SimpleNamespace(collect=lambda *a: {})
    svc = PolicyService(store, model, collector)
    result = svc.answer(p,"2025년 3차 공고의 서류는?")
    assert model.calls == 0
    assert "확인되지 않습니다" in result["answer"] and result["sources"] == []
    evidence.append({"id":"old","text":"2025년 신청서 제출", "meta":{"title":"(3차) 2025 광주 청년 구직활동수당", "source_type":"공식 웹문서"}})
    result = svc.answer(p,"2025년 3차 공고의 서류는?")
    assert model.calls == 1
    assert "확인되지 않습니다" in result["answer"] and result["sources"] == []
    model.invoke = lambda messages: SimpleNamespace(content="2025년 3차 공고에는 신청서 제출이 명시됩니다 [근거 2]")
    result = svc.answer(p,"2025년 3차 공고의 서류는?")
    assert result["sources"][0]["id"] == "old"


def test_scope_does_not_confuse_document_issue_month_with_policy_year():
    assert requested_scopes("건강보험료 2025년 11월 납부확인서가 필요한가요?", "2026 정책") == []
    assert requested_scopes("2025년 3차 공고를 알려줘", "2026 정책") == [("2025", "3")]
    assert requested_scopes("2차 모집 안내는?", "2026 정책") == [("2026", "2")]


def test_http_retries_transient_errors_with_spacing_and_masks_connection_errors(monkeypatch):
    import youth_rag.http as module
    sleeps, calls = [], []
    monkeypatch.setattr(module, "settings", lambda:{"request_interval":1,"external_retries":1,"retry_max_wait":2})
    monkeypatch.setattr(module.time, "sleep", lambda n:sleeps.append(n))
    monkeypatch.setattr(module.time, "monotonic", lambda:100)
    module._last_request.clear()
    statuses = iter([429,200])
    class Session:
        def get(self,url,**kwargs):
            calls.append(url)
            return SimpleNamespace(status_code=next(statuses),headers={"Retry-After":"1"},close=lambda:None)
    assert module.request(Session(),"get","https://example.org/notice").status_code == 200
    assert len(calls)==2 and sleeps
    class Broken:
        def get(self,url,**kwargs):
            raise requests.Timeout("SECRET_KEY_URL_MUST_NOT_LEAK")
    with pytest.raises(RuntimeError) as exc:
        module.request(Broken(),"get","https://example.org/notice")
    assert "SECRET" not in str(exc.value)


def test_pdf_extraction_is_bounded_and_page_limit_is_enforced():
    writer=PdfWriter()
    writer.add_blank_page(width=100,height=100)
    stream=io.BytesIO()
    writer.write(stream)
    assert extract_pdf(stream.getvalue(),timeout=10,max_pages=1)==[]
    with pytest.raises(ValueError,match="페이지 수"):
        extract_pdf(stream.getvalue(),timeout=10,max_pages=0)


def test_pdf_timeout_terminates_worker(monkeypatch):
    import youth_rag.pdf_reader as module
    terminated=[]
    receiver=SimpleNamespace(poll=lambda timeout:False,close=lambda:None)
    sender=SimpleNamespace(close=lambda:None)
    process=SimpleNamespace(pid=1,start=lambda:None,is_alive=lambda:True,
                            terminate=lambda:terminated.append(True),join=lambda timeout:None)
    context=SimpleNamespace(Pipe=lambda **kwargs:(receiver,sender),Process=lambda **kwargs:process)
    monkeypatch.setattr(module.multiprocessing,"get_context",lambda name:context)
    with pytest.raises(ValueError,match="제한 시간"):
        extract_pdf(b"pdf",timeout=0.01)
    assert terminated==[True]


def test_retry_after_longer_than_budget_is_not_shortened(monkeypatch):
    import youth_rag.http as module
    monkeypatch.setattr(module, "settings", lambda:{"request_interval":0,"external_retries":1,"retry_max_wait":2})
    monkeypatch.setattr(module, "_blocked_until", {})
    response=SimpleNamespace(status_code=429,headers={"Retry-After":"60"})
    calls=[]
    session=SimpleNamespace(get=lambda *a,**k:(calls.append(True) or response))
    assert module.request(session,"get","https://example.org/limited") is response
    assert len(calls)==1
    with pytest.raises(RuntimeError,match="대기 시간"):
        module.request(session,"get","https://example.org/limited")
    assert len(calls)==1


def test_pdf_pages_are_embedded_in_batches_instead_of_one_request_per_page(tmp_path):
    from youth_rag.store import Store
    class Embedder:
        calls = 0
        def embed_documents(self,texts):
            self.calls += 1
            return [[1.0,2.0,3.0] for text in texts]
    embedder=Embedder()
    store=Store(tmp_path/'db',embedder)
    documents=[{'policy_id':'one','doc_id':f'pdf-p{i}','title':f'공고 {i}쪽',
                'source':f'https://example.org/notice.pdf#page={i}','source_type':'공식 PDF',
                'text':'제출서류 안내입니다.'} for i in range(1,51)]
    store.index_documents(documents)
    assert store.docs.count()==50 and embedder.calls==2
    store.index_documents(documents)
    assert store.docs.count()==50 and embedder.calls==2


def test_truncated_model_output_is_not_presented_as_a_complete_answer():
    evidence=[{'id':'one','text':'지원 내용', 'meta':{'title':'정책','source_type':'항목 메타데이터'}}]
    store=SimpleNamespace(retrieve=lambda *args:evidence)
    collector=SimpleNamespace(collect=lambda *args:{})
    model=SimpleNamespace(invoke=lambda *args:SimpleNamespace(content='조건은 무조건 [근거 1]',response_metadata={'finish_reason':'length'}))
    result=PolicyService(store,model,collector).answer({'plcyNo':'one','plcyNm':'정책'},'자격은?')
    assert '길이 제한' in result['answer'] and result['sources']==[]


def test_checklist_is_short_structured_and_only_uses_valid_evidence_numbers():
    evidence=[{'id':'one','text':'제출서류: 등본', 'meta':{'title':'정책','source_type':'항목 메타데이터'}}]
    store=SimpleNamespace(retrieve=lambda *args:evidence)
    collector=SimpleNamespace(collect=lambda *args:{})
    content={'items':[{'label':'서류','text':'등본 준비','citations':[1,1,99]},
                      {'label':'소득','text':'문서에서 확인되지 않습니다','citations':[]},
                      {'label':'임의 조건','text':'무조건 선정','citations':[]}]}
    model=SimpleNamespace(invoke=lambda *args:SimpleNamespace(content=json.dumps(content)))
    result=PolicyService(store,model,collector).answer({'plcyNo':'one','plcyNm':'정책'},'신청 준비 체크리스트')
    assert len(result['answer'].splitlines())==2
    assert result['answer'].count('[근거 1]')==1
    assert '무조건 선정' not in result['answer'] and '[근거 99]' not in result['answer']
    assert len(result['sources'])==1
