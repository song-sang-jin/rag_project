import json
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from .config import llm_credentials, settings


def recent_history(history, limit=6):
    """대화 원본은 UI에 유지하고 모델에는 질문 이해에 필요한 최근 맥락만 전달한다."""
    return [{"role": m.get("role", "user"), "content": str(m.get("content", ""))[:500]}
            for m in (history or [])[-limit:]]


def chat(max_output_tokens=None):
    key, base = llm_credentials()
    cfg = settings()
    kwargs = {"max_tokens": max_output_tokens} if max_output_tokens is not None else {}
    return ChatOpenAI(model=cfg["chat_model"], api_key=key, base_url=base,
                      timeout=cfg.get("llm_timeout", 30), max_retries=cfg.get("llm_retries", 1), **kwargs)


def embeddings():
    key, base = llm_credentials()
    return OpenAIEmbeddings(model=settings()["embedding_model"], api_key=key, base_url=base,
                            check_embedding_ctx_length=False, chunk_size=32, request_timeout=20, max_retries=1)


def ask_json(system, payload, model=None):
    response = (model or chat()).invoke([("system", system), ("human", json.dumps(payload, ensure_ascii=False))])
    text = response.content
    if not isinstance(text, str):
        raise ValueError("모델 응답 형식을 확인할 수 없습니다.")
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(text)
