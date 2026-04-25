import os
from dataclasses import dataclass


def _resolve_secret(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"required secret '{name}' is not set")
    return value


@dataclass(frozen=True)
class Config:
    notion_db_id: str
    notion_token: str
    anthropic_api_key: str
    notion_status_property: str = "Status"
    notion_status_unread: str = "未読"
    llm_model: str = "claude-sonnet-4-6"
    llm_body_max_chars: int = 30_000
    llm_max_rate_limit_retries: int = 3
    llm_initial_backoff_sec: float = 1.0

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            notion_db_id=os.environ["NOTION_DB_ID"],
            notion_token=_resolve_secret("NOTION_TOKEN"),
            anthropic_api_key=_resolve_secret("ANTHROPIC_API_KEY"),
            notion_status_property=os.environ.get("NOTION_STATUS_PROPERTY", "Status"),
            notion_status_unread=os.environ.get("NOTION_STATUS_UNREAD", "未読"),
            llm_model=os.environ.get("LLM_MODEL", "claude-sonnet-4-6"),
            llm_body_max_chars=int(os.environ.get("LLM_BODY_MAX_CHARS", "30000")),
            llm_max_rate_limit_retries=int(os.environ.get("LLM_MAX_RATE_LIMIT_RETRIES", "3")),
            llm_initial_backoff_sec=float(os.environ.get("LLM_INITIAL_BACKOFF_SEC", "1.0")),
        )
