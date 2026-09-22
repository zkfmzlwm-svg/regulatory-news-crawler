import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import List

from .crawler import crawl_all, load_sources, save_sources
from .fetcher import fetch_full_text
from .models import Article
from .storage import DEFAULT_DB_PATH, Storage
from .summarizer import SummaryEntry, load_format, summarize_article, write_summaries

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES_CONFIG = BASE_DIR / "config" / "sources.yaml"
DEFAULT_FORMAT_CONFIG = BASE_DIR / "config" / "summary_format.yaml"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _fmt_row(a: Article) -> str:
    check_mark = "[x]" if a.checked else "[ ]"
    sum_mark = "(요약됨)" if a.summarized else ""
    date = (a.published_at or "")[:10]
    return f"{check_mark} #{a.id:<4} {date:<10} {a.source:<32} {a.title} {sum_mark}"


def cmd_crawl(args):
    sources = load_sources(args.sources_config)
    store = Storage(Path(args.db))
    articles = crawl_all(sources, only=args.only)
    added = store.add_articles(articles)
    print(f"수집 완료: 총 {len(articles)}건 조회, 신규 {added}건 저장 (DB: {args.db})")


def cmd_sources(args):
    sources = load_sources(args.sources_config)
    for s in sources:
        status = "on " if s.get("enabled", True) else "off"
        print(f"[{status}] ({s.get('type', 'rss'):4}) {s['name']}  -  {s['url']}")


def cmd_add_source(args):
    sources = load_sources(args.sources_config)
    if any(s["name"] == args.name for s in sources):
        print(f"이미 같은 이름의 소스가 있습니다: {args.name}", file=sys.stderr)
        sys.exit(1)

    entry = {
        "name": args.name,
        "type": args.type,
        "url": args.url,
        "region": args.region or "Custom",
        "enabled": True,
    }
    if args.type == "html":
        entry["selectors"] = {
            k: v
            for k, v in {
                "item": args.item_selector,
                "title": args.title_selector,
                "link": args.link_selector,
                "date": args.date_selector,
                "summary": args.summary_selector,
            }.items()
            if v
        }
        if "item" not in entry["selectors"] or "title" not in entry["selectors"]:
            print("html 타입은 --item-selector 와 --title-selector 가 필요합니다.", file=sys.stderr)
            sys.exit(1)

    sources.append(entry)
    save_sources(args.sources_config, sources)
    print(f"소스를 추가했습니다: {args.name}")


def cmd_list(args):
    store = Storage(Path(args.db))
    articles = store.list_articles(
        source=args.source,
        keyword=args.keyword,
        checked_only=args.checked_only,
        unchecked_only=args.unchecked_only,
        limit=args.limit,
    )
    if not articles:
        print("조건에 맞는 기사가 없습니다. 먼저 `crawl` 을 실행하세요.")
        return
    for a in articles:
        print(_fmt_row(a))
    print(f"\n총 {len(articles)}건 표시됨. 체크하려면: python main.py check <번호 ...>")


def _parse_ids(id_args: List[str]) -> List[int]:
    ids: List[int] = []
    for token in id_args:
        for part in token.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = part.split("-", 1)
                ids.extend(range(int(start), int(end) + 1))
            else:
                ids.append(int(part))
    return ids


def cmd_check(args, checked: bool):
    store = Storage(Path(args.db))
    ids = _parse_ids(args.ids)
    n = store.set_checked(ids, checked=checked)
    verb = "체크" if checked else "체크 해제"
    print(f"{n}건 {verb} 완료: {ids}")


