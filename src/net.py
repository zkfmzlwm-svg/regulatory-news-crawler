import logging
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    # FDA·TGA(Akamai) 등은 User-Agent 가 아니라 TLS 지문으로 파이썬 요청을 막는다(401/403).
    # curl_cffi 는 실제 브라우저의 TLS 지문을 흉내내므로 이런 사이트도 열린다.
    from curl_cffi import requests as cffi_requests
except ImportError:  # 설치 안 됐으면 requests 만으로 동작
    cffi_requests = None

if cffi_requests is not None:
    try:
        from curl_cffi.requests import BrowserType

        _supported = {b.value for b in BrowserType} | {"chrome", "safari", "firefox", "edge"}
    except ImportError:
        _supported = {"chrome", "safari", "edge"}
else:
    _supported = set()

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 25

# Akamai(FDA 등)는 요청마다 일정 비율(실측 약 40%)을 무작위로 막고, 막히는 브라우저 종류도 매번 달라진다.
# 여러 브라우저·버전을 차례로 시도하면 거의 항상 통과한다.
_WANTED_TARGETS = (
    "chrome", "firefox", "safari", "edge",
    "chrome124", "firefox135", "safari180", "chrome136", "firefox147", "safari184",
)
BLOCKED_STATUSES = (401, 403, 406)
RETRY_STATUSES = (429, 500, 502, 503, 504)
IMPERSONATE_TARGETS = tuple(t for t in _WANTED_TARGETS if t in _supported)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/rss+xml,application/atom+xml,"
        "application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class HttpStatusError(Exception):
    def __init__(self, status_code: int, url: str):
        super().__init__(f"HTTP {status_code}: {url}")
        self.status_code = status_code
        self.url = url


_session = None


def _requests_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(total=2, backoff_factor=1.5, status_forcelist=RETRY_STATUSES, allowed_methods=("GET",))
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update(HEADERS)
        _session = s
    return _session


# 호스트별로 마지막에 통과한 브라우저를 기억해 다음 요청 때 먼저 시도 (Akamai 는 시간대마다 통과 브라우저가 바뀜)
_preferred_target = {}


def _get_with_cffi(url: str):
    """브라우저를 바꿔가며 요청. 차단(401/403/406)이면 다음 브라우저로, 일시 오류면 잠시 후 재시도."""
    host = urlparse(url).netloc
    first = _preferred_target.get(host)
    order = ([first] if first else []) + [t for t in IMPERSONATE_TARGETS if t != first]
    resp = None
    last_exc = None
    for attempt in range(2):
        if attempt:
            time.sleep(3)
        for target in order:
            try:
                resp = cffi_requests.get(url, impersonate=target, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            except cffi_requests.exceptions.RequestException as exc:
                last_exc = exc
                logger.debug("%s 요청 오류 (%s): %s", target, url, exc)
                time.sleep(1.5)
                continue
            if resp.status_code in RETRY_STATUSES:
                time.sleep(1.5)
                continue
            if resp.status_code in BLOCKED_STATUSES:
                if resp.headers.get("cf-mitigated") == "challenge":
                    # Cloudflare 자바스크립트 챌린지는 브라우저를 바꿔도 통과 못 하므로 바로 포기
                    return resp
                time.sleep(0.5)
                continue
            if resp.status_code not in BLOCKED_STATUSES:
                _preferred_target[host] = target
                return resp
    if resp is None and last_exc is not None:
        raise last_exc
    return resp


def http_get(url: str):
    """URL 을 받아 응답 객체(.content/.text/.url/.status_code)를 돌려준다. 실패 시 예외."""
    # FDA RSS 등은 기사 링크를 http:// 로 주는데, http 요청은 차단되는 경우가 많아 https 로 올려 요청
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if cffi_requests is not None:
        resp = _get_with_cffi(url)
    else:
        resp = _requests_session().get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code >= 400:
        raise HttpStatusError(resp.status_code, url)
    return resp


def describe_error(exc: Exception) -> str:
    """화면에 보여줄 짧은 오류 설명."""
    status = None
    if isinstance(exc, HttpStatusError):
        status = exc.status_code
    elif isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
    if status is not None:
        hint = {
            401: " 봇 차단",
            403: " 접근 차단",
            404: " 주소 없음(사이트 개편 가능성)",
            429: " 요청 과다",
        }.get(status, "")
        return f"HTTP {status}{hint}"

    name = type(exc).__name__
    text = str(exc)
    if isinstance(exc, requests.Timeout) or name == "Timeout" or "timed out" in text.lower():
        return "응답 시간 초과"
    if isinstance(exc, requests.exceptions.SSLError) or "SSL" in text:
        return "보안 연결(SSL) 실패"
    if isinstance(exc, requests.exceptions.ProxyError) or "proxy" in text.lower():
        return "프록시/방화벽에서 차단됨"
    if isinstance(exc, requests.ConnectionError) or name in ("ConnectionError", "DNSError"):
        return "접속 실패"
    first_line = (text.strip().splitlines() or [""])[0]
    return f"{name}: {first_line[:120]}"
