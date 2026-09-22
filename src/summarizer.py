import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from .models import Article

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")


@dataclass
class SummaryField:
    key: str
    label: str
    instruction: str


@dataclass
class SummaryFormat:
    title: str
    output_format: str
    fields: List[SummaryField]


def load_format(config_path: str) -> SummaryFormat:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    fields = [SummaryField(**f) for f in data.get("fields", [])]
    return SummaryFormat(
        title=data.get("title", "규제동향 요약"),
        output_format=data.get("output_format", "md"),
        fields=fields,
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
        logger.warning("요약 결과 JSON 파싱 실패, 원문 그대로 사용")
        return None


def _summarize_with_claude(
    article: Article, full_text: Optional[str], fmt: SummaryFormat
) -> Dict[str, str]:
    from anthropic import Anthropic

    client = Anthropic()  # reads ANTHROPIC_API_KEY from env

    field_spec = "\n".join(f'- "{f.key}" ({f.label}): {f.instruction}' for f in fmt.fields)
    body = full_text or article.excerpt or "(본문을 가져오지 못했습니다. 제목과 출처 정보만으로 작성)"
    # keep prompt size reasonable
    body = body[:12000]

    prompt = f"""다음은 해외 의약품 규제 관련 뉴스 기사입니다.

제목: {article.title}
출처: {article.source}
발행일: {article.published_at or "미상"}
URL: {article.url}

본문:
{body}

위 기사를 분석해서 아래 항목들을 채운 JSON 객체 하나만 출력하세요. 다른 설명 문구는 출력하지 마세요.
각 항목의 값은 한국어 문자열이며, 불릿이 필요하면 문자열 안에 "- " 로 시작하는 줄바꿈(\\n)을 사용하세요.

항목 목록:
{field_spec}

출력 형식 예시: {{"{fmt.fields[0].key if fmt.fields else "field"}": "...", ...}}
"""

    resp = client.messages.create(
        model=DEFAULT_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
    parsed = _extract_json(text)
    if parsed is None:
        return {f.key: text.strip() for f in fmt.fields[:1]} | {
            f.key: "" for f in fmt.fields[1:]
        }
    return {f.key: str(parsed.get(f.key, "")).strip() for f in fmt.fields}


def _summarize_fallback(article: Article, full_text: Optional[str], fmt: SummaryFormat) -> Dict[str, str]:
    """ANTHROPIC_API_KEY가 없을 때 사용하는 단순 추출 요약 (번역/분석 없음)."""
    text = (full_text or article.excerpt or "").strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)
    bullets = [s.strip() for s in sentences if s.strip()][:5]
    summary = "\n".join(f"- {s}" for s in bullets) if bullets else "- (본문을 가져오지 못했습니다)"

    values = {
        "title_kr": article.title,
        "source_meta": f"{article.source} · {article.published_at or '발행일 미상'}",
        "summary": summary,
        "impact": "(ANTHROPIC_API_KEY 미설정으로 자동 분석을 생략했습니다. 직접 검토가 필요합니다.)",
        "action_items": "- 원문을 직접 확인하고 국내 영향 여부 검토",
        "source_url": article.url,
    }
    return {f.key: values.get(f.key, "") for f in fmt.fields}


def summarize_article(article: Article, full_text: Optional[str], fmt: SummaryFormat) -> Dict[str, str]:
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _summarize_with_claude(article, full_text, fmt)
        except Exception as exc:
            logger.warning("Claude 요약 실패, 단순 요약으로 대체 (%s): %s", article.title, exc)
    return _summarize_fallback(article, full_text, fmt)


def render_markdown(fmt: SummaryFormat, entries: List[Dict[str, str]]) -> str:
    lines = [f"# {fmt.title}", ""]
    for values in entries:
        heading = values.get(fmt.fields[0].key, "제목 없음") if fmt.fields else "제목 없음"
        lines.append(f"## {heading}")
        for f in fmt.fields[1:] if fmt.fields else []:
            lines.append(f"**{f.label}**")
            lines.append(values.get(f.key, ""))
            lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


def render_txt(fmt: SummaryFormat, entries: List[Dict[str, str]]) -> str:
    lines = [fmt.title, "=" * len(fmt.title), ""]
    for values in entries:
        for f in fmt.fields:
            lines.append(f"[{f.label}] {values.get(f.key, '')}")
        lines.append("-" * 40)
        lines.append("")
    return "\n".join(lines)


def render_docx(fmt: SummaryFormat, entries: List[Dict[str, str]], output_path: str) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading(fmt.title, level=1)
    for values in entries:
        heading = values.get(fmt.fields[0].key, "제목 없음") if fmt.fields else "제목 없음"
        doc.add_heading(heading, level=2)
        for f in fmt.fields[1:] if fmt.fields else []:
            p = doc.add_paragraph()
            p.add_run(f"{f.label}: ").bold = True
            p.add_run(values.get(f.key, ""))
        doc.add_paragraph("―" * 20)
    doc.save(output_path)


def write_summaries(fmt: SummaryFormat, entries: List[Dict[str, str]], output_path: str) -> str:
    output_format = fmt.output_format.lower()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    if output_format == "docx":
        render_docx(fmt, entries, output_path)
    elif output_format == "txt":
        Path(output_path).write_text(render_txt(fmt, entries), encoding="utf-8")
    else:
        Path(output_path).write_text(render_markdown(fmt, entries), encoding="utf-8")
    return output_path
