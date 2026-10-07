import logging
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import feedparser
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .models import Article
from .net import describe_error, http_get
from .utils import clean_text

logger = logging.getLogger(__name__)

# link_pattern 으로 기사 링크를 찾을 때, 메뉴/카테고리 링크를 거르기 위한 최소 제목 길이
MIN_FALLBACK_TITLE_LEN = 30

# 수집 대상: 수집 실행 시점 기준 최근 N일 이내 발행된 자료
RECENT_DAYS = 21

_DOTTED_DATE = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{2,4}")
# "Health product recall | 2026-10-06", "Updated: October 5, 2026" 처럼 날짜 앞뒤에 글자가 붙은 경우 날짜 부분만 꺼낸다
_DATE_IN_TEXT = re.compile(
    r"\d{4}-\d{1,2}-\d{1,2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?)?"
    r"|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}"
    r"|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}"
)
# WordPress 피드 요약문 끝에 붙는 "... Read more The post X appeared first on Y." 꼬리
_EXCERPT_TAIL = re.compile(r"\s*(?:\[(?:…|\.\.\.)\]|(?:…|\.\.\.)\s*Read more\b|The post .+ appeared first on ).*$", re.S)


@dataclass
class SourceResult:
    name: str
    count: int = 0
    skipped_old: int = 0
    error: Optional[str] = None
    oldest: Optional[str] = None  # 사이트가 준 목록 중 가장 오래된 발행일
    gap_from: Optional[str] = None  # 이 날짜 ~ oldest 사이 기사는 목록에 없어 놓쳤을 수 있음


def load_sources(config_path: str) -> List[Dict]:
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("sources", [])


def save_sources(config_path: str, sources: List[Dict]) -> None:
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"sources": sources}, f, allow_unicode=True, sort_keys=False)


def _normalize_date(value) -> Optional[str]:
    if not value:
        return None
    try:
        if isinstance(value, str):
            dt = _parse_date_text(value)
        else:
            dt = datetime.fromtimestamp(timegm(value), tz=timezone.utc)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _parse_date_text(value: str) -> Optional[datetime]:
    try:
        # 04.03.2025 처럼 점으로 구분된 숫자 날짜는 유럽식(일.월.년)이다
        return dateparser.parse(value, dayfirst=bool(_DOTTED_DATE.match(value)))
    except (ValueError, OverflowError):
        m = _DATE_IN_TEXT.search(value)
        if not m:
            raise
        return dateparser.parse(m.group(0))


def _clean_excerpt(text: str) -> str:
    return _EXCERPT_TAIL.sub("", clean_text(text)).strip()[:500]


def _default_tag(source: Dict) -> str:
    return source.get("tag") or source["name"].split(" - ")[0].split(" (")[0].strip()


def _discover_feed_url(html_bytes: bytes, page_url: str) -> Optional[str]:
    """HTML 페이지가 돌아왔을 때 <link rel="alternate" type="application/rss+xml"> 로 실제 피드 주소를 찾는다."""
    soup = BeautifulSoup(html_bytes, "lxml")
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        typ = (link.get("type") or "").lower()
        if "alternate" in rel and ("rss" in typ or "atom" in typ):
            return urljoin(page_url, link["href"])
    return None


def crawl_rss(source: Dict) -> List[Article]:
    name = source["name"]
    resp = http_get(source["url"])
    feed = feedparser.parse(resp.content)
    base_url = resp.url or source["url"]
    if not feed.entries:
        # 사이트 개편으로 피드 주소가 일반 웹페이지로 바뀐 경우, 페이지에 걸린 피드 주소를 따라간다
        feed_url = _discover_feed_url(resp.content, base_url)
        if feed_url and feed_url.rstrip("/") != base_url.rstrip("/"):
            logger.info("[%s] 피드 주소 자동 탐지: %s", name, feed_url)
            resp = http_get(feed_url)
            feed = feedparser.parse(resp.content)
            base_url = resp.url or feed_url
    if not feed.entries and feed.bozo:
        raise ValueError("RSS/Atom 피드 형식이 아닙니다 (URL 확인 필요)")

    articles: List[Article] = []
    for entry in feed.entries:
        title = clean_text(getattr(entry, "title", ""))
        link = (getattr(entry, "link", "") or "").strip()
        if not title or not link:
            continue
        link = urljoin(base_url, link)
        if link.startswith("http://"):
            link = "https://" + link[len("http://"):]
        published = _normalize_date(
            getattr(entry, "published_parsed", None) or getattr(entry, "published", None)
            or getattr(entry, "updated_parsed", None) or getattr(entry, "updated", None)
        )
        articles.append(
            Article(
                source=name,
                region=source.get("region", ""),
                tag=_default_tag(source),
                title=title,
                url=link,
                published_at=published,
                excerpt=_clean_excerpt(getattr(entry, "summary", "")),
            )
        )
    return articles


def _date_from_element(el) -> Optional[str]:
    # <time datetime="2025-03-04"> 처럼 기계용 날짜 속성이 있으면 화면 표기보다 우선
    return _normalize_date(el.get("datetime") or el.get_text(strip=True))


