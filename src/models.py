from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Article:
    id: Optional[int] = None
    source: str = ""
    region: str = ""
    tag: str = ""
    title: str = ""
    title_ko: str = ""  # 목록 화면용 제목 단순 번역 (번역 전이면 빈 값)
    url: str = ""
    published_at: Optional[str] = None  # ISO 8601 string
    excerpt: str = ""
    collected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    checked: bool = False
    summarized: bool = False
