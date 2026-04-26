import os
from dataclasses import dataclass, field
from enum import StrEnum


class NotificationChannel(StrEnum):
    """Notification delivery channel selectable via NOTIFY_CHANNELS."""

    MAIL = "mail"
    SLACK = "slack"


class NotifyGranularity(StrEnum):
    """Notification fan-out granularity selectable via NOTIFY_GRANULARITY.

    `digest` (default) sends one combined message per channel per run, matching
    the historical behavior. `per_article` sends one message per successfully
    summarized article (plus, when applicable, one aggregated failure summary).
    """

    DIGEST = "digest"
    PER_ARTICLE = "per_article"


def _parse_notify_granularity(raw: str) -> NotifyGranularity:
    """Parse NOTIFY_GRANULARITY into the enum, raising on unknown values."""
    token = raw.strip().lower()
    if not token:
        raise RuntimeError("required env 'NOTIFY_GRANULARITY' is empty")
    valid = {g.value for g in NotifyGranularity}
    if token not in valid:
        raise RuntimeError(f"unknown NOTIFY_GRANULARITY value {token!r} (valid: {sorted(valid)})")
    return NotifyGranularity(token)


def _resolve_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required secret '{name}' is not set")
    return value


def _parse_mail_to(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def _parse_notification_channels(raw: str) -> frozenset[NotificationChannel]:
    """Parse a CSV like 'mail,slack' into a non-empty channel set.

    Whitespace is trimmed and case is normalized to lower-case.
    Raises RuntimeError on empty input or unknown channel names so
    misconfiguration fails at startup rather than silently dropping notifications.
    """
    tokens = [t.strip().lower() for t in raw.split(",") if t.strip()]
    if not tokens:
        raise RuntimeError("required env 'NOTIFY_CHANNELS' is empty")

    valid = {c.value for c in NotificationChannel}
    unknown = sorted({t for t in tokens if t not in valid})
    if unknown:
        raise RuntimeError(
            f"unknown notification channels in NOTIFY_CHANNELS: {unknown} (valid: {sorted(valid)})"
        )
    return frozenset(NotificationChannel(t) for t in tokens)


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

    notification_channels: frozenset[NotificationChannel]
    """Enabled notification delivery channels. Parsed from a CSV like ``mail,slack``.
    Source: ``NOTIFY_CHANNELS`` (optional, default: ``mail``)."""

    notify_granularity: NotifyGranularity = NotifyGranularity.DIGEST
    """Notification fan-out granularity. ``digest`` sends one combined message
    per channel per run (legacy behavior). ``per_article`` sends one message per
    successfully summarized article (plus an aggregated failure summary when
    failures exist). Applies uniformly across every enabled channel.
    Source: ``NOTIFY_GRANULARITY`` (optional, default: ``digest``)."""

    mail_from: str = ""
    """Sender email address for digest mails. Required only when the ``mail``
    channel is enabled. Source: ``MAIL_FROM``."""

    mail_to: list[str] = field(default_factory=list)
    """Recipient email addresses for digest mails (CSV is split by comma).
    Required only when the ``mail`` channel is enabled. Source: ``MAIL_TO``."""

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

    aws_region: str = "ap-northeast-1"
    """AWS region used for SES (mail) and other AWS clients.
    Source: ``AWS_REGION`` (optional, default: ``ap-northeast-1``)."""

    slack_webhook_url: str | None = None
    """Slack Incoming Webhook URL for digest delivery. Required only when the
    ``slack`` channel is enabled. Source: ``SLACK_WEBHOOK_URL`` (secret)."""

    slack_timeout_sec: float = 10.0
    """HTTP timeout (seconds) for Slack webhook POST requests.
    Source: ``SLACK_TIMEOUT_SEC`` (optional, default: ``10.0``)."""

    @classmethod
    def from_env(cls) -> "Config":
        channels = _parse_notification_channels(os.environ.get("NOTIFY_CHANNELS", "mail"))
        granularity = _parse_notify_granularity(os.environ.get("NOTIFY_GRANULARITY", "digest"))

        # Each channel's transport-specific env vars are required only when that
        # channel is enabled, so a slack-only deployment doesn't need MAIL_*
        # and a mail-only deployment doesn't need SLACK_WEBHOOK_URL.
        mail_from = ""
        mail_to: list[str] = []
        if NotificationChannel.MAIL in channels:
            mail_from = os.environ.get("MAIL_FROM", "")
            if not mail_from:
                raise RuntimeError(
                    "channel 'mail' is enabled but required env 'MAIL_FROM' is not set"
                )
            mail_to = _parse_mail_to(os.environ.get("MAIL_TO", ""))
            if not mail_to:
                raise RuntimeError("channel 'mail' is enabled but required env 'MAIL_TO' is empty")

        slack_webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or None
        if NotificationChannel.SLACK in channels and not slack_webhook_url:
            raise RuntimeError(
                "channel 'slack' is enabled but required env 'SLACK_WEBHOOK_URL' is not set"
            )

        return cls(
            notion_db_id=os.environ["NOTION_DB_ID"],
            notion_token=_resolve_secret("NOTION_TOKEN"),
            anthropic_api_key=_resolve_secret("ANTHROPIC_API_KEY"),
            notification_channels=channels,
            notify_granularity=granularity,
            mail_from=mail_from,
            mail_to=mail_to,
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
            aws_region=os.environ.get("AWS_REGION", "ap-northeast-1"),
            slack_webhook_url=slack_webhook_url,
            slack_timeout_sec=float(os.environ.get("SLACK_TIMEOUT_SEC", "10.0")),
        )
