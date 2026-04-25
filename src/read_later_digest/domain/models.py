from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class NotionArticle:
    page_id: str
    title: str
    url: str
    added_at: datetime
    age_days: int | None
