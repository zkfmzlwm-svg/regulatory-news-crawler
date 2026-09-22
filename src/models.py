from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Article:
    id: Optional[int] = None
    source: str = ""
    region: str = ""
    tag: str = ""
    title: str = ""
    url: str = ""
    published_at: Optional[str] = None  # ISO 8601 string
    excerpt: str = ""
    collected_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    checked: bool = False
    summarized: bool = False
