from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from read_later_digest.config import NotifyGranularity
from read_later_digest.domain.digest_builder import DigestBuilder
from read_later_digest.domain.models import (
    ArticleSummary,
    ArticleType,
    FetchFailureReason,
    FetchResult,
    NotionArticle,
    Priority,
)
from read_later_digest.exceptions import LLMError, MailerError, NotifierError, NotionError
from read_later_digest.orchestrator import Orchestrator

# ---------- Test doubles ----------


class _FakeFetcher:
    def __init__(self, responses: dict[str, FetchResult]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        return self.responses[url]


class _FakeLLM:
    def __init__(
        self,
        responses: dict[str, ArticleSummary | Exception],
    ) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def summarize(self, *, title: str, body: str) -> ArticleSummary:
        self.calls.append(title)
        result = self.responses[title]
        if isinstance(result, Exception):
            raise result
        return result


class _FakeMailer:
    def __init__(self, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    async def send(self, *, to: list[str], subject: str, html: str, text: str) -> None:
        self.calls.append({"to": to, "subject": subject})
        if self.raise_error:
            raise MailerError("ses send failed")


class _FakeNotifier:
    def __init__(self, raise_error: bool = False) -> None:
        self.raise_error = raise_error
        self.calls: list[dict[str, Any]] = []

    async def send(self, *, subject: str, text: str) -> None:
        self.calls.append({"subject": subject, "text": text})
        if self.raise_error:
            raise NotifierError("slack webhook failed")


class _FakeNotion:
    def __init__(
        self,
        articles: list[NotionArticle],
        *,
        write_summary_fail_for: set[str] | None = None,
        mark_processed_fail_for: set[str] | None = None,
    ) -> None:
        self._articles = articles
        self._write_summary_fail = write_summary_fail_for or set()
        self._mark_processed_fail = mark_processed_fail_for or set()
        self.summary_writes: list[tuple[str, ArticleSummary]] = []
        self.failure_writes: list[tuple[str, str]] = []
        self.marked: list[str] = []

    async def list_unread(self) -> list[NotionArticle]:
        return list(self._articles)

    async def write_summary(self, page_id: str, summary: ArticleSummary) -> None:
        if page_id in self._write_summary_fail:
            raise NotionError("write_summary failed")
        self.summary_writes.append((page_id, summary))

    async def write_failure(self, page_id: str, reason: str) -> None:
        self.failure_writes.append((page_id, reason))

    async def mark_processed(self, page_id: str) -> None:
        if page_id in self._mark_processed_fail:
            raise NotionError("mark_processed failed")
        self.marked.append(page_id)


class _FixedClock:
    def __init__(self, now_iso: str) -> None:
        self._now = datetime.fromisoformat(now_iso)
        self._t = 0.0

    def now_jst(self) -> datetime:
        return self._now

    def monotonic(self) -> float:
        self._t += 0.001
        return self._t


# ---------- Helpers ----------


def _article(page_id: str, title: str = "T", url: str | None = None) -> NotionArticle:
    return NotionArticle(
        page_id=page_id,
        title=title,
        url=url or f"https://example.com/{page_id}",
        added_at=datetime.fromisoformat("2026-04-20T00:00:00+09:00"),
        age_days=0,
    )


def _summary() -> ArticleSummary:
    return ArticleSummary(
        summary_lines=["s1", "s2", "s3"],
        key_points=["k1", "k2"],
        type_=ArticleType.TECH,
        priority=Priority.HIGH,
    )


def _ok_fetch(url: str = "https://example.com/x") -> FetchResult:
    return FetchResult(url=url, ok=True, text="body", reason=None, status_code=200)


def _fail_fetch(url: str, reason: FetchFailureReason) -> FetchResult:
    return FetchResult(url=url, ok=False, text=None, reason=reason, status_code=None)


def _build_orchestrator(
    *,
    notion: _FakeNotion,
    fetcher: _FakeFetcher,
    llm: _FakeLLM,
    mailer: _FakeMailer | None = None,
    notifier: _FakeNotifier | None = None,
    notify_granularity: NotifyGranularity = NotifyGranularity.DIGEST,
    llm_concurrency: int = 5,
) -> Orchestrator:
    return Orchestrator(
        notion=notion,
        fetcher=fetcher,
        llm=llm,
        digest_builder=DigestBuilder(),
        mailer=mailer,
        mail_to=["me@example.com"] if mailer is not None else None,
        notifier=notifier,
        notify_granularity=notify_granularity,
        llm_concurrency=llm_concurrency,
        clock=_FixedClock("2026-04-26T07:00:00+09:00"),
    )


# ---------- Tests ----------


async def test_run_zero_articles_sends_empty_digest_and_returns_zero_counts() -> None:
    notion = _FakeNotion(articles=[])
    mailer = _FakeMailer()
    orch = _build_orchestrator(
        notion=notion,
        fetcher=_FakeFetcher({}),
        llm=_FakeLLM({}),
        mailer=mailer,
    )

    result = await orch.run()

    assert result.total_articles == 0
    assert result.succeeded == 0
    assert result.failed == 0
    assert result.notification_sent is True
    assert result.status_updated == 0
    assert len(mailer.calls) == 1, "0-article runs still send a digest mail"
    assert notion.summary_writes == []
    assert notion.marked == []


async def test_run_all_succeeded_writes_summary_and_marks_processed() -> None:
    a1 = _article("p1", title="A1")
    a2 = _article("p2", title="A2")
    notion = _FakeNotion(articles=[a1, a2])
    fetcher = _FakeFetcher({a1.url: _ok_fetch(), a2.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary(), "A2": _summary()})
    mailer = _FakeMailer()

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, mailer=mailer)
    result = await orch.run()

    assert result.succeeded == 2
    assert result.failed == 0
    assert result.notification_sent is True
    assert result.status_updated == 2
    assert {pid for pid, _ in notion.summary_writes} == {"p1", "p2"}
    assert set(notion.marked) == {"p1", "p2"}
    assert notion.failure_writes == []


async def test_run_partial_failure_only_marks_succeeded_and_writes_failure_for_others() -> None:
    a1 = _article("p1", title="A1")  # fetch fail
    a2 = _article("p2", title="A2")  # llm fail
    a3 = _article("p3", title="A3")  # success
    notion = _FakeNotion(articles=[a1, a2, a3])
    fetcher = _FakeFetcher(
        {
            a1.url: _fail_fetch(a1.url, FetchFailureReason.TIMEOUT),
            a2.url: _ok_fetch(a2.url),
            a3.url: _ok_fetch(a3.url),
        }
    )
    llm = _FakeLLM({"A2": LLMError("schema invalid"), "A3": _summary()})
    mailer = _FakeMailer()

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, mailer=mailer)
    result = await orch.run()

    assert result.total_articles == 3
    assert result.succeeded == 1
    assert result.failed == 2
    assert result.status_updated == 1
    assert notion.marked == ["p3"]
    assert {pid for pid, _ in notion.failure_writes} == {"p1", "p2"}