def cmd_summarize(args):
    store = Storage(Path(args.db))
    if args.ids:
        ids = _parse_ids(args.ids)
        articles = store.get_by_ids(ids)
    else:
        articles = store.list_articles(checked_only=True)

    if not articles:
        print("요약할 기사가 없습니다. 먼저 `check` 로 기사를 선택하세요.")
        return

    fmt = load_format(args.format_config)
    entries: List[SummaryEntry] = []
    for i, a in enumerate(articles, start=1):
        print(f"요약 중: [{a.tag or a.source}] {a.title}")
        full_text = fetch_full_text(a.url)
        title_kr, body = summarize_article(a, full_text, fmt)
        entries.append(SummaryEntry(index=i, article=a, title_kr=title_kr, body=body))

    if args.output:
        output_path = args.output
    else:
        ext = {"docx": "docx", "txt": "txt"}.get(fmt.output_format.lower(), "md")
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = str(DEFAULT_OUTPUT_DIR / f"summary_{ts}.{ext}")

    write_summaries(fmt, entries, output_path)
    store.set_summarized([a.id for a in articles], summarized=True)
    if not args.keep_checked:
        store.set_checked([a.id for a in articles], checked=False)
    print(f"\n요약 {len(entries)}건 저장 완료 -> {output_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="regulatory-news-crawler",
        description="해외 의약품 규제 뉴스 수집 및 요약 도구",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="SQLite DB 경로")
    sub = parser.add_subparsers(dest="command", required=True)

    p_crawl = sub.add_parser("crawl", help="설정된 사이트에서 기사 수집")
    p_crawl.add_argument("--sources-config", default=str(DEFAULT_SOURCES_CONFIG))
    p_crawl.add_argument("--only", nargs="*", help="특정 소스 이름만 수집")
    p_crawl.set_defaults(func=cmd_crawl)

    p_sources = sub.add_parser("sources", help="등록된 수집 대상 사이트 목록")
    p_sources.add_argument("--sources-config", default=str(DEFAULT_SOURCES_CONFIG))
    p_sources.set_defaults(func=cmd_sources)

    p_add = sub.add_parser("add-source", help="수집 대상 사이트 추가")
    p_add.add_argument("--sources-config", default=str(DEFAULT_SOURCES_CONFIG))
    p_add.add_argument("--name", required=True)
    p_add.add_argument("--url", required=True)
    p_add.add_argument("--type", choices=["rss", "html"], default="rss")
    p_add.add_argument("--region", default="")
    p_add.add_argument("--item-selector", help="(html) 기사 블록 CSS 선택자")
    p_add.add_argument("--title-selector", help="(html) 제목 CSS 선택자")
    p_add.add_argument("--link-selector", help="(html) 링크 CSS 선택자 (기본: title-selector)")
    p_add.add_argument("--date-selector", help="(html) 날짜 CSS 선택자")
    p_add.add_argument("--summary-selector", help="(html) 요약 CSS 선택자")
    p_add.set_defaults(func=cmd_add_source)

    p_list = sub.add_parser("list", help="수집된 기사 목록 조회")
    p_list.add_argument("--source")
    p_list.add_argument("--keyword")
    p_list.add_argument("--checked-only", action="store_true")
    p_list.add_argument("--unchecked-only", action="store_true")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_check = sub.add_parser("check", help="기사를 요약 대상으로 체크 (예: 1,2,5-8)")
    p_check.add_argument("ids", nargs="+")
    p_check.set_defaults(func=lambda a: cmd_check(a, True))

    p_uncheck = sub.add_parser("uncheck", help="기사 체크 해제")
    p_uncheck.add_argument("ids", nargs="+")
    p_uncheck.set_defaults(func=lambda a: cmd_check(a, False))

    p_summarize = sub.add_parser("summarize", help="체크된 기사를 지정 서식으로 요약")
    p_summarize.add_argument("--format-config", default=str(DEFAULT_FORMAT_CONFIG))
    p_summarize.add_argument("--ids", nargs="*", help="특정 기사 번호만 요약 (기본: 체크된 전체)")
    p_summarize.add_argument("--output", help="출력 파일 경로")
    p_summarize.add_argument(
        "--keep-checked", action="store_true", help="요약 후에도 체크 상태 유지"
    )
    p_summarize.set_defaults(func=cmd_summarize)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
