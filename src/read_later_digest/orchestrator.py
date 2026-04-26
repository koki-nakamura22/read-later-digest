from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Protocol

from read_later_digest.adapters.mailer.base import Mailer
from read_later_digest.adapters.notifier.base import Notifier
from read_later_digest.config import NotifyGranularity
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
from read_later_digest.exceptions import LLMError, MailerError, NotifierError, NotionError
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

    Notification fan-out: at least one of `mailer` / `notifier` must be supplied;
    both may be set, in which case both transports receive the same digest.
    Ordering invariant: Notion writeback (summary / mark_processed / failure) only
    runs after every configured notification channel reports a successful send.
    If any channel raises, the batch aborts and Notion records remain untouched
    (next run will reprocess).
    """

    def __init__(
        self,
        *,
        notion: _NotionRepo,
        fetcher: _Fetcher,
        llm: _LLMClient,
        digest_builder: DigestBuilder,
        mailer: Mailer | None = None,
        mail_to: list[str] | None = None,
        notifier: Notifier | None = None,
        mail_granularity: NotifyGranularity = NotifyGranularity.DIGEST,
        notifier_granularity: NotifyGranularity = NotifyGranularity.DIGEST,
        llm_concurrency: int = 5,
        clock: Clock | None = None,
    ) -> None:
        if mailer is None and notifier is None:
            raise ValueError("at least one of mailer or notifier must be provided")
        if mailer is not None and not mail_to:
            raise ValueError("mail_to is required when mailer is configured")
        self._notion = notion
        self._fetcher = fetcher
        self._llm = llm
        self._mailer = mailer
        self._notifier = notifier
        self._digest_builder = digest_builder
        self._mail_to = mail_to or []
        self._mail_granularity = mail_granularity
        self._notifier_granularity = notifier_granularity
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

        notification_sent = await self._send_all(target_date, succeeded, failed, dry_run=dry_run)
        if not notification_sent:
            return self._make_result(articles, succeeded, failed, notification_sent, 0, started)

        status_updated = 0 if dry_run else await self._writeback(succeeded, failed)
        return self._make_result(
            articles, succeeded, failed, notification_sent, status_updated, started
        )

    def _render(
        self,
        target_date: str,
        succeeded: list[ProcessedArticle],
        failed: list[ProcessedArticle],
        granularity: NotifyGranularity,
    ) -> list[RenderedDigest]:
        """Build the list of messages to fan out for one channel's granularity.

        digest mode: always exactly one combined message (matches legacy behavior,
        including the empty-digest case where the run still sends one notification).
        per_article mode: one message per succeeded article, plus one aggregated
        failure summary when failures exist. When both lists are empty (no unread
        articles at all), still emit a single combined "empty digest" message so
        operators see a heartbeat — silence would be ambiguous with a broken job.
        """
        if granularity is NotifyGranularity.DIGEST:
            digest = Digest(target_date=target_date, succeeded=succeeded, failed=failed)
            return [self._digest_builder.build(digest)]

        if not succeeded and not failed:
            digest = Digest(target_date=target_date, succeeded=[], failed=[])
            return [self._digest_builder.build(digest)]

        total = len(succeeded)
        renderings: list[RenderedDigest] = [
            self._digest_builder.build_per_article(p, target_date=target_date, index=i, total=total)
            for i, p in enumerate(succeeded, start=1)
        ]
        if failed:
            renderings.append(
                self._digest_builder.build_failure_summary(failed, target_date=target_date)
            )
        return renderings

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

    async def _send_all(
        self,
        target_date: str,
        succeeded: list[ProcessedArticle],
        failed: list[ProcessedArticle],
        *,
        dry_run: bool,
    ) -> bool:
        """Render and fan out per channel, each at its own granularity.

        Each channel can have a different granularity (e.g. mail=digest while
        slack=per_article), so each transport gets its own renderings list.
        Mailer is processed before Notifier so the abort-then-skip-writeback
        invariant matches the legacy single-channel path: if mail fails, the
        slack send never happens; if any send raises, writeback never runs.
        Sequential rather than concurrent is deliberate — SES and Slack webhook
        rate limits are easy to trip with parallel fan-out.
        """
        if dry_run:
            logger.info(
                "dry-run: skipping notification send",
                extra={
                    "mail_granularity": self._mail_granularity.value,
                    "notifier_granularity": self._notifier_granularity.value,
                },
            )
            return False
        sent_any = False
        if self._mailer is not None:
            renderings = self._render(target_date, succeeded, failed, self._mail_granularity)
            for rendered in renderings:
                await self._send_via_mailer(rendered)
                sent_any = True
        if self._notifier is not None:
            renderings = self._render(target_date, succeeded, failed, self._notifier_granularity)
            for rendered in renderings:
                await self._send_via_notifier(rendered)
                sent_any = True
        return sent_any

    async def _send_via_mailer(self, rendered: RenderedDigest) -> None:
        assert self._mailer is not None
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

    async def _send_via_notifier(self, rendered: RenderedDigest) -> None:
        assert self._notifier is not None
        try:
            await self._notifier.send(
                subject=rendered.subject,
                text=rendered.text,
            )
        except NotifierError:
            logger.exception("notifier send failed; aborting batch")
            raise
        logger.info(
            "notifier sent",
            extra={"subject": rendered.subject},
        )

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
        notification_sent: bool,
        status_updated: int,
        started: float,
    ) -> RunResult:
        duration_ms = int((self._clock.monotonic() - started) * 1000)
        return RunResult(
            total_articles=len(articles),
            succeeded=len(succeeded),
            failed=len(failed),
            notification_sent=notification_sent,
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