async def test_run_mailer_failure_aborts_before_any_writeback() -> None:
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    mailer = _FakeMailer(raise_error=True)

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, mailer=mailer)

    with pytest.raises(MailerError):
        await orch.run()

    assert notion.summary_writes == [], "writeback must not run after mail failure"
    assert notion.marked == []
    assert notion.failure_writes == []


async def test_run_dry_run_skips_send_and_writeback() -> None:
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    mailer = _FakeMailer()

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, mailer=mailer)
    result = await orch.run(dry_run=True)

    assert result.notification_sent is False
    assert result.status_updated == 0
    assert mailer.calls == []
    assert notion.marked == []


async def test_run_writeback_failure_does_not_abort_remaining_articles() -> None:
    a1 = _article("p1", title="A1")
    a2 = _article("p2", title="A2")
    notion = _FakeNotion(
        articles=[a1, a2],
        mark_processed_fail_for={"p1"},
    )
    fetcher = _FakeFetcher({a1.url: _ok_fetch(), a2.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary(), "A2": _summary()})
    mailer = _FakeMailer()

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, mailer=mailer)
    result = await orch.run()

    assert result.succeeded == 2
    assert result.status_updated == 1
    assert notion.marked == ["p2"]


async def test_run_emits_run_result_with_duration_ms() -> None:
    notion = _FakeNotion(articles=[])
    orch = _build_orchestrator(
        notion=notion,
        fetcher=_FakeFetcher({}),
        llm=_FakeLLM({}),
        mailer=_FakeMailer(),
    )
    result = await orch.run()
    assert result.duration_ms >= 0


# ---------- typing safeguard: DigestBuilder receives a RenderedDigest-shaped output ----------