def _articles_by_link_pattern(soup, page_url: str, pattern: str, make_article) -> List[Article]:
    """선택자로 기사를 못 찾았을 때, 주소에 pattern 이 들어간 긴 제목 링크들을 기사로 간주."""
    host = urlparse(page_url).netloc.lower().removeprefix("www.")
    page = page_url.rstrip("/")
    articles = []
    for a in soup.find_all("a", href=True):
        link = urljoin(page_url, a["href"]).split("#")[0]
        parsed = urlparse(link)
        title = clean_text(a.get_text(" ", strip=True))
        if (
            parsed.netloc.lower().removeprefix("www.") == host
            and pattern in parsed.path
            and link.rstrip("/") != page
            and len(title) >= MIN_FALLBACK_TITLE_LEN
        ):
            articles.append(make_article(title, link, None, ""))
    return articles


def crawl_html(source: Dict) -> List[Article]:
    name = source["name"]
    url = source["url"]
    selectors = source.get("selectors", {})
    item_sel = selectors.get("item")
    title_sel = selectors.get("title")
    link_sel = selectors.get("link", title_sel)
    date_sel = selectors.get("date")
    summary_sel = selectors.get("summary")
    link_pattern = selectors.get("link_pattern")

    if not (item_sel and title_sel) and not link_pattern:
        raise ValueError("html 타입은 selectors.item/title 또는 selectors.link_pattern 이 필요합니다")

    resp = http_get(url)
    # www 유무 등으로 리다이렉트되면 최종 주소 기준으로 상대 링크·도메인 비교를 해야 기사를 놓치지 않는다
    url = resp.url or url
    soup = BeautifulSoup(resp.content, "lxml")

    def make_article(title, link, published, excerpt):
        return Article(
            source=name,
            region=source.get("region", ""),
            tag=_default_tag(source),
            title=title,
            url=link,
            published_at=published,
            excerpt=excerpt,
        )

    articles: List[Article] = []
    if item_sel and title_sel:
        for item in soup.select(item_sel):
            title_el = item.select_one(title_sel)
            if not title_el:
                continue
            title = clean_text(title_el.get_text(" ", strip=True))
            link_el = item.select_one(link_sel) if link_sel else title_el
            href = link_el.get("href") if link_el else None
            if not title or not href:
                continue

            published = None
            if date_sel:
                date_el = item.select_one(date_sel)
                if date_el:
                    published = _date_from_element(date_el)

            excerpt = ""
            if summary_sel:
                summary_el = item.select_one(summary_sel)
                if summary_el:
                    excerpt = clean_text(summary_el.get_text(" ", strip=True))[:500]

            articles.append(make_article(title, urljoin(url, href), published, excerpt))

    if not articles and link_pattern:
        articles = _articles_by_link_pattern(soup, url, link_pattern, make_article)

    unique = {}
    for a in articles:
        unique.setdefault(a.url, a)
    return list(unique.values())


def _dig(data, path: str):
    """"a.b.0.c" 같은 점 경로로 JSON 값을 꺼낸다."""
    for key in path.split(".") if path else []:
        if isinstance(data, list):
            data = data[int(key)] if key.isdigit() and int(key) < len(data) else None
        elif isinstance(data, dict):
            data = data.get(key)
        else:
            return None
    return data


def crawl_json(source: Dict) -> List[Article]:
    """RSS 가 없거나 멈춘 사이트의 JSON API (예: WHO 뉴스 API) 에서 기사 목록을 가져온다."""
    name = source["name"]
    url = source["url"]
    fields = source.get("fields", {})
    title_key = fields.get("title")
    link_key = fields.get("link")
    if not (title_key and link_key):
        raise ValueError("json 타입은 fields.title/link 가 필요합니다")
    link_prefix = source.get("link_prefix") or url

    resp = http_get(url)
    items = _dig(resp.json(), fields.get("items", ""))
    if not isinstance(items, list):
        raise ValueError("JSON 응답에서 기사 목록(fields.items)을 찾지 못했습니다")

    articles: List[Article] = []
    for item in items:
        title = clean_text(str(_dig(item, title_key) or ""))
        link = str(_dig(item, link_key) or "").strip()
        if not title or not link:
            continue
        if not link.startswith("http"):
            link = link_prefix.rstrip("/") + "/" + link.lstrip("/")
        date_key = fields.get("date")
        summary_key = fields.get("summary")
        articles.append(
            Article(
                source=name,
                region=source.get("region", ""),
                tag=_default_tag(source),
                title=title,
                url=link,
                published_at=_normalize_date(str(_dig(item, date_key) or "")) if date_key else None,
                excerpt=clean_text(str(_dig(item, summary_key) or ""))[:500] if summary_key else "",
            )
        )
    return articles


def _exclude_patterns(source: Dict) -> List[str]:
    value = source.get("exclude_link_pattern") or []
    return [value] if isinstance(value, str) else [str(v) for v in value]


