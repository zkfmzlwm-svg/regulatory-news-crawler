import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

from .models import Article
from .utils import clean_text, strip_tags

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


@dataclass
class SummaryFormat:
    title: str
    output_format: str
    style_example: str
    instruction: str


@dataclass
class SummaryEntry:
    index: int
    article: Article
    body: str

    @property
    def tag(self) -> str:
        return self.article.tag or self.article.source

    @property
    def title(self) -> str:
        """요약 제목 줄에 쓰는 원문(영문) 제목. 번역하지 않고 그대로 사용."""
        return clean_text(self.article.title)


def load_format(config_path: str) -> SummaryFormat:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return SummaryFormat(
        title=data.get("title", "규제동향 요약"),
        output_format=data.get("output_format", "md"),
        style_example=data.get("style_example", ""),
        instruction=data.get("instruction", ""),
    )


def _summarize_with_claude(
    article: Article, full_text: Optional[str], fmt: SummaryFormat, api_key: Optional[str] = None
) -> str:
    from anthropic import Anthropic

    # api_key가 주어지면 그 키를 쓰고, 없으면 SDK가 ANTHROPIC_API_KEY 환경변수를 읽음
    client = Anthropic(api_key=api_key) if api_key else Anthropic()

    body = full_text or article.excerpt or "(본문을 가져오지 못했습니다. 제목과 출처 정보만으로 작성)"
    body = body[:12000]  # keep prompt size reasonable

    prompt = f"""다음은 해외 의약품 규제 관련 뉴스 기사입니다.

제목: {article.title}
출처: {article.source}
발행일: {article.published_at or "미상"}
URL: {article.url}

본문:
{body}

---
아래는 원하는 요약 서식의 예시입니다 (번호/[태그]/제목/링크 줄은 프로그램이 자동으로 채우므로 무시하고,
본문 불릿의 형식만 참고하세요):

{fmt.style_example}
---

지침:
{fmt.instruction}

위 기사 본문을 분석해서 예시의 본문 불릿과 동일한 형식으로만 출력하세요.
- 반드시 한국어로 작성하세요 (원문이 영어여도 한국어로 번역/요약)
- "- "로 시작하는 불릿, 필요하면 "  : "로 들여쓴 세부 항목으로 구성
- 불릿 개수는 내용에 맞게 3~6개 정도
- 번호, 제목 줄, 링크 줄은 포함하지 말고 본문 불릿만 출력
- 다른 설명 문구 없이 본문 불릿만 출력하세요
"""

    resp = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    return strip_tags(text.strip())


def _extract_sentences(article: Article, full_text: Optional[str]) -> List[str]:
    text = strip_tags(full_text or article.excerpt or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()][:5]


def _summarize_fallback(article: Article, full_text: Optional[str]) -> str:
    """API 키가 없을 때 쓰는 가장 단순한 추출 요약 (번역 없이 원문 문장 그대로)."""
    bullets = _extract_sentences(article, full_text)
    return "\n".join(f"- {s}" for s in bullets) if bullets else "- (본문을 가져오지 못했습니다)"


def _summarize_free_translate(article: Article, full_text: Optional[str]) -> str:
    """Anthropic API 키 없이 Google 번역(비공식, 무료)으로 원문 문장을 한국어로 옮긴 요약.

    실제 AI 요약처럼 내용을 재구성/분석하지는 않고 추출한 문장을 그대로 번역만 하므로
    품질은 AI 요약보다 단순하지만, 번역된 한국어 결과를 얻는 데 비용이 들지 않는다.
    """
    bullets = _extract_sentences(article, full_text)
    if not bullets:
        return "- (본문을 가져오지 못했습니다)"

    try:
        from deep_translator import GoogleTranslator

        translator = GoogleTranslator(source="auto", target="ko")
    except Exception as exc:
        logger.warning("번역 모듈 로드 실패, 원문으로 대체: %s", exc)
        return "\n".join(f"- {s}" for s in bullets)

    lines = []
    for s in bullets:
        try:
            translated = translator.translate(s)
            lines.append(f"- {translated or s}")
        except Exception as exc:
            logger.warning("문장 번역 실패, 원문 유지 (%s): %s", article.title, exc)
            lines.append(f"- {s} (번역 실패)")
    return "\n".join(lines)


