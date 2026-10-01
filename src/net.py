import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

REQUEST_TIMEOUT = 20

# 봇처럼 보이는 User-Agent 는 FDA 등 일부 기관 사이트에서 403 으로 차단되므로 일반 브라우저처럼 요청한다.
# (Akamai 등 봇 차단은 User-Agent 외에 Sec-Fetch-*/Accept-Encoding 같은 헤더 유무도 본다)
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
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# 브라우저 흉내가 오히려 차단되는 사이트(일부 RSS 서버)를 위해, 403 이 나면 RSS 리더처럼 한 번 더 요청한다.
FALLBACK_HEADERS = {
    "User-Agent": "Feedly/1.0 (+http://www.feedly.com/fetcher.html; like FeedFetcher-Google)",
    "Accept": "application/rss+xml,application/atom+xml,application/xml,text/xml,text/html;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}

_session = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        s = requests.Session()
        retry = Retry(
            total=2,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        s.headers.update(HEADERS)
        _session = s
    return _session


def http_get(url: str) -> requests.Response:
    session = _get_session()
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    if resp.status_code in (401, 403, 406):
        alt = session.get(url, headers=FALLBACK_HEADERS, timeout=REQUEST_TIMEOUT)
        if alt.ok:
            resp = alt
    resp.raise_for_status()
    return resp


def describe_error(exc: Exception) -> str:
    """화면에 보여줄 짧은 오류 설명."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        hint = {403: " 접근 차단", 404: " 주소 없음(사이트 개편 가능성)", 429: " 요청 과다"}.get(code, "")
        return f"HTTP {code}{hint}"
    if isinstance(exc, requests.Timeout):
        return "응답 시간 초과"
    if isinstance(exc, requests.exceptions.SSLError):
        return "보안 연결(SSL) 실패"
    if isinstance(exc, requests.exceptions.ProxyError):
        return "프록시/방화벽에서 차단됨"
    if isinstance(exc, requests.ConnectionError):
        return "접속 실패"
    first_line = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {first_line[:120]}"
