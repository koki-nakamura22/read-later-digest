import os
from dataclasses import dataclass
from importlib.metadata import version as _pkg_version

_DEFAULT_FETCH_USER_AGENT: str = (
    f"read-later-digest/{_pkg_version('read-later-digest')}"
    " (+https://github.com/koki-nakamura22/read-later-digest)"
)


def _resolve_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required secret '{name}' is not set")
    return value


@dataclass(frozen=True)
class Config:
    """Runtime configuration loaded from environment variables.

    Each attribute below is populated by :meth:`from_env` from a specific
    environment variable. Required vars raise ``RuntimeError`` at startup if
    missing; optional vars fall back to the documented default.
    """

    notion_db_id: str
    """Notion database ID for the read-later list. Source: ``NOTION_DB_ID`` (required)."""

    notion_token: str
    """Notion integration token used to authenticate the Notion API.
    Source: ``NOTION_TOKEN`` (required, secret)."""

    anthropic_api_key: str
    """Anthropic API key used to call Claude for summarization.
    Source: ``ANTHROPIC_API_KEY`` (required, secret)."""

    notion_status_property: str = "Status"
    """Name of the Notion property representing the read/processed status.
    Source: ``NOTION_STATUS_PROPERTY`` (optional, default: ``Status``)."""

    notion_status_unread: str = "未読"
    """Status value indicating an unread (to-be-processed) entry.
    Source: ``NOTION_STATUS_UNREAD`` (optional, default: ``未読``)."""

    notion_status_processed: str = "処理済み"
    """Status value written back after the entry has been digested.
    Source: ``NOTION_STATUS_PROCESSED`` (optional, default: ``処理済み``)."""

    notion_type_property: str = "Type"
    """Name of the Notion property representing the entry type/category.
    Source: ``NOTION_TYPE_PROPERTY`` (optional, default: ``Type``)."""

    notion_priority_property: str = "Priority"
    """Name of the Notion property representing the entry priority.
    Source: ``NOTION_PRIORITY_PROPERTY`` (optional, default: ``Priority``)."""

    llm_model: str = "claude-sonnet-4-6"
    """Claude model ID used for summarization.
    Source: ``LLM_MODEL`` (optional, default: ``claude-sonnet-4-6``)."""

    llm_body_max_chars: int = 30_000
    """Maximum number of characters of fetched body text passed to the LLM.
    Longer bodies are truncated to control token cost.
    Source: ``LLM_BODY_MAX_CHARS`` (optional, default: ``30000``)."""

    llm_max_rate_limit_retries: int = 3
    """Maximum retry count for LLM rate-limit (429) errors before giving up.
    Source: ``LLM_MAX_RATE_LIMIT_RETRIES`` (optional, default: ``3``)."""

    llm_initial_backoff_sec: float = 1.0
    """Initial backoff (seconds) for exponential retry on LLM rate-limit errors.
    Source: ``LLM_INITIAL_BACKOFF_SEC`` (optional, default: ``1.0``)."""

    llm_concurrency: int = 5
    """Maximum number of concurrent LLM requests during digest building.
    Source: ``LLM_CONCURRENCY`` (optional, default: ``5``)."""

    fetch_timeout_sec: float = 15.0
    """HTTP timeout (seconds) for fetching the URL body of each entry.
    Source: ``FETCH_TIMEOUT_SEC`` (optional, default: ``15.0``)."""

    slack_webhook_url: str | None = None
    """Slack Incoming Webhook URL for digest delivery.
    Source: ``SLACK_WEBHOOK_URL`` (secret)."""

    slack_timeout_sec: float = 10.0
    """HTTP timeout (seconds) for Slack webhook POST requests.
    Source: ``SLACK_TIMEOUT_SEC`` (optional, default: ``10.0``)."""

    fetch_user_agent: str = _DEFAULT_FETCH_USER_AGENT
    """HTTP User-Agent header for article fetch requests.
    Source: ``FETCH_USER_AGENT`` (optional, default: package version string)."""

    max_items_per_run: int = 30
    """Soft warning threshold for items fetched per run.

    Source: ``MAX_ITEMS_PER_RUN`` (optional, default: 30).
    Exceeding this triggers a WARN log; the run continues to process all
    items. Lambda timeout (15 min) typically caps actual throughput at
    ~30 items at 30s/item; excess items remain "未読" for next-day retry.
    """

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            notion_db_id=os.environ["NOTION_DB_ID"],
            notion_token=_resolve_secret("NOTION_TOKEN"),
            anthropic_api_key=_resolve_secret("ANTHROPIC_API_KEY"),
            notion_status_property=os.environ.get("NOTION_STATUS_PROPERTY", "Status"),
            notion_status_unread=os.environ.get("NOTION_STATUS_UNREAD", "未読"),
            notion_status_processed=os.environ.get("NOTION_STATUS_PROCESSED", "処理済み"),
            notion_type_property=os.environ.get("NOTION_TYPE_PROPERTY", "Type"),
            notion_priority_property=os.environ.get("NOTION_PRIORITY_PROPERTY", "Priority"),
            llm_model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
            llm_body_max_chars=int(os.environ.get("LLM_BODY_MAX_CHARS", "30000")),
            llm_max_rate_limit_retries=int(os.environ.get("LLM_MAX_RATE_LIMIT_RETRIES", "3")),
            llm_initial_backoff_sec=float(os.environ.get("LLM_INITIAL_BACKOFF_SEC", "1.0")),
            llm_concurrency=int(os.environ.get("LLM_CONCURRENCY", "5")),
            fetch_timeout_sec=float(os.environ.get("FETCH_TIMEOUT_SEC", "15.0")),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
            slack_timeout_sec=float(os.environ.get("SLACK_TIMEOUT_SEC", "10.0")),
            fetch_user_agent=os.environ.get("FETCH_USER_AGENT", _DEFAULT_FETCH_USER_AGENT),
            max_items_per_run=int(os.environ.get("MAX_ITEMS_PER_RUN", "30")),
        )
