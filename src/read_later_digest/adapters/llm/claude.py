from __future__ import annotations

import asyncio
import re
from typing import Any, Protocol

from pydantic import ValidationError

from read_later_digest.adapters.llm.schema import SummaryPayload
from read_later_digest.domain.models import ArticleSummary
from read_later_digest.exceptions import LLMError
from read_later_digest.logging_setup import logger

try:
    from anthropic import RateLimitError as _AnthropicRateLimitError
except ImportError:  # pragma: no cover

    class _AnthropicRateLimitError(Exception):  # type: ignore[no-redef]
        """Sentinel used when the anthropic SDK is unavailable."""


_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_MAX_OUTPUT_TOKENS = 1024
_SYSTEM_PROMPT = """\
あなたは日本語の記事要約アシスタントです。与えられた記事タイトルと本文から、
ダイジェストメール用に下記の JSON のみを出力してください。説明文・前置き・後置き・
コードフェンスは禁止です。

スキーマ:
{
  "summary_lines": ["1 文目", "2 文目", "3 文目"],
  "key_points": ["要点1", "要点2", "要点3"],
  "type": "記事" | "技術" | "ネタ" | "仕事" | null,
  "priority": "高" | "中" | "低" | null
}

制約:
- summary_lines は必ず 3 要素。
- key_points は 3〜5 要素。各要素は簡潔な日本語の体言止めまたは短い文。
- type / priority が判定不能なら null。
"""


class _MessagesAPI(Protocol):
    async def create(self, **kwargs: Any) -> Any: ...


class _AnthropicLike(Protocol):
    @property
    def messages(self) -> _MessagesAPI: ...


class ClaudeLLMClient:
    """Claude (Anthropic) implementation of the LLMClient port."""

    def __init__(
        self,
        *,
        client: _AnthropicLike,
        model: str = _DEFAULT_MODEL,
        body_max_chars: int = 30_000,
        max_rate_limit_retries: int = 3,
        initial_backoff_sec: float = 1.0,
        max_output_tokens: int = _DEFAULT_MAX_OUTPUT_TOKENS,
    ) -> None:
        self._client = client
        self._model = model
        self._body_max_chars = body_max_chars
        self._max_rate_limit_retries = max_rate_limit_retries
        self._initial_backoff_sec = initial_backoff_sec
        self._max_output_tokens = max_output_tokens

    async def summarize(self, *, title: str, body: str) -> ArticleSummary:
        truncated = body[: self._body_max_chars]
        user_message = f"# タイトル\n{title}\n\n# 本文\n{truncated}"

        last_validation_error: ValidationError | None = None
        for attempt in range(2):
            raw = await self._call_with_rate_limit_retry(user_message)
            try:
                payload = SummaryPayload.model_validate_json(_extract_json(raw))
            except (ValidationError, ValueError) as e:
                last_validation_error = e if isinstance(e, ValidationError) else None
                logger.warning(
                    "llm output schema invalid",
                    extra={"attempt": attempt + 1, "error": str(e)},
                )
                continue
            return ArticleSummary(
                summary_lines=payload.summary_lines,
                key_points=payload.key_points,
                type_=payload.type,
                priority=payload.priority,
            )

        raise LLMError(f"llm output schema invalid after retry: {last_validation_error}")

    async def _call_with_rate_limit_retry(self, user_message: str) -> str:
        delay = self._initial_backoff_sec
        for attempt in range(self._max_rate_limit_retries + 1):
            try:
                response = await self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_output_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": _SYSTEM_PROMPT,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user_message}],
                )
            except _AnthropicRateLimitError as e:
                if attempt >= self._max_rate_limit_retries:
                    raise LLMError(f"llm rate limit retries exhausted: {e}") from e
                logger.warning(
                    "llm rate limited; retrying",
                    extra={"attempt": attempt + 1, "delay_sec": delay},
                )
                await asyncio.sleep(delay)
                delay *= 2
                continue
            except Exception as e:
                raise LLMError(f"llm call failed: {e}") from e

            _log_usage(response)
            return _extract_text(response)
        raise LLMError("unreachable: rate limit retry loop exited without return")


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if not content:
        raise LLMError("llm response had empty content")
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    if not parts:
        raise LLMError("llm response had no text blocks")
    return "".join(parts)


def _extract_json(raw: str) -> str:
    """Pull a JSON object out of the raw LLM text, tolerating leading prose."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match is None:
        raise ValueError("no json object found in llm response")
    return match.group(0)


def _log_usage(response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    logger.info(
        "llm usage",
        extra={
            "usage_input_tokens": getattr(usage, "input_tokens", None),
            "usage_output_tokens": getattr(usage, "output_tokens", None),
        },
    )
