"""실행: .venv/Scripts/python -m streamlit run app.py"""
import json
import time
import streamlit as st
from youth_rag.config import DATA, youth_key, llm_credentials, settings
from youth_rag.api import collect_all
from youth_rag.data import load_policies, policy_text, policy_url, validate, policy_categories
from youth_rag.service import PolicyService

st.set_page_config(page_title="청년이음 · 광주 정책 길잡이", page_icon="🌱", layout="wide")


@st.cache_resource
def service():
    return PolicyService()


def choose(pid):
    st.session_state["selected_policy"] = pid
    st.session_state["title_filter"] = ""


def show_sources(sources):
    if not sources:
        st.caption("이 답변에 인용된 근거가 없습니다.")
    for source in sources:
        meta = source["meta"]
        with st.expander(f"[근거 {source['number']}] {meta['title']} · {meta['source_type']}"):
            st.write(source["text"])
            url = meta.get("source", "")
            if url.startswith(("https://", "http://")):
                st.link_button("원문 확인", url)
            else:
                st.caption(url)
            st.caption(f"수집: {meta.get('collected_at', '미기재')}")


def main():
    st.title("🌱 청년이음")
    st.write("광주 청년을 위한 지역·중앙부처 정책 길잡이")
    st.caption("주거부터 취업까지, 내 상황에 맞는 정책을 찾고 근거를 확인하세요.")
    st.info("정책 안내는 최종 자격 판정이나 금융·법률 전문가의 조언을 대신하지 않습니다. 신청 전 해당 연도·모집 차수의 공식 공고와 담당 기관을 확인하세요.")
    try:
        svc = service()
    except Exception:
        st.error("저장소를 열 수 없습니다. requirements.txt 설치와 데이터 경로를 확인하세요.")
        return
    policies = load_policies()
    cfg = settings()
    with st.sidebar:
        st.header("정책 둘러보기")
        st.metric("정책 기본정보 색인", svc.store.meta.count())
        st.metric("상세 근거 청크", svc.store.docs.count())
        categories = ["전체"] + sorted({c for p in policies for c in policy_categories(p)})
        category = st.selectbox("관심 분야", categories)
        group = st.selectbox("정책 제공기관", ["전체", "0054001", "0054002"],
                             format_func=lambda x: {"전체": "전체", "0054001": "중앙부처", "0054002": "광주 지역"}[x])
        title_filter = st.text_input("정책명 검색", placeholder="월세, 취업, 교육", key="title_filter")
        filtered = [p for p in policies if (category == "전체" or category in policy_categories(p))
                    and (group == "전체" or p.get("pvsnInstGroupCd") == group)
                    and title_filter.casefold() in p.get("plcyNm", "").casefold()]
        by_id = {str(p["plcyNo"]): p for p in policies}
        options = [str(p["plcyNo"]) for p in filtered]
        if options:
            if st.session_state.get("selected_policy") not in options:
                st.session_state["selected_policy"] = options[0]
            st.selectbox("질문할 정책", options, key="selected_policy", format_func=lambda i: f"{by_id[i]['plcyNm']} · {i[-6:]}")
        else:
            st.caption("조건에 맞는 정책이 없습니다.")
        with st.expander("검색 설정"):
            top_k = st.slider("전체 근거 수 (Top-k)", 2, 12, cfg["top_k"])
            method = st.selectbox("근거 검색 방식", ["similarity", "mmr"],
                                   format_func=lambda x: "의미 유사도" if x == "similarity" else "MMR · 다양한 근거")
            st.caption("청크 크기·중복 길이는 policy_config.json에서 변경 후 재색인합니다.")
    explore_tab, chat_tab, manage_tab = st.tabs(["내 조건으로 찾기", "정책에 질문하기", "데이터 준비"])
    with manage_tab:
        st.subheader("데이터 수집과 색인")
        st.write("온통청년에서 전체 목록을 받은 뒤 중앙부처와 광주 거주지역코드의 정책을 선별합니다.")
        if not youth_key():
            st.warning(".env에 API_KEY 또는 YOUTH_API_KEY를 설정하세요.")
        try:
            llm_credentials()
            model_ready = True
        except ValueError:
            model_ready = False
            st.warning(".env에 GMS_KEY 또는 OPENAI_API_KEY를 설정하세요.")
        refresh = st.checkbox("API를 다시 조회해 최신 데이터로 갱신", value=False)
        if st.button("1. 정책 데이터 수집", disabled=not bool(youth_key())):
            with st.status("정책을 수집하고 있습니다.", expanded=True) as status:
                progress = st.empty()
                try:
                    _, report = collect_all(progress.write, refresh=refresh)
                    status.update(label=f"수집 완료 · 대상 정책 {report['count']:,}건", state="complete")
                    st.rerun()
                except Exception:
                    status.update(label="수집을 완료하지 못했습니다. 저장된 페이지는 다음 시도에 재사용합니다.", state="error")
        if st.button("2. 정책 기본정보 색인", disabled=not policies or not model_ready):
            with st.status("정책 텍스트를 임베딩하고 있습니다.") as status:
                progress = st.empty()
                try:
                    svc.store.index_policies(policies, progress.write)
                    status.update(label="색인 완료", state="complete")
                    st.rerun()
                except Exception:
                    status.update(label="색인 실패 · 모델 접근 권한과 네트워크를 확인하세요.", state="error")
        if policies:
            report = validate(policies)
            if report["count"] < 100:
                st.warning("현재 정책이 100건 미만입니다. 명세서의 데이터 확보 조건을 확인하세요.")
            st.json(report)
            st.download_button("정책 원본 JSON 다운로드", json.dumps(policies, ensure_ascii=False, indent=2), "policies.json", "application/json")
            st.dataframe([{k: p.get(k, "") for k in ["plcyNo", "plcyNm", "lclsfNm", "zipCd", "sprvsnInstCdNm", "aplyYmd"]} for p in policies], hide_index=True)
        st.caption("출처: 한국고용정보원 온통청년. 데이터 출처와 이용 조건은 README.md를 확인하세요.")
    with explore_tab:
        st.subheader("어떤 지원이 필요한가요?")
        with st.form("explore"):
            condition = st.text_area("내 상황과 원하는 조건", placeholder="광주에서 자취 중인 취업준비생인데 주거 지원을 찾고 있어요.")
            submitted = st.form_submit_button("관련 정책 찾기")
        if submitted:
            if not svc.store.meta.count():
                st.warning("데이터 준비 탭에서 수집과 색인을 먼저 진행하세요.")
            elif condition.strip():
                try:
                    with st.spinner("관련 정책을 찾고 조건을 확인하고 있습니다."):
                        st.session_state["matches"] = svc.explore(condition, policies, category, group)
                except Exception:
                    st.error("조건 검색을 완료하지 못했습니다. 모델 설정과 네트워크를 확인하세요.")
        matches = st.session_state.get("matches")
        if matches == []:
            st.info("수집한 정책 중 입력 조건을 근거로 확인할 수 있는 항목을 찾지 못했습니다. 조건을 바꿔보세요.")
        for match in matches or []:
            p = match["policy"]
            with st.container(border=True):
                st.markdown(f"### {p['plcyNm']}")
                st.caption(f"{' · '.join(policy_categories(p))} · {p.get('sprvsnInstCdNm', '')}")
                st.write(match["reason"])
                st.caption("검색 후보입니다. 추가 자격·제외 조건은 정책에 질문해 확인하세요.")
                st.button("이 정책에 질문하기", key="pick_"+p["plcyNo"], on_click=choose, args=(p["plcyNo"],))
    with chat_tab:
        if not options:
            st.info("정책을 수집하거나 왼쪽 검색 조건을 변경하세요.")
            return
        policy = by_id[st.session_state["selected_policy"]]
        pid = str(policy["plcyNo"])
        st.subheader(policy["plcyNm"])
        st.write(policy.get("plcyExplnCn") or "상세 안내를 확인하세요.")
        a, b = st.columns(2)
        a.metric("신청 기간 · 원본", policy.get("aplyYmd", "").strip() or "미기재 · 확인 필요")
        b.caption(f"주관: {policy.get('sprvsnInstCdNm') or '미기재'}\n\n최종수정: {policy.get('lastMdfcnDt') or '미기재'}")
        with st.expander("정책 기본 정보와 원문"):
            st.text(policy_text(policy))
            st.link_button("온통청년 정책 상세", policy_url(policy))
            for field in ["aplyUrlAddr", "refUrlAddr1", "refUrlAddr2"]:
                url = str(policy.get(field, "")).strip()
                if url.startswith(("https://", "http://")):
                    st.link_button({"aplyUrlAddr": "신청 페이지", "refUrlAddr1": "참고 자료 1", "refUrlAddr2": "참고 자료 2"}[field], url)
        histories = st.session_state.setdefault("histories", {})
        messages = histories.setdefault(pid, [])
        c1, c2 = st.columns(2)
        checklist = c1.button("신청 준비 질문하기")
        if c2.button("이 정책 대화 지우기"):
            histories[pid] = []
            st.rerun()
        for message in messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                if message["role"] == "assistant":
                    show_sources(message.get("sources", []))
        question = st.chat_input("자격 조건, 중복 참여, 준비서류를 물어보세요.")
        if checklist:
            question = "이 정책의 신청 준비 체크리스트를 만들어줘. 제출서류, 추가 확인할 자격과 제외 조건, 신청 순서를 근거와 함께 정리해줘."
        if question:
            if svc.store.one_policy(pid) is None:
                st.warning("데이터 준비 탭에서 정책 기본정보를 먼저 색인하세요.")
            else:
                with st.chat_message("user"):
                    st.write(question)
                with st.chat_message("assistant"):
                    result = None
                    start = time.monotonic()
                    with st.status("정책 근거를 확인하고 있습니다.", expanded=True) as status:
                        progress = st.empty()
                        try:
                            result = svc.answer(policy, question, [{"role": m["role"], "content": m["content"]} for m in messages],
                                                top_k, method, progress.write)
                            progress.empty()
                            status.update(label="답변 완료", state="complete", expanded=False)
                        except Exception:
                            progress.empty()
                            status.update(label="답변 실패 · API·모델 설정과 네트워크를 확인하고 다시 시도하세요.", state="error", expanded=True)
                    # 답변은 접히는 진행 상태 상자 밖에 표시한다.
                    if result is not None:
                        st.markdown(result["answer"])
                        show_sources(result["sources"])
                        for warning in result["trace"].get("warnings", []):
                            st.warning(warning)
                        st.caption(f"처리 {time.monotonic()-start:.1f}초 · 검색어 캐시 {result['trace']['cache_hits']}건")
                        messages.extend([{"role": "user", "content": question},
                                         {"role": "assistant", "content": result["answer"], "sources": result["sources"]}])


if __name__ == "__main__":
    main()
