import logging
import re
from typing import List, Optional, Tuple
from urllib.parse import quote, urlparse

import trafilatura
from bs4 import BeautifulSoup

from .net import http_get
from .utils import is_pdf_url

logger = logging.getLogger(__name__)

MIN_PRECISE_LEN = 800

# ICH 사이트(www.ich.org)는 자바스크립트로 그려져 페이지 HTML 에 본문이 없다.
# 페이지가 쓰는 API 에 주소(alias)를 넘기면 본문 문단(HTML)을 JSON 으로 준다.
ICH_HOSTS = ("www.ich.org", "ich.org")
ICH_NODE_API = "https://admin.ich.org/api/v1/nodes?loadEntities%5B%5D=paragraph&alias="
_BLOCK_TAGS = ["p", "li", "h1", "h2", "h3", "h4", "h5", "h6"]


def _collect_texts(node, out: List[str]) -> None:
    """API 응답 안의 문단 위젯("text" 항목, HTML)을 순서대로 모은다."""
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            out.append(node["text"])
        for value in node.values():
            _collect_texts(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_texts(value, out)


def _fetch_ich(url: str) -> Tuple[Optional[str], Optional[str]]:
    path = urlparse(url).path
    try:
        items = http_get(ICH_NODE_API + quote(path)).json().get("items") or []
    except Exception as exc:
        logger.warning("ICH 본문 API 실패 (%s): %s", url, exc)
        return None, None
    if not items:
        return None, None
    title = re.sub(r"\s+", " ", (items[0].get("bundleInfo") or {}).get("title") or "").strip() or None
    htmls: List[str] = []
    _collect_texts(items[0].get("mainWidgets"), htmls)
    blocks = []
    for html in htmls:
        soup = BeautifulSoup(html, "lxml")
        for el in soup.find_all(_BLOCK_TAGS):
            if el.find(_BLOCK_TAGS):  # 목록 안의 문단 등 중첩된 블록은 안쪽에서 처리
                continue
            text = re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip()
            if text:
                blocks.append(text)
    return title, ("\n".join(blocks) or None)


def _strip_site_name(title: str) -> str:
    """"Advice on ... | European Medicines Agency (EMA)" 처럼 페이지 제목 뒤에 붙은 사이트 이름을 뗀다."""
    head, sep, _ = title.rpartition(" | ")
    return head.strip() if sep and len(head.strip()) >= 15 else title


def fetch_full_text(url: str) -> Optional[str]:
    """원문 기사 페이지에서 본문 텍스트를 추출. 실패 시 None."""
    return fetch_page(url)[1]


def fetch_page(url: str) -> Tuple[Optional[str], Optional[str]]:
    """페이지의 (제목, 본문 텍스트)를 추출. 실패한 항목은 None."""
    # PDF 는 웹페이지 본문 추출기로 읽을 수 없어(바이너리가 그대로 본문이 됨) 내려받지 않는다.
    # NMPA 뉴스레터처럼 한 파일이 수십 MB 인 경우도 있다.
    if is_pdf_url(url):
        logger.info("PDF 문서는 본문을 추출하지 않음 (%s)", url)
        return None, None
    if urlparse(url).netloc.lower() in ICH_HOSTS:
        return _fetch_ich(url)
    try:
        resp = http_get(url)
    except Exception as exc:
        logger.warning("본문 다운로드 실패 (%s): %s", url, exc)
        return None, None
    if resp.content[:5] == b"%PDF-":
        logger.info("PDF 문서는 본문을 추출하지 않음 (%s)", url)
        return None, None

    # resp.text 는 헤더에 charset 이 없으면 ISO-8859-1 로 잘못 디코딩해 글자가 깨지므로,
    # 원본 바이트를 넘겨 trafilatura 가 <meta charset> 으로 직접 판단하게 한다.
    page_url = getattr(resp, "url", None) or url
    title = None
    try:
        meta = trafilatura.extract_metadata(resp.content, default_url=page_url)
        title = (_strip_site_name((meta.title or "").strip()) or None) if meta else None
    except Exception as exc:
        logger.warning("제목 추출 실패 (%s): %s", url, exc)
    text = None
    try:
        # 정밀 모드는 메뉴·광고를 잘 거르지만 TGA 처럼 본문 대부분을 버리는 사이트가 있어,
        # 결과가 너무 짧으면 일반 모드로 다시 추출해 더 긴 쪽을 쓴다.
        text = trafilatura.extract(
            resp.content, include_comments=False, include_tables=False, favor_precision=True, url=page_url
        )
        if not text or len(text) < MIN_PRECISE_LEN:
            relaxed = trafilatura.extract(
                resp.content, include_comments=False, include_tables=False, url=page_url
            )
            if relaxed and len(relaxed) > len(text or ""):
                text = relaxed
    except Exception as exc:  # 특이한 페이지 하나 때문에 요약 전체가 멈추지 않도록
        logger.warning("본문 추출 중 오류 (%s): %s", url, exc)
        return title, None
    if not text:
        logger.warning("본문 추출 실패 (%s)", url)
        return title, None
    return title, text.strip()