async def test_digest_builder_output_used_for_mailer_arguments() -> None:
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    mailer = _FakeMailer()

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, mailer=mailer)
    await orch.run()

    sent = mailer.calls[0]
    assert sent["to"] == ["me@example.com"]
    assert isinstance(sent["subject"], str) and sent["subject"]


# ---------- Multi-channel notification routing ----------


async def test_run_slack_only_sends_via_notifier_and_writes_back() -> None:
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    notifier = _FakeNotifier()

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, notifier=notifier)
    result = await orch.run()

    assert result.notification_sent is True
    assert result.status_updated == 1
    assert len(notifier.calls) == 1
    sent = notifier.calls[0]
    assert sent["subject"]
    assert sent["text"]


async def test_run_mail_and_slack_both_receive_the_digest() -> None:
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    mailer = _FakeMailer()
    notifier = _FakeNotifier()

    orch = _build_orchestrator(
        notion=notion, fetcher=fetcher, llm=llm, mailer=mailer, notifier=notifier
    )
    result = await orch.run()

    assert result.notification_sent is True
    assert len(mailer.calls) == 1
    assert len(notifier.calls) == 1
    # Both channels saw the same subject (rendered once and fanned out).
    assert mailer.calls[0]["subject"] == notifier.calls[0]["subject"]


async def test_run_notifier_failure_aborts_before_writeback() -> None:
    # Slack-only path: a NotifierError must abort the batch identically to a
    # MailerError, so writeback never runs and Notion stays untouched.
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    notifier = _FakeNotifier(raise_error=True)

    orch = _build_orchestrator(notion=notion, fetcher=fetcher, llm=llm, notifier=notifier)

    with pytest.raises(NotifierError):
        await orch.run()
    assert notion.summary_writes == []
    assert notion.marked == []


async def test_run_mail_succeeds_then_slack_fails_aborts_before_writeback() -> None:
    # Failure semantics for the dual-channel path: even after mail succeeds, a
    # subsequent slack failure must skip writeback so the next run can retry.
    a1 = _article("p1", title="A1")
    notion = _FakeNotion(articles=[a1])
    fetcher = _FakeFetcher({a1.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary()})
    mailer = _FakeMailer()
    notifier = _FakeNotifier(raise_error=True)

    orch = _build_orchestrator(
        notion=notion, fetcher=fetcher, llm=llm, mailer=mailer, notifier=notifier
    )

    with pytest.raises(NotifierError):
        await orch.run()
    assert len(mailer.calls) == 1, "mail was sent before slack failed"
    assert notion.summary_writes == []
    assert notion.marked == []


def test_orchestrator_rejects_construction_without_any_channel() -> None:
    with pytest.raises(ValueError, match="at least one of mailer or notifier"):
        Orchestrator(
            notion=_FakeNotion(articles=[]),
            fetcher=_FakeFetcher({}),
            llm=_FakeLLM({}),
            digest_builder=DigestBuilder(),
        )


def test_orchestrator_rejects_mailer_without_mail_to() -> None:
    with pytest.raises(ValueError, match="mail_to is required"):
        Orchestrator(
            notion=_FakeNotion(articles=[]),
            fetcher=_FakeFetcher({}),
            llm=_FakeLLM({}),
            digest_builder=DigestBuilder(),
            mailer=_FakeMailer(),
            mail_to=None,
        )


# ---------- per_article granularity ----------


async def test_per_article_sends_one_mail_per_succeeded_article() -> None:
    a1 = _article("p1", title="A1")
    a2 = _article("p2", title="A2")
    a3 = _article("p3", title="A3")
    notion = _FakeNotion(articles=[a1, a2, a3])
    fetcher = _FakeFetcher({a1.url: _ok_fetch(), a2.url: _ok_fetch(), a3.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary(), "A2": _summary(), "A3": _summary()})
    mailer = _FakeMailer()

    orch = _build_orchestrator(
        notion=notion,
        fetcher=fetcher,
        llm=llm,
        mailer=mailer,
        notify_granularity=NotifyGranularity.PER_ARTICLE,
    )
    result = await orch.run()

    assert result.succeeded == 3
    assert result.failed == 0
    assert result.notification_sent is True
    assert len(mailer.calls) == 3, "per_article fans out N succeeded → N mails"
    # Each subject should be distinct so the chat thread groups per-article.
    subjects = [c["subject"] for c in mailer.calls]
    assert len(set(subjects)) == 3
    # All articles should be marked processed once writeback runs.
    assert set(notion.marked) == {"p1", "p2", "p3"}


