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
    notion_status_property: str = "Status"
    notion_status_unread: str = "未読"

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            notion_db_id=os.environ["NOTION_DB_ID"],
            notion_token=_resolve_secret("NOTION_TOKEN"),
            notion_status_property=os.environ.get("NOTION_STATUS_PROPERTY", "Status"),
            notion_status_unread=os.environ.get("NOTION_STATUS_UNREAD", "未読"),
        )
