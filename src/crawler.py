import logging
from calendar import timegm
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urljoin

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from .models import Article

logger = logging.getLogger(__name__)

USER_AGENT = "regulatory-news-crawler/1.0 (+personal research tool)"
REQUEST_TIMEOUT = 20


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
            dt = dateparser.parse(value)
        else:
            dt = datetime.fromtimestamp(timegm(value), tz=timezone.utc)
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None


def crawl_rss(source: Dict) -> List[Article]:
    name = source["name"]
    url = source["url"]
    region = source.get("region", "")
    articles: List[Article] = []
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as exc:  # network/parse errors are per-source, don't abort whole crawl
        logger.warning("[%s] RSS 수집 실패: %s", name, exc)
        return articles

    for entry in feed.entries:
        title = getattr(entry, "title", "").strip()
        link = getattr(entry, "link", "").strip()
        if not title or not link:
            continue
        published = _normalize_date(
            getattr(entry, "published_parsed", None) or getattr(entry, "published", None)
            or getattr(entry, "updated_parsed", None) or getattr(entry, "updated", None)
        )
        excerpt = getattr(entry, "summary", "") or ""
        excerpt = BeautifulSoup(excerpt, "lxml").get_text(" ", strip=True)[:500]
        articles.append(
            Article(
                source=name,
                region=region,
                title=title,
                url=link,
                published_at=published,
                excerpt=excerpt,
            )
        )
    return articles


def crawl_html(source: Dict) -> List[Article]:
    name = source["name"]
    url = source["url"]
    region = source.get("region", "")
    selectors = source.get("selectors", {})
    item_sel = selectors.get("item")
    title_sel = selectors.get("title")
    link_sel = selectors.get("link", title_sel)
    date_sel = selectors.get("date")
    summary_sel = selectors.get("summary")
    articles: List[Article] = []

    if not item_sel or not title_sel:
        logger.warning("[%s] html 타입 소스는 selectors.item / selectors.title 이 필요합니다.", name)
        return articles

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "lxml")
    except Exception as exc:
        logger.warning("[%s] HTML 수집 실패: %s", name, exc)
        return articles

    for item in soup.select(item_sel):
        title_el = item.select_one(title_sel)
        if not title_el:
            continue
        title = title_el.get_text(" ", strip=True)

        link_el = item.select_one(link_sel) if link_sel else title_el
        href = link_el.get("href") if link_el else None
        if not href:
            continue
        link = urljoin(url, href)

        published = None
        if date_sel:
            date_el = item.select_one(date_sel)
            if date_el:
                published = _normalize_date(date_el.get_text(strip=True))

        excerpt = ""
        if summary_sel:
            summary_el = item.select_one(summary_sel)
            if summary_el:
                excerpt = summary_el.get_text(" ", strip=True)[:500]

        if not title:
            continue
        articles.append(
            Article(
                source=name,
                region=region,
                title=title,
                url=link,
                published_at=published,
                excerpt=excerpt,
            )
        )
    return articles


def crawl_source(source: Dict) -> List[Article]:
    if not source.get("enabled", True):
        return []
    src_type = source.get("type", "rss")
    if src_type == "rss":
        return crawl_rss(source)
    if src_type == "html":
        return crawl_html(source)
    logger.warning("[%s] 알 수 없는 소스 type: %s", source.get("name"), src_type)
    return []


def crawl_all(sources: List[Dict], only: Optional[List[str]] = None) -> List[Article]:
    all_articles: List[Article] = []
    for source in sources:
        if only and source.get("name") not in only:
            continue
        found = crawl_source(source)
        logger.info("[%s] %d건 수집", source.get("name"), len(found))
        all_articles.extend(found)
    return all_articles
