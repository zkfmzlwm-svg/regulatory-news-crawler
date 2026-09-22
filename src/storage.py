import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, List, Optional

from .models import Article

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "articles.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    region TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    published_at TEXT,
    excerpt TEXT,
    collected_at TEXT NOT NULL,
    checked INTEGER NOT NULL DEFAULT 0,
    summarized INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source);
CREATE INDEX IF NOT EXISTS idx_articles_checked ON articles(checked);
"""


class Storage:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def add_articles(self, articles: Iterable[Article]) -> int:
        """Insert new articles, skipping duplicates by URL. Returns number of new rows."""
        added = 0
        with self._connect() as conn:
            for a in articles:
                try:
                    conn.execute(
                        """INSERT INTO articles
                           (source, region, title, url, published_at, excerpt, collected_at, checked, summarized)
                           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)""",
                        (a.source, a.region, a.title, a.url, a.published_at, a.excerpt, a.collected_at),
                    )
                    added += 1
                except sqlite3.IntegrityError:
                    continue
        return added

    def list_articles(
        self,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        checked_only: bool = False,
        unchecked_only: bool = False,
        summarized: Optional[bool] = None,
        limit: Optional[int] = None,
    ) -> List[Article]:
        query = "SELECT * FROM articles WHERE 1=1"
        params: List = []
        if source:
            query += " AND source = ?"
            params.append(source)
        if keyword:
            query += " AND (title LIKE ? OR excerpt LIKE ?)"
            like = f"%{keyword}%"
            params.extend([like, like])
        if checked_only:
            query += " AND checked = 1"
        if unchecked_only:
            query += " AND checked = 0"
        if summarized is not None:
            query += " AND summarized = ?"
            params.append(1 if summarized else 0)
        query += " ORDER BY COALESCE(published_at, collected_at) DESC, id DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_article(r) for r in rows]

    def get_by_ids(self, ids: Iterable[int]) -> List[Article]:
        ids = list(ids)
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM articles WHERE id IN ({placeholders})", ids
            ).fetchall()
        by_id = {r["id"]: self._row_to_article(r) for r in rows}
        return [by_id[i] for i in ids if i in by_id]

    def set_checked(self, ids: Iterable[int], checked: bool = True) -> int:
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE articles SET checked = ? WHERE id IN ({placeholders})",
                [1 if checked else 0, *ids],
            )
        return cur.rowcount

    def set_summarized(self, ids: Iterable[int], summarized: bool = True) -> int:
        ids = list(ids)
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE articles SET summarized = ? WHERE id IN ({placeholders})",
                [1 if summarized else 0, *ids],
            )
        return cur.rowcount

    @staticmethod
    def _row_to_article(row: sqlite3.Row) -> Article:
        return Article(
            id=row["id"],
            source=row["source"],
            region=row["region"] or "",
            title=row["title"],
            url=row["url"],
            published_at=row["published_at"],
            excerpt=row["excerpt"] or "",
            collected_at=row["collected_at"],
            checked=bool(row["checked"]),
            summarized=bool(row["summarized"]),
        )
