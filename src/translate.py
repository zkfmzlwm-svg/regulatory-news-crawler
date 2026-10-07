"""무료(비공식) 번역. API 키 없이 여러 공개 번역 엔드포인트를 차례로 시도한다.

예전에 쓰던 deep-translator 는 translate.google.com/m 페이지를 긁어오는데, Google 이 이 주소를
봇으로 판단해 차단(429 / sorry 페이지)하면서 "Server Error: You made too many requests" 오류로
번역이 전부 실패했다. 그래서 아래 순서로 다른 엔드포인트를 쓰고, 하나가 막히면 다음 것으로 넘어간다.

1. clients5.google.com (Chrome 사전 확장용) — 한 번의 요청으로 여러 문장을 번역
2. translate.googleapis.com (gtx) — 문장별 요청
3. MyMemory — Google 이 모두 막혔을 때의 마지막 대안 (익명 하루 약 5000자)
"""
import logging
import re
import time
from typing import Callable, List, Optional

import requests

from .net import HEADERS, cffi_requests

logger = logging.getLogger(__name__)

TIMEOUT = 20
RETRY_STATUSES = (429, 500, 502, 503, 504)
# Google 번역 결과에 섞여 나오는 폭 없는 공백 (복사해 붙여넣으면 글자 사이가 이상하게 벌어짐)
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


class TranslationError(Exception):
    pass


def _request(method: str, url: str, **kwargs):
    """브라우저 TLS 지문(curl_cffi)으로 요청. 없으면 requests 로."""
    if cffi_requests is not None:
        return cffi_requests.request(method, url, impersonate="chrome", timeout=TIMEOUT, **kwargs)
    return requests.request(method, url, headers=HEADERS, timeout=TIMEOUT, **kwargs)


def _call(method: str, url: str, retries: int = 2, **kwargs):
    """일시 오류(429/5xx)면 잠시 쉬었다가 재시도. 그래도 안 되면 TranslationError."""
    last = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(1.5 * attempt)
        try:
            resp = _request(method, url, **kwargs)
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            continue
        if resp.status_code == 200:
            return resp
        last = f"HTTP {resp.status_code}"
        if resp.status_code not in RETRY_STATUSES:
            break
    raise TranslationError(last or "알 수 없는 오류")


def _clients5(texts: List[str], target: str) -> List[str]:
    resp = _call(
        "POST",
        "https://clients5.google.com/translate_a/t",
        params={"client": "dict-chrome-ex", "sl": "auto", "tl": target},
        data=[("q", t) for t in texts],
    )
    data = resp.json()
    out = []
    for item in data:
        # 문장 여러 개: [["번역", "en"], ...] / 하나: ["번역"] 또는 [["번역","en"]]
        out.append(item[0] if isinstance(item, list) else item)
    if len(out) != len(texts) or not all(isinstance(t, str) and t for t in out):
        raise TranslationError("응답 형식이 예상과 다릅니다")
    return out


def _gtx(texts: List[str], target: str) -> List[str]:
    out = []
    for t in texts:
        resp = _call(
            "GET",
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "auto", "tl": target, "dt": "t", "q": t},
        )
        segments = resp.json()[0] or []
        translated = "".join(seg[0] for seg in segments if seg and seg[0])
        if not translated:
            raise TranslationError("빈 번역 결과")
        out.append(translated)
    return out


def _mymemory(texts: List[str], target: str) -> List[str]:
    out = []
    for t in texts:
        resp = _call(
            "GET",
            "https://api.mymemory.translated.net/get",
            params={"q": t[:500], "langpair": f"en|{target}"},
        )
        data = resp.json()
        translated = (data.get("responseData") or {}).get("translatedText") or ""
        if str(data.get("responseStatus")) != "200" or not translated or "MYMEMORY WARNING" in translated:
            raise TranslationError(f"MyMemory: {data.get('responseDetails') or '번역 실패'}")
        out.append(translated)
    return out


_BACKENDS: List[Callable[[List[str], str], List[str]]] = [_clients5, _gtx, _mymemory]

# 마지막으로 성공한 엔드포인트를 먼저 시도해 차단된 곳에 매번 시간을 쓰지 않는다
_preferred: Optional[Callable] = None


def translate_texts(texts: List[str], target: str = "ko") -> List[str]:
    """문장 목록을 번역해 같은 순서로 돌려준다. 모든 엔드포인트가 실패하면 TranslationError."""
    global _preferred
    if not texts:
        return []
    order = ([_preferred] if _preferred else []) + [b for b in _BACKENDS if b is not _preferred]
    errors = []
    for backend in order:
        name = backend.__name__.lstrip("_")
        try:
            result = [_ZERO_WIDTH.sub("", t) for t in backend(texts, target)]
            _preferred = backend
            return result
        except Exception as exc:
            logger.info("번역 엔드포인트 %s 실패: %s", name, exc)
            errors.append(f"{name}: {exc}")
    raise TranslationError("모든 번역 서버 접속 실패 (" + "; ".join(errors) + ")")