def summarize_article(
    article: Article,
    full_text: Optional[str],
    fmt: SummaryFormat,
    mode: str = "auto",
    api_key: Optional[str] = None,
) -> str:
    """체크한 기사 본문을 요약해 body(불릿 텍스트)를 반환.

    mode:
      - "simple": 토큰을 쓰지 않는 단순 추출 요약 (번역 없음, 무료, API 키 불필요)
      - "free": Google 번역(비공식, 무료)으로 문장을 한국어로 번역 (API 키 불필요)
      - "ai": Claude API로 번역/요약 (토큰 사용). 키가 없으면 예외 발생
      - "auto": 키가 있으면 ai, 없으면 simple로 자동 대체 (기존 동작)

    api_key: 명시적으로 지정하면 이 키를 사용 (예: 화면에서 개인 키를 입력한 경우).
             지정하지 않으면 ANTHROPIC_API_KEY 환경변수를 사용.
    """
    effective_key = api_key or os.environ.get("ANTHROPIC_API_KEY")

    if mode == "simple":
        return _summarize_fallback(article, full_text)

    if mode == "free":
        return _summarize_free_translate(article, full_text)

    if mode == "ai":
        if not effective_key:
            raise RuntimeError("API 키가 설정되어 있지 않아 AI 요약을 사용할 수 없습니다.")
        return _summarize_with_claude(article, full_text, fmt, api_key=api_key)

    if effective_key:
        try:
            return _summarize_with_claude(article, full_text, fmt, api_key=api_key)
        except Exception as exc:
            logger.warning("Claude 요약 실패, 단순 요약으로 대체 (%s): %s", article.title, exc)
    return _summarize_fallback(article, full_text)


def render_markdown(fmt: SummaryFormat, entries: List[SummaryEntry]) -> str:
    lines = [f"# {fmt.title}", ""]
    for e in entries:
        lines.append(f"{e.index}. [{e.tag}] {e.title}")
        if e.body:
            lines.append(e.body)
        lines.append(f"- 링크: [{e.title} | {e.tag}]({e.article.url})")
        lines.append("")
    return "\n".join(lines)


def render_txt(fmt: SummaryFormat, entries: List[SummaryEntry]) -> str:
    lines = [fmt.title, "=" * len(fmt.title), ""]
    for e in entries:
        lines.append(f"{e.index}. [{e.tag}] {e.title}")
        if e.body:
            lines.append(e.body)
        lines.append(f"- 링크: {e.title} ({e.tag}) - {e.article.url}")
        lines.append("")
    return "\n".join(lines)


def _add_hyperlink(paragraph, url: str, text: str):
    """python-docx 는 하이퍼링크를 기본 지원하지 않아 저수준 XML로 추가."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    part = paragraph.part
    r_id = part.relate_to(
        url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink", is_external=True
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)

    run = OxmlElement("w:r")
    rPr = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), "0563C1")
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    rPr.append(color)
    rPr.append(underline)
    run.append(rPr)
    text_el = OxmlElement("w:t")
    text_el.text = text
    run.append(text_el)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)


def render_docx(fmt: SummaryFormat, entries: List[SummaryEntry], output_path: str) -> None:
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    doc.styles["Normal"].font.size = Pt(11)
    for style_name in ("List Bullet", "List Bullet 2"):
        if style_name in doc.styles:
            doc.styles[style_name].font.size = Pt(11)

    doc.add_heading(fmt.title, level=1)
    for e in entries:
        doc.add_heading(f"{e.index}. [{e.tag}] {e.title}", level=2)
        for line in e.body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(": "):
                doc.add_paragraph(stripped[2:], style="List Bullet 2")
            elif stripped.startswith("- "):
                doc.add_paragraph(stripped[2:], style="List Bullet")
            else:
                doc.add_paragraph(stripped, style="List Bullet")
        link_p = doc.add_paragraph()
        link_p.add_run("링크: ")
        try:
            _add_hyperlink(link_p, e.article.url, f"{e.title} | {e.tag}")
        except Exception:
            link_p.add_run(f"{e.title} | {e.tag} ({e.article.url})")
    doc.save(output_path)


def output_extension(fmt: SummaryFormat) -> str:
    return {"docx": "docx", "txt": "txt"}.get(fmt.output_format.lower(), "md")


def write_summaries(fmt: SummaryFormat, entries: List[SummaryEntry], output_path: str) -> str:
    output_format = fmt.output_format.lower()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if output_format == "docx":
        render_docx(fmt, entries, output_path)
    elif output_format == "txt":
        Path(output_path).write_text(render_txt(fmt, entries), encoding="utf-8")
    else:
        Path(output_path).write_text(render_markdown(fmt, entries), encoding="utf-8")
    return output_path
