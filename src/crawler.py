import logging
import re
from calendar import timegm
from dataclasses import dataclass
from datetime import datetime, timezone
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

_DOTTED_DATE = re.compile(r"^\s*\d{1,2}\.\d{1,2}\.\d{2,4}")


@dataclass
class SourceResult:
    name: str
    count: int = 0
    error: Optional[str] = None


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
            # 04.03.2025 처럼 점으로 구분된 숫자 날짜는 유럽식(일.월.년)이다
            dt = dateparser.parse(value, dayfirst=bool(_DOTTED_DATE.match(value)))
        else:
            dt = datetime.fromtimestamp(timegm(value), tz=timezone.utc)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def _default_tag(source: Dict) -> str:
    return source.get("tag") or source["name"].split(" - ")[0].split(" (")[0].strip()


def crawl_rss(source: Dict) -> List[Article]:
    name = source["name"]
    resp = http_get(source["url"])
    feed = feedparser.parse(resp.content)
    if not feed.entries and feed.bozo:
        raise ValueError("RSS/Atom 피드 형식이 아닙니다 (URL 확인 필요)")

    articles: List[Article] = []
    for entry in feed.entries:
        title = clean_text(getattr(entry, "title", ""))
        link = (getattr(entry, "link", "") or "").strip()
        if not title or not link:
            continue
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
                excerpt=clean_text(getattr(entry, "summary", ""))[:500],
            )
        )
    return articles


def _date_from_element(el) -> Optional[str]:
    # <time datetime="2025-03-04"> 처럼 기계용 날짜 속성이 있으면 화면 표기보다 우선
    return _normalize_date(el.get("datetime") or el.get_text(strip=True))


def _articles_by_link_pattern(soup, page_url: str, pattern: str, make_article) -> List[Article]:
    """선택자로 기사를 못 찾았을 때, 주소에 pattern 이 들어간 긴 제목 링크들을 기사로 간주."""
    host = urlparse(page_url).netloc
    page = page_url.rstrip("/")
    articles = []
    for a in soup.find_all("a", href=True):
        link = urljoin(page_url, a["href"]).split("#")[0]
        parsed = urlparse(link)
        title = clean_text(a.get_text(" ", strip=True))
        if (
            parsed.netloc == host
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

    soup = BeautifulSoup(http_get(url).content, "lxml")

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


def crawl_source(source: Dict) -> List[Article]:
    src_type = source.get("type", "rss")
    if src_type == "rss":
        return crawl_rss(source)
    if src_type == "html":
        return crawl_html(source)
    raise ValueError(f"알 수 없는 소스 type: {src_type}")


def crawl_all(
    sources: List[Dict],
    only: Optional[List[str]] = None,
    report: Optional[List[SourceResult]] = None,
    progress: Optional[Callable[[int, int, str], None]] = None,
) -> List[Article]:
    """모든 사이트를 수집. 한 사이트가 실패해도 나머지는 계속 수집하고, 결과는 report 에 사이트별로 남긴다."""
    targets = [s for s in sources if s.get("enabled", True) and (not only or s.get("name") in only)]
    all_articles: List[Article] = []
    for i, source in enumerate(targets, start=1):
        name = source.get("name", "(이름 없음)")
        if progress:
            progress(i, len(targets), name)
        result = SourceResult(name=name)
        try:
            found = crawl_source(source)
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
        elif r.count == 0:
            lines.append(f"△ {r.name}: 0건 (주소나 선택자 확인 필요)")
        else:
            lines.append(f"✔ {r.name}: {r.count}건")
    return "\n".join(lines)
