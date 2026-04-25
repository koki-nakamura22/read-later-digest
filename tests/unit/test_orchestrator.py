from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from read_later_digest.domain.digest_builder import DigestBuilder
from read_later_digest.domain.models import (
    ArticleSummary,
    ArticleType,
    FetchFailureReason,
    FetchResult,
    NotionArticle,
    Priority,
)
from read_later_digest.exceptions import LLMError, MailerError, NotionError
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
    mailer: _FakeMailer,
    llm_concurrency: int = 5,
) -> Orchestrator:
    return Orchestrator(
        notion=notion,
        fetcher=fetcher,
        llm=llm,
        mailer=mailer,
        digest_builder=DigestBuilder(),
        mail_to=["me@example.com"],
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
    assert result.mail_sent is True
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
    assert result.mail_sent is True
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

    assert result.mail_sent is False
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
