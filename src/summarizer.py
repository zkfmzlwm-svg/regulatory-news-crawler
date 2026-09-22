import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

from .models import Article

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
    title_kr: str
    body: str

    @property
    def tag(self) -> str:
        return self.article.tag or self.article.source


def load_format(config_path: str) -> SummaryFormat:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return SummaryFormat(
        title=data.get("title", "규제동향 요약"),
        output_format=data.get("output_format", "md"),
        style_example=data.get("style_example", ""),
        instruction=data.get("instruction", ""),
    )


def _extract_json(text: str) -> Optional[dict]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", text, re.DOTALL)
        if brace:
            text = brace.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("요약 결과 JSON 파싱 실패")
        return None


def _summarize_with_claude(article: Article, full_text: Optional[str], fmt: SummaryFormat) -> Tuple[str, str]:
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY from env

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
아래는 원하는 요약 서식의 예시입니다 (번호/[태그]/링크 줄은 프로그램이 자동 생성하므로 무시하고,
제목 문구와 본문 불릿 형식만 참고하세요):

{fmt.style_example}
---

지침:
{fmt.instruction}

위 기사를 지침과 예시 형식에 맞춰 요약해서, 아래 두 항목만 담은 JSON 객체 하나로 출력하세요.
다른 설명 문구 없이 JSON만 출력하세요.
- "title_kr": 예시의 제목 줄처럼 핵심을 담은 한글 제목 (번호/태그 없이 제목 문구만)
- "body": 예시의 본문 불릿처럼 "- "로 시작하는 불릿과 필요시 "  : "로 들여쓴 세부 항목으로 구성된 문자열 (줄바꿈은 \\n 사용, 링크 줄은 포함하지 않음)
"""

    resp = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    parsed = _extract_json(text)
    if parsed is None:
        return article.title, text.strip()
    return str(parsed.get("title_kr", article.title)).strip(), str(parsed.get("body", "")).strip()


def _summarize_fallback(article: Article, full_text: Optional[str]) -> Tuple[str, str]:
    """ANTHROPIC_API_KEY가 없을 때 사용하는 단순 추출 요약 (번역/구조화 없음)."""
    text = (full_text or article.excerpt or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    bullets = [s.strip() for s in sentences if s.strip()][:5]
    body = "\n".join(f"- {s}" for s in bullets) if bullets else "- (본문을 가져오지 못했습니다)"
    return article.title, body


def summarize_article(article: Article, full_text: Optional[str], fmt: SummaryFormat) -> Tuple[str, str]:
    """Returns (title_kr, body) for one article."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _summarize_with_claude(article, full_text, fmt)
        except Exception as exc:
            logger.warning("Claude 요약 실패, 단순 요약으로 대체 (%s): %s", article.title, exc)
    return _summarize_fallback(article, full_text)


def render_markdown(fmt: SummaryFormat, entries: List[SummaryEntry]) -> str:
    lines = [f"# {fmt.title}", ""]
    for e in entries:
        lines.append(f"{e.index}. [{e.tag}] {e.title_kr}")
        if e.body:
            lines.append(e.body)
        lines.append(f"- 링크: [{e.article.title} | {e.tag}]({e.article.url})")
        lines.append("")
    return "\n".join(lines)


def render_txt(fmt: SummaryFormat, entries: List[SummaryEntry]) -> str:
    lines = [fmt.title, "=" * len(fmt.title), ""]
    for e in entries:
        lines.append(f"{e.index}. [{e.tag}] {e.title_kr}")
        if e.body:
            lines.append(e.body)
        lines.append(f"- 링크: {e.article.title} ({e.tag}) - {e.article.url}")
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

    doc = Document()
    doc.add_heading(fmt.title, level=1)
    for e in entries:
        doc.add_heading(f"{e.index}. [{e.tag}] {e.title_kr}", level=2)
        for line in e.body.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(": "):
                doc.add_paragraph(stripped[2:], style="List Bullet 2")
            elif stripped.startswith("- "):
                doc.add_paragraph(stripped[2:], style="List Bullet")
            else:
                doc.add_paragraph(stripped)
        link_p = doc.add_paragraph()
        link_p.add_run("링크: ")
        try:
            _add_hyperlink(link_p, e.article.url, f"{e.article.title} | {e.tag}")
        except Exception:
            link_p.add_run(f"{e.article.title} | {e.tag} ({e.article.url})")
    doc.save(output_path)


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
