import html
import re

from bs4 import BeautifulSoup

_TAG_RE = re.compile(r"<[^>]+>")


def clean_text(text: str) -> str:
    """제목/요약 등 한 줄짜리 텍스트에서 HTML 태그·엔티티를 제거하고 공백을 정리."""
    if not text:
        return ""
    cleaned = BeautifulSoup(text, "lxml").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", cleaned).strip()


def strip_tags(text: str) -> str:
    """줄바꿈/들여쓰기는 유지한 채 본문에서 HTML 태그만 제거."""
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", text))
