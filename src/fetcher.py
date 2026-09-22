import logging
from typing import Optional

import requests
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = "regulatory-news-crawler/1.0 (+personal research tool)"
REQUEST_TIMEOUT = 20


def fetch_full_text(url: str) -> Optional[str]:
    """원문 기사 페이지에서 본문 텍스트를 추출. 실패 시 None."""
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("본문 다운로드 실패 (%s): %s", url, exc)
        return None

    text = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
        url=url,
    )
    if not text:
        logger.warning("본문 추출 실패 (%s)", url)
        return None
    return text.strip()
