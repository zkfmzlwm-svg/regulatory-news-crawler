import logging
from typing import Optional

import trafilatura

from .net import http_get

logger = logging.getLogger(__name__)


def fetch_full_text(url: str) -> Optional[str]:
    """원문 기사 페이지에서 본문 텍스트를 추출. 실패 시 None."""
    try:
        resp = http_get(url)
    except Exception as exc:
        logger.warning("본문 다운로드 실패 (%s): %s", url, exc)
        return None

    try:
        # resp.text 는 헤더에 charset 이 없으면 ISO-8859-1 로 잘못 디코딩해 글자가 깨지므로,
        # 원본 바이트를 넘겨 trafilatura 가 <meta charset> 으로 직접 판단하게 한다.
        text = trafilatura.extract(
            resp.content,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
            url=resp.url or url,
        )
    except Exception as exc:  # 특이한 페이지 하나 때문에 요약 전체가 멈추지 않도록
        logger.warning("본문 추출 중 오류 (%s): %s", url, exc)
        return None
    if not text:
        logger.warning("본문 추출 실패 (%s)", url)
        return None
    return text.strip()