async def test_per_article_with_partial_failure_appends_one_failure_summary() -> None:
    # Failed articles MUST NOT generate one notification each (would spam the
    # channel on bad days). They land in a single aggregated summary message.
    a_ok = _article("p1", title="OK")
    a_fetch_fail = _article("p2", title="FETCH")
    a_llm_fail = _article("p3", title="LLM")
    notion = _FakeNotion(articles=[a_ok, a_fetch_fail, a_llm_fail])
    fetcher = _FakeFetcher(
        {
            a_ok.url: _ok_fetch(a_ok.url),
            a_fetch_fail.url: _fail_fetch(a_fetch_fail.url, FetchFailureReason.TIMEOUT),
            a_llm_fail.url: _ok_fetch(a_llm_fail.url),
        }
    )
    llm = _FakeLLM({"OK": _summary(), "LLM": LLMError("schema invalid")})
    mailer = _FakeMailer()

    orch = _build_orchestrator(
        notion=notion,
        fetcher=fetcher,
        llm=llm,
        mailer=mailer,
        notify_granularity=NotifyGranularity.PER_ARTICLE,
    )
    result = await orch.run()

    assert result.succeeded == 1
    assert result.failed == 2
    # 1 per-article + 1 failure summary = 2 total mails
    assert len(mailer.calls) == 2
    last_subject = mailer.calls[-1]["subject"]
    assert "失敗" in last_subject and "2" in last_subject


async def test_per_article_zero_articles_still_sends_one_heartbeat() -> None:
    # Empty-batch case: even in per_article mode we send the digest-style
    # "today's queue is empty" message so operators can see the batch ran.
    notion = _FakeNotion(articles=[])
    mailer = _FakeMailer()

    orch = _build_orchestrator(
        notion=notion,
        fetcher=_FakeFetcher({}),
        llm=_FakeLLM({}),
        mailer=mailer,
        notify_granularity=NotifyGranularity.PER_ARTICLE,
    )
    result = await orch.run()

    assert result.notification_sent is True
    assert len(mailer.calls) == 1


async def test_per_article_mid_send_failure_aborts_before_writeback() -> None:
    # Failure semantics must hold per-article too: if any one of the fan-out
    # mails raises, writeback is skipped so the next batch will reprocess
    # everything (including the articles whose mail already succeeded — this
    # is the documented duplicate-notification trade-off, MVP-acceptable).
    class _FlakyMailer:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def send(self, *, to: list[str], subject: str, html: str, text: str) -> None:
            self.calls.append({"to": to, "subject": subject})
            if len(self.calls) == 2:
                raise MailerError("ses send failed on second mail")

    a1 = _article("p1", title="A1")
    a2 = _article("p2", title="A2")
    notion = _FakeNotion(articles=[a1, a2])
    fetcher = _FakeFetcher({a1.url: _ok_fetch(), a2.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary(), "A2": _summary()})
    flaky = _FlakyMailer()

    orch = _build_orchestrator(
        notion=notion,
        fetcher=fetcher,
        llm=llm,
        mailer=flaky,  # type: ignore[arg-type]
        notify_granularity=NotifyGranularity.PER_ARTICLE,
    )

    with pytest.raises(MailerError):
        await orch.run()

    assert len(flaky.calls) == 2, "second send raised — third must not run"
    assert notion.summary_writes == [], "writeback must not run after partial send failure"
    assert notion.marked == []


async def test_per_article_fans_out_to_both_channels_in_order() -> None:
    a1 = _article("p1", title="A1")
    a2 = _article("p2", title="A2")
    notion = _FakeNotion(articles=[a1, a2])
    fetcher = _FakeFetcher({a1.url: _ok_fetch(), a2.url: _ok_fetch()})
    llm = _FakeLLM({"A1": _summary(), "A2": _summary()})
    mailer = _FakeMailer()
    notifier = _FakeNotifier()

    orch = _build_orchestrator(
        notion=notion,
        fetcher=fetcher,
        llm=llm,
        mailer=mailer,
        notifier=notifier,
        notify_granularity=NotifyGranularity.PER_ARTICLE,
    )
    result = await orch.run()

    assert result.notification_sent is True
    assert len(mailer.calls) == 2
    assert len(notifier.calls) == 2
    # Both channels saw the same per-article subjects, in the same order.
    assert [c["subject"] for c in mailer.calls] == [c["subject"] for c in notifier.calls]
