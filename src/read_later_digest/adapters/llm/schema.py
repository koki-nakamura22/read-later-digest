from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from read_later_digest.domain.models import ArticleType, Priority


class SummaryPayload(BaseModel):
    """Pydantic model for the JSON payload returned by the LLM."""

    summary_lines: list[str] = Field(min_length=3, max_length=3)
    key_points: list[str] = Field(min_length=3, max_length=5)
    type: ArticleType | None = None
    priority: Priority | None = None

    @field_validator("summary_lines", "key_points")
    @classmethod
    def _strip_and_require_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [v.strip() for v in value]
        if any(v == "" for v in cleaned):
            raise ValueError("entries must be non-empty")
        return cleaned

    @field_validator("type", mode="before")
    @classmethod
    def _coerce_type(cls, value: Any) -> ArticleType | None:
        return _coerce_enum(value, ArticleType)

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, value: Any) -> Priority | None:
        return _coerce_enum(value, Priority)


def _coerce_enum[E: (ArticleType, Priority)](value: Any, enum_cls: type[E]) -> E | None:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)
        except ValueError:
            return None
    return None
