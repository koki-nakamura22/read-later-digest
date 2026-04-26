from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

from read_later_digest.adapters.mailer.base import Mailer
from read_later_digest.domain.digest_builder import DigestBuilder
from read_later_digest.domain.models import (
    ArticleSummary,
    Digest,
    FetchResult,
    NotionArticle,
    ProcessedArticle,
    ProcessStatus,
    RenderedDigest,
    RunResult,
)
from read_later_digest.exceptions import LLMError, MailerError, NotionError
from read_later_digest.logging_setup import logger

JST = timezone(timedelta(hours=9))


class _Fetcher(Protocol):
    async def fetch(self, url: str) -> FetchResult: ...


class _LLMClient(Protocol):
    async def summarize(self, *, title: str, body: str) -> ArticleSummary: ...


class _NotionRepo(Protocol):
    async def list_unread(self) -> list[NotionArticle]: ...
    async def write_summary(self, page_id: str, summary: ArticleSummary) -> None: ...
    async def write_failure(self, page_id: str, reason: str) -> None: ...
    async def mark_processed(self, page_id: str) -> None: ...


class Orchestrator:
    """Coordinates the daily batch flow: list → fetch+summarize → digest → send → write back.

    Ordering invariant: Notion writeback (summary / mark_processed / failure) only runs
    after the mailer reports a successful send. If the mailer raises, the batch aborts
    and Notion records remain untouched (next run will reprocess).
    """

    def __init__(
        self,
        *,
        notion: _NotionRepo,
        fetcher: _Fetcher,
        llm: _LLMClient,
        mailer: Mailer,
        digest_builder: DigestBuilder,
        mail_to: list[str],
        llm_concurrency: int = 5,
        clock: Clock | None = None,
    ) -> None:
        self._notion = notion
        self._fetcher = fetcher
        self._llm = llm
        self._mailer = mailer
        self._digest_builder = digest_builder
        self._mail_to = mail_to
        self._sem = asyncio.Semaphore(max(1, llm_concurrency))
        self._clock = clock or _RealClock()

    async def run(self, *, dry_run: bool = False) -> RunResult:
        started = self._clock.monotonic()
        target_date = self._clock.now_jst().date().isoformat()

        articles = await self._notion.list_unread()
        logger.info("listed unread articles", extra={"count": len(articles)})

        processed = await self._process_articles(articles)
        succeeded = [p for p in processed if p.status is ProcessStatus.SUCCESS]
        failed = [p for p in processed if p.status is not ProcessStatus.SUCCESS]

        digest = Digest(target_date=target_date, succeeded=succeeded, failed=failed)
        rendered = self._digest_builder.build(digest)

        mail_sent = await self._send(rendered, dry_run=dry_run)
        if not mail_sent:
            return self._make_result(articles, succeeded, failed, mail_sent, 0, started)

        status_updated = 0 if dry_run else await self._writeback(succeeded, failed)
        return self._make_result(articles, succeeded, failed, mail_sent, status_updated, started)

    async def _process_articles(self, articles: list[NotionArticle]) -> list[ProcessedArticle]:
        if not articles:
            return []
        results = await asyncio.gather(
            *(self._process_one(a) for a in articles), return_exceptions=False
        )
        return list(results)

    async def _process_one(self, article: NotionArticle) -> ProcessedArticle:
        async with self._sem:
            fetch = await self._fetcher.fetch(article.url)
            if not fetch.ok or fetch.text is None:
                reason = str(fetch.reason) if fetch.reason is not None else "fetch_failed"
                logger.warning(
                    "article fetch failed",
                    extra={"page_id": article.page_id, "reason": reason},
                )
                return ProcessedArticle(
                    article=article,
                    status=ProcessStatus.FETCH_FAILED,
                    summary=None,
                    error_reason=reason,
                )
            try:
                summary = await self._llm.summarize(title=article.title, body=fetch.text)
            except LLMError as e:
                logger.warning(
                    "article summarization failed",
                    extra={"page_id": article.page_id, "error": str(e)},
                )
                return ProcessedArticle(
                    article=article,
                    status=ProcessStatus.LLM_FAILED,
                    summary=None,
                    error_reason=str(e),
                )
            return ProcessedArticle(
                article=article,
                status=ProcessStatus.SUCCESS,
                summary=summary,
                error_reason=None,
            )

    async def _send(self, rendered: RenderedDigest, *, dry_run: bool) -> bool:
        if dry_run:
            logger.info("dry-run: skipping mail send", extra={"subject": rendered.subject})
            return False
        try:
            await self._mailer.send(
                to=self._mail_to,
                subject=rendered.subject,
                html=rendered.html,
                text=rendered.text,
            )
        except MailerError:
            logger.exception("mail send failed; aborting batch")
            raise
        logger.info(
            "mail sent",
            extra={"subject": rendered.subject, "to_count": len(self._mail_to)},
        )
        return True

    async def _writeback(
        self,
        succeeded: list[ProcessedArticle],
        failed: list[ProcessedArticle],
    ) -> int:
        status_updated = 0
        for processed in succeeded:
            assert processed.summary is not None  # invariant
            try:
                await self._notion.write_summary(processed.article.page_id, processed.summary)
                await self._notion.mark_processed(processed.article.page_id)
            except NotionError:
                logger.exception(
                    "notion writeback failed for succeeded article",
                    extra={"page_id": processed.article.page_id},
                )
                continue
            status_updated += 1

        for processed in failed:
            reason = processed.error_reason or "unknown"
            try:
                await self._notion.write_failure(processed.article.page_id, reason)
            except NotionError:
                logger.exception(
                    "notion writeback failed for failed article",
                    extra={"page_id": processed.article.page_id},
                )
        return status_updated

    def _make_result(
        self,
        articles: list[NotionArticle],
        succeeded: list[ProcessedArticle],
        failed: list[ProcessedArticle],
        mail_sent: bool,
        status_updated: int,
        started: float,
    ) -> RunResult:
        duration_ms = int((self._clock.monotonic() - started) * 1000)
        return RunResult(
            total_articles=len(articles),
            succeeded=len(succeeded),
            failed=len(failed),
            mail_sent=mail_sent,
            status_updated=status_updated,
            duration_ms=duration_ms,
        )


class Clock(Protocol):
    def now_jst(self) -> datetime: ...

    def monotonic(self) -> float: ...


class _RealClock:
    def now_jst(self) -> datetime:
        return datetime.now(tz=UTC).astimezone(JST)

    def monotonic(self) -> float:
        return time.monotonic()
