import requests

REQUEST_TIMEOUT = 20

# 봇처럼 보이는 User-Agent 는 FDA 등 일부 기관 사이트에서 403 으로 차단되므로 일반 브라우저처럼 요청한다.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def http_get(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp


def describe_error(exc: Exception) -> str:
    """화면에 보여줄 짧은 오류 설명."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, requests.Timeout):
        return "응답 시간 초과"
    if isinstance(exc, requests.ConnectionError):
        return "접속 실패"
    first_line = (str(exc).strip().splitlines() or [""])[0]
    return f"{type(exc).__name__}: {first_line[:120]}"