def _crawl_page(source: Dict) -> List[Article]:
    src_type = source.get("type", "rss")
    if src_type == "rss":
        return crawl_rss(source)
    if src_type == "html":
        return crawl_html(source)
    if src_type == "json":
        return crawl_json(source)
    raise ValueError(f"알 수 없는 소스 type: {src_type}")


def crawl_source(source: Dict, cutoff: Optional[str] = None) -> List[Article]:
    """사이트 하나를 수집.

    url 에 {page} 가 있으면 pages 쪽수까지 넘겨가며 가져온다 (page_start: 첫 쪽 번호, 기본 1).
    목록 하나에 최근 몇 건만 나오는 사이트도 수집 기간(cutoff)까지 빠짐없이 모으기 위한 것으로,
    새 기사가 없는 쪽이 나오거나 cutoff 보다 오래된 기사가 나오면 멈춘다.
    """
    url = source["url"]
    pages = max(1, int(source.get("pages", 1))) if "{page}" in url else 1
    start = int(source.get("page_start", 1))
    articles: List[Article] = []
    seen = set()
    for page in range(start, start + pages):
        try:
            found = _crawl_page(dict(source, url=url.replace("{page}", str(page))))
        except Exception:
            if page == start:
                raise
            logger.info("[%s] %d쪽 수집 실패, 앞쪽까지만 사용", source.get("name"), page)
            break
        new = [a for a in found if a.url not in seen]
        if not new:
            break
        seen.update(a.url for a in new)
        articles.extend(new)
        dated = [a.published_at for a in new if a.published_at]
        if cutoff and dated and min(dated) < cutoff:
            break
    # 광고(/sponsored/) 등 주소에 특정 문자열이 든 기사는 수집하지 않는다
    patterns = _exclude_patterns(source)
    if patterns:
        articles = [a for a in articles if not any(p in a.url for p in patterns)]
    return articles


def crawl_all(
    sources: List[Dict],
    only: Optional[List[str]] = None,
    report: Optional[List[SourceResult]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
    max_age_days: Optional[int] = RECENT_DAYS,
    last_seen: Optional[Dict[str, str]] = None,
) -> List[Article]:
    """모든 사이트를 수집. 한 사이트가 실패해도 나머지는 계속 수집하고, 결과는 report 에 사이트별로 남긴다.

    max_age_days 일 이전에 발행된 기사는 제외한다 (None 이면 기간 제한 없음).
    발행일을 알 수 없는 기사는 판단할 수 없으므로 남겨둔다.
    last_seen 은 {사이트 이름: DB 에 이미 있는 가장 최근 발행일}. 사이트 목록이 최근 몇 건만 보여줘서
    지난 수집과 이번 수집 사이(또는 수집 기간 앞부분)가 비면 report 의 gap_from 에 표시한다.
    """
    cutoff = None
    if max_age_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    targets = [s for s in sources if s.get("enabled", True) and (not only or s.get("name") in only)]
    all_articles: List[Article] = []
    for i, source in enumerate(targets, start=1):
        name = source.get("name", "(이름 없음)")
        if progress:
            progress(i, len(targets), name)
        result = SourceResult(name=name)
        try:
            found = crawl_source(source, cutoff)
            dated = [a.published_at for a in found if a.published_at]
            result.oldest = min(dated) if dated else None
            if cutoff and result.oldest and result.oldest > cutoff:
                prev = (last_seen or {}).get(name)
                if not prev or prev < result.oldest:
                    result.gap_from = max(cutoff, prev or "")
            if cutoff:
                recent = [a for a in found if not a.published_at or a.published_at >= cutoff]
                result.skipped_old = len(found) - len(recent)
                found = recent
            result.count = len(found)
            all_articles.extend(found)
            logger.info("[%s] %d건 수집", name, len(found))
        except Exception as exc:
            result.error = describe_error(exc)
            logger.warning("[%s] 수집 실패: %s", name, exc)
        if report is not None:
            report.append(result)
    return all_articles


def format_report(report: List[SourceResult]) -> str:
    lines = []
    for r in report:
        if r.error:
            lines.append(f"✖ {r.name}: 실패 ({r.error})")
        elif r.count == 0 and r.skipped_old:
            lines.append(f"△ {r.name}: 기간 내 기사 없음 (기간 외 {r.skipped_old}건 제외)")
        elif r.count == 0:
            lines.append(f"△ {r.name}: 0건 (주소나 선택자 확인 필요)")
        else:
            extra = f" (기간 외 {r.skipped_old}건 제외)" if r.skipped_old else ""
            lines.append(f"✔ {r.name}: {r.count}건{extra}")
        if r.gap_from and not r.error:
            lines.append(
                f"   ⚠ 사이트 목록이 최근 기사만 보여줘 {_local_day(r.gap_from)} ~ {_local_day(r.oldest)} 사이 기사는"
                " 빠졌을 수 있음 (더 자주 수집하면 해결)"
            )
    return "\n".join(lines)


def _local_day(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone().strftime("%m-%d")
