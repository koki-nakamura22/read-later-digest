from typing import Protocol

from read_later_digest.domain.models import ArticleSummary


class LLMClient(Protocol):
    """Abstract LLM port for summarizing articles (see ADR-0004)."""

    async def summarize(self, *, title: str, body: str) -> ArticleSummary:
        """Generate a structured summary for a single article.

        Raises:
            LLMError: when summarization fails after retries.
        """
        ...
