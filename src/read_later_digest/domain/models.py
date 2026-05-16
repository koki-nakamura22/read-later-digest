from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(frozen=True)
class NotionArticle:
    page_id: str
    title: str
    url: str
    added_at: datetime
    age_days: int | None


class FetchFailureReason(StrEnum):
    INVALID_SCHEME = "invalid_scheme"
    BLOCKED_HOST = "blocked_host"
    TIMEOUT = "timeout"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    NETWORK = "network"
    EXTRACTION_EMPTY = "extraction_empty"


@dataclass(frozen=True)
class FetchResult:
    """Result of fetching and extracting article body from a URL.

    `text` is set only when `ok` is True; `reason` is set only when `ok` is False.
    """

    url: str
    ok: bool
    text: str | None
    reason: FetchFailureReason | None
    status_code: int | None


class ArticleType(StrEnum):
    ARTICLE = "記事"
    TECH = "技術"
    IDEA = "ネタ"
    WORK = "仕事"


class Priority(StrEnum):
    HIGH = "高"
    MID = "中"
    LOW = "低"


class ArticleSummary(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    summary_lines: list[str]
    key_points: list[str]
    type_: ArticleType | None = Field(default=None, alias="type")
    priority: Priority | None = None

    @field_validator("type_", mode="before")
    @classmethod
    def _coerce_type(cls, value: Any) -> ArticleType | None:
        if value is None or isinstance(value, ArticleType):
            return value
        if isinstance(value, str):
            try:
                return ArticleType(value)
            except ValueError:
                return None
        return None

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, value: Any) -> Priority | None:
        if value is None or isinstance(value, Priority):
            return value
        if isinstance(value, str):
            try:
                return Priority(value)
            except ValueError:
                return None
        return None


class ProcessStatus(StrEnum):
    SUCCESS = "success"
    FETCH_FAILED = "fetch_failed"
    LLM_FAILED = "llm_failed"


@dataclass(frozen=True)
class ProcessedArticle:
    """Per-article processing outcome, paired with summary or failure reason.

    Invariants (trusted, not validated):
    - status == SUCCESS  => summary is not None, error_reason is None
    - status != SUCCESS  => summary is None, error_reason is not None
    """

    article: NotionArticle
    status: ProcessStatus
    summary: ArticleSummary | None
    error_reason: str | None


@dataclass(frozen=True)
class Digest:
    """Aggregated processing outcome for one batch run.

    Invariants (caller's responsibility — not validated):
    - every element of `succeeded` has `status == ProcessStatus.SUCCESS`
      and a non-None `summary`
    - every element of `failed` has `status != ProcessStatus.SUCCESS`
      and a non-None `error_reason`
    """

    target_date: str
    succeeded: list[ProcessedArticle]
    failed: list[ProcessedArticle]


@dataclass(frozen=True)
class RenderedDigest:
    subject: str
    html: str
    text: str


@dataclass(frozen=True)
class RunResult:
    total_articles: int
    succeeded: int
    failed: int
    notification_sent: bool
    status_updated: int
    duration_ms: int


@dataclass(frozen=True)
class ReadLaterRunResult:
    """handler の return dict を組み立てるための集計値.

    digestkit RunResult が持たない `notification_sent` / `status_updated` /
    `duration_ms` を ReadLaterDigester 内で集計してこの dataclass に詰める.
    handler は `asdict(result)` で dict に変換して return する.
    """

    total_articles: int
    succeeded: int
    failed: int
    notification_sent: bool
    status_updated: int
    duration_ms: int
