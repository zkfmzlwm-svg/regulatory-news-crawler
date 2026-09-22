import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from src.crawler import crawl_all, load_sources, save_sources
from src.fetcher import fetch_full_text
from src.storage import DEFAULT_DB_PATH, Storage
from src.utils import clean_text
from src.summarizer import (
    SummaryEntry,
    load_format,
    output_extension,
    render_txt,
    summarize_article,
    write_summaries,
)

BASE_DIR = Path(__file__).resolve().parent
SOURCES_CONFIG = BASE_DIR / "config" / "sources.yaml"
FORMAT_CONFIG = BASE_DIR / "config" / "summary_format.yaml"
OUTPUT_DIR = BASE_DIR / "output"

st.set_page_config(page_title="해외 의약품 규제 뉴스 크롤러", layout="wide")


@st.cache_resource
def get_storage() -> Storage:
    return Storage(DEFAULT_DB_PATH)


store = get_storage()

st.title("해외 의약품 규제 뉴스 크롤러")

# ---------------- 사이드바: 수집 / 필터 / 사이트 관리 ----------------
with st.sidebar:
    st.header("1. 기사 수집")
    if st.button("🔄 기사 수집 실행", use_container_width=True):
        sources = load_sources(str(SOURCES_CONFIG))
        with st.spinner("등록된 사이트에서 기사를 가져오는 중..."):
            found = crawl_all(sources)
            added = store.add_articles(found)
        st.success(f"조회 {len(found)}건 · 신규 저장 {added}건")

    st.divider()
    st.header("2. 목록 필터")
    keyword = st.text_input("키워드 검색", placeholder="예: biosimilar")
    all_sources = load_sources(str(SOURCES_CONFIG))
    source_names = [s["name"] for s in all_sources]
    selected_sources = st.multiselect("출처", source_names)
    unchecked_only = st.checkbox("아직 요약 안 한 기사만 보기")

    st.divider()
    st.header("3. 수집 대상 사이트")
    with st.expander(f"등록된 사이트 ({len(all_sources)}개)"):
        for s in all_sources:
            dot = "🟢" if s.get("enabled", True) else "⚪"
            st.caption(f"{dot} [{s.get('tag', '')}] {s['name']}")

    with st.expander("➕ 사이트 추가 (RSS)"):
        with st.form("add_source_form", clear_on_submit=True):
            new_name = st.text_input("사이트 이름")
            new_url = st.text_input("RSS URL")
            new_tag = st.text_input("태그 (예: FDA)")
            if st.form_submit_button("추가") and new_name and new_url:
                all_sources.append(
                    {
                        "name": new_name,
                        "type": "rss",
                        "url": new_url,
                        "tag": new_tag or new_name,
                        "region": "Custom",
                        "enabled": True,
                    }
                )
                save_sources(str(SOURCES_CONFIG), all_sources)
                st.success("사이트를 추가했습니다.")
                st.rerun()

    st.divider()
    if os.environ.get("ANTHROPIC_API_KEY"):
        st.caption("✅ Claude 요약 사용 중 (번역/분석 포함)")
    else:
        st.caption("⚠️ ANTHROPIC_API_KEY 미설정 — 단순 추출 요약으로 동작")

# ---------------- 본문: 기사 목록 + 체크박스 선택 ----------------
articles = store.list_articles(
    keyword=keyword or None,
    unchecked_only=unchecked_only,
    limit=300,
)
if selected_sources:
    articles = [a for a in articles if a.source in selected_sources]

st.header("기사 목록")

if not articles:
    st.info("표시할 기사가 없습니다. 왼쪽에서 '기사 수집 실행'을 눌러주세요.")
else:
    df = pd.DataFrame(
        [
            {
                "id": a.id,
                "선택": False,
                "날짜": (a.published_at or "")[:10],
                "출처": a.tag or a.source,
                "제목": clean_text(a.title),
                "링크": a.url,
                "요약됨": "✅" if a.summarized else "",
            }
            for a in articles
        ]
    )

    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        column_order=["선택", "날짜", "출처", "제목", "링크", "요약됨"],
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", default=False),
            "링크": st.column_config.LinkColumn("원문", display_text="열기"),
        },
        disabled=["날짜", "출처", "제목", "링크", "요약됨"],
        key="article_editor",
    )

    selected_ids = edited.loc[edited["선택"], "id"].tolist()
    st.caption(f"{len(articles)}건 표시 중 · {len(selected_ids)}건 선택됨")

    def run_summarize(mode: str, spinner_label: str):
        fmt = load_format(str(FORMAT_CONFIG))
        selected_articles = store.get_by_ids(selected_ids)
        entries = []
        errors = []
        progress = st.progress(0.0)
        for i, a in enumerate(selected_articles, start=1):
            with st.spinner(f"{spinner_label} ({i}/{len(selected_articles)}): [{a.tag or a.source}] {a.title}"):
                full_text = fetch_full_text(a.url)
                try:
                    body = summarize_article(a, full_text, fmt, mode=mode)
                except Exception as exc:
                    errors.append(f"{a.title}: {exc}")
                    body = f"- (요약 실패: {exc})"
                entries.append(SummaryEntry(index=i, article=a, body=body))
            progress.progress(i / len(selected_articles))

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(OUTPUT_DIR / f"summary_{ts}.{output_extension(fmt)}")
        write_summaries(fmt, entries, output_path)
        store.set_summarized(selected_ids, True)
        store.set_checked(selected_ids, False)

        st.session_state["last_summary_preview"] = render_txt(fmt, entries)
        st.session_state["last_summary_path"] = output_path
        if errors:
            st.warning(f"{len(entries)}건 중 {len(errors)}건 요약 실패:\n" + "\n".join(errors))
        else:
            st.success(f"{len(entries)}건 요약 완료 → {output_path}")
        st.rerun()

    has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "🆓 단순 요약 (토큰 미사용)",
            use_container_width=True,
            disabled=not selected_ids,
            help="번역 없이 원문 문장을 그대로 추출합니다. 내용을 먼저 확인할 때 사용하세요.",
        ):
            run_summarize("simple", "단순 요약 중")
    with col2:
        if st.button(
            "🤖 AI 요약 생성 (토큰 사용)",
            type="primary",
            use_container_width=True,
            disabled=not selected_ids or not has_api_key,
            help=(
                "Claude API로 한국어 번역·요약을 생성합니다 (기사당 토큰 소모)."
                if has_api_key
                else "ANTHROPIC_API_KEY가 설정되어 있지 않아 사용할 수 없습니다."
            ),
        ):
            run_summarize("ai", "AI 요약 생성 중")

if "last_summary_preview" in st.session_state:
    st.divider()
    st.header("요약 결과")
    # 글머리 기호/글자 크기가 뒤죽박죽 보이지 않도록 마크다운 해석 없이 고정폭 텍스트로 표시
    st.code(st.session_state["last_summary_preview"], language=None)
    path = st.session_state["last_summary_path"]
    if Path(path).exists():
        with open(path, "rb") as f:
            st.download_button("📥 파일 다운로드", f, file_name=Path(path).name, use_container_width=True)
