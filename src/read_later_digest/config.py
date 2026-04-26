import os
from dataclasses import dataclass


def _resolve_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required secret '{name}' is not set")
    return value


def _parse_mail_to(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Config:
    notion_db_id: str
    notion_token: str
    anthropic_api_key: str
    mail_from: str
    mail_to: list[str]
    notion_status_property: str = "Status"
    notion_status_unread: str = "未読"
    notion_status_processed: str = "処理済み"
    notion_type_property: str = "Type"
    notion_priority_property: str = "Priority"
    llm_model: str = "claude-sonnet-4-6"
    llm_body_max_chars: int = 30_000
    llm_max_rate_limit_retries: int = 3
    llm_initial_backoff_sec: float = 1.0
    llm_concurrency: int = 5
    fetch_timeout_sec: float = 15.0
    aws_region: str = "ap-northeast-1"
    slack_webhook_url: str | None = None
    slack_timeout_sec: float = 10.0

    @classmethod
    def from_env(cls) -> "Config":
        mail_from = os.environ.get("MAIL_FROM", "")
        if not mail_from:
            raise RuntimeError("required env 'MAIL_FROM' is not set")
        mail_to = _parse_mail_to(os.environ.get("MAIL_TO", ""))
        if not mail_to:
            raise RuntimeError("required env 'MAIL_TO' is empty")
        return cls(
            notion_db_id=os.environ["NOTION_DB_ID"],
            notion_token=_resolve_secret("NOTION_TOKEN"),
            anthropic_api_key=_resolve_secret("ANTHROPIC_API_KEY"),
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
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
            slack_timeout_sec=float(os.environ.get("SLACK_TIMEOUT_SEC", "10.0")),
        )
