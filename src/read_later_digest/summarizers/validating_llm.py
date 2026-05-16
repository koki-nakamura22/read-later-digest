from __future__ import annotations

import re

from digestkit.summarizers import LLMSummarizer
from digestkit.types import Digest, Item
from pydantic import ValidationError

from read_later_digest.domain.models import ArticleSummary
from read_later_digest.exceptions import LLMError
from read_later_digest.logging_setup import logger


def _extract_json(raw: str) -> str:
    """先頭/末尾にプロンプト文がついた LLM 出力から JSON 部だけを寛容に取り出す."""
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m is None:
        raise LLMError("no JSON object found in LLM output")
    return m.group(0)


def parse_summary(digest: Digest) -> ArticleSummary:
    """Digest.summary (JSON 文字列) を ArticleSummary に復元.

    Sink / AckSource callback / Slack Formatter から呼ばれる単一エントリポイント.
    """
    return ArticleSummary.model_validate_json(_extract_json(digest.summary))


class ValidatingLLMSummarizer:
    """LLMSummarizer を所有し、スキーマ違反時の retry をかぶせる composition wrapper.

    継承ではなく composition なので、digestkit の LLMSummarizer 内部実装が変わっても影響を受けない.
    digestkit Summarizer Protocol を実装する.
    """

    def __init__(self, inner: LLMSummarizer, *, max_schema_retries: int = 1) -> None:
        self._inner = inner
        self._max_schema_retries = max_schema_retries

    def summarize(self, text: str, item: Item, *, length: str | None = None) -> Digest:
        last_err: Exception | None = None
        for attempt in range(self._max_schema_retries + 1):
            digest = self._inner.summarize(text, item)
            try:
                ArticleSummary.model_validate_json(_extract_json(digest.summary))
            except (ValidationError, LLMError) as e:
                last_err = e
                logger.warning(
                    "schema validation failed",
                    extra={"attempt": attempt + 1, "error": str(e), "item_id": item.id},
                )
                continue
            return digest
        raise LLMError(f"schema invalid after {self._max_schema_retries + 1} attempts: {last_err}")
