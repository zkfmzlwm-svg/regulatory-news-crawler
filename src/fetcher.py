import logging
from typing import Optional, Tuple

import trafilatura

from .net import http_get
from .utils import is_pdf_url

logger = logging.getLogger(__name__)

MIN_PRECISE_LEN = 800


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
