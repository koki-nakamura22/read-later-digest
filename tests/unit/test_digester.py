from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from digestkit.digester import Digester, FailureInfo, RunResult
from digestkit.types import Item

import read_later_digest.digester as digester_mod
from read_later_digest.digester import ReadLaterDigester, _CachedSource, _paragraph_block
from read_later_digest.domain.models import FetchFailureReason
from read_later_digest.extractors.safe_webpage import FetchFailure

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(
    *,
    title: str = "Test Article",
    added_at: str | None = "2024-01-01T00:00:00+00:00",
    use_date_style: bool = False,
) -> dict[str, Any]:
    props: dict[str, Any] = {
        "Name": {"title": [{"plain_text": title}]},
    }
    if added_at is not None:
        if use_date_style:
            props["AddedAt"] = {"date": {"start": added_at}}
        else:
            props["AddedAt"] = {"created_time": added_at}
    return {"properties": props}


def _make_item(
    *,
    id: str = "page-1",
    url: str = "https://example.com",
    title: str = "Test Article",
    added_at: str | None = "2024-01-01T00:00:00+00:00",
    use_date_style: bool = False,
) -> Item:
    return Item(
        id=id,
        payload=url,
        metadata={
            "page": _make_page(title=title, added_at=added_at, use_date_style=use_date_style)
        },
    )


def _make_digester(
    *,
    items: list[Item] | None = None,
    slack: MagicMock | None = None,
    notion: MagicMock | None = None,
    max_items_per_run: int = 30,
    clock_fn: Any = None,
) -> tuple[ReadLaterDigester, MagicMock, MagicMock, MagicMock]:
    source = MagicMock()
    source.fetch.return_value = items if items is not None else []
    slack_mock = slack or MagicMock()
    notion_mock = notion or MagicMock()

    digester = ReadLaterDigester(
        source=source,
        extractor=MagicMock(),
        summarizer=MagicMock(),
        sink=MagicMock(),
        slack_notifier=slack_mock,
        notion_client=notion_mock,
        max_items_per_run=max_items_per_run,
        clock_fn=clock_fn,
        seen_store=None,
    )
    return digester, source, slack_mock, notion_mock


@pytest.fixture
def captured_digester_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    """Capture logger.warning calls from the digester module."""
    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        captured.append((msg, kwargs.get("extra", {})))

    monkeypatch.setattr(digester_mod.logger, "warning", _capture)
    return captured


# ---------------------------------------------------------------------------
# Tests: zero items → heartbeat + early return
# ---------------------------------------------------------------------------


class TestZeroItems:
    def test_send_heartbeat_called_with_target_date(self) -> None:
        # Arrange
        fixed_dt = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
        digester, _, slack, _ = _make_digester(items=[], clock_fn=lambda: fixed_dt)

        # Act
        digester.run()

        # Assert
        slack.send_heartbeat.assert_called_once_with(target_date="2024-03-15")

    def test_returns_run_result_with_zero_counts(self) -> None:
        # Arrange
        fixed_dt = datetime(2024, 3, 15, 12, 0, 0, tzinfo=UTC)
        digester, _, _, _ = _make_digester(items=[], clock_fn=lambda: fixed_dt)

        # Act
        result = digester.run()

        # Assert
        assert result.total_articles == 0
        assert result.succeeded == 0
        assert result.failed == 0
        assert result.status_updated == 0

    def test_notification_sent_true_on_heartbeat(self) -> None:
        # Arrange
        digester, _, _, _ = _make_digester(
            items=[], clock_fn=lambda: datetime(2024, 1, 1, tzinfo=UTC)
        )

        # Act
        result = digester.run()

        # Assert
        assert result.notification_sent is True

    def test_super_run_not_called_on_empty(self) -> None:
        # Arrange
        digester, _, _, _ = _make_digester(
            items=[], clock_fn=lambda: datetime(2024, 1, 1, tzinfo=UTC)
        )

        with patch.object(Digester, "run") as mock_super_run:
            # Act
            digester.run()

        # Assert: super().run() is never called when items is empty
        mock_super_run.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: one success item
# ---------------------------------------------------------------------------


class TestOneSuccessItem:
    def test_returns_succeeded_one(self) -> None:
        # Arrange
        item = _make_item(id="page-1")
        digester, _, _, _ = _make_digester(items=[item])

        with patch.object(Digester, "run", return_value=RunResult(success=1)):
            # Act
            result = digester.run()

        # Assert
        assert result.succeeded == 1
        assert result.status_updated == 1

    def test_returns_total_articles_one(self) -> None:
        # Arrange
        item = _make_item(id="page-1")
        digester, _, _, _ = _make_digester(items=[item])

        with patch.object(Digester, "run", return_value=RunResult(success=1)):
            result = digester.run()

        assert result.total_articles == 1

    def test_notification_sent_true_when_success_gt_zero(self) -> None:
        # Arrange
        item = _make_item(id="page-1")
        digester, _, _, _ = _make_digester(items=[item])

        with patch.object(Digester, "run", return_value=RunResult(success=1)):
            result = digester.run()

        assert result.notification_sent is True

    def test_no_failure_hooks_called_on_success(self) -> None:
        # Arrange
        item = _make_item(id="page-1")
        digester, _, slack, notion = _make_digester(items=[item])

        with patch.object(Digester, "run", return_value=RunResult(success=1)):
            digester.run()

        # Assert
        notion.blocks.children.append.assert_not_called()
        slack.send_failure_summary.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: one failure item
# ---------------------------------------------------------------------------


class TestOneFailureItem:
    def _make_failure(self, item: Item, reason: FetchFailureReason) -> FailureInfo:
        return FailureInfo(item=item, stage="write", error=FetchFailure(reason))

    def test_notion_append_called_with_failure_reason(self) -> None:
        # Arrange
        item = _make_item(id="page-abc")
        digester, _, _, notion = _make_digester(items=[item])
        failure = self._make_failure(item, FetchFailureReason.HTTP_4XX)

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=[failure])):
            # Act
            digester.run()

        # Assert: block appended with "[処理失敗] http_4xx"
        notion.blocks.children.append.assert_called_once_with(
            block_id="page-abc",
            children=[_paragraph_block("[処理失敗] http_4xx")],
        )

    def test_slack_failure_summary_called_once(self) -> None:
        # Arrange
        item = _make_item(id="page-abc")
        fixed_dt = datetime(2024, 3, 15, tzinfo=UTC)
        digester, _, slack, _ = _make_digester(items=[item], clock_fn=lambda: fixed_dt)
        failure = self._make_failure(item, FetchFailureReason.HTTP_4XX)

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=[failure])):
            digester.run()

        # Assert
        slack.send_failure_summary.assert_called_once_with([failure], target_date="2024-03-15")

    def test_returns_failed_one_and_succeeded_zero(self) -> None:
        # Arrange
        item = _make_item(id="page-abc")
        digester, _, _, _ = _make_digester(items=[item])
        failure = self._make_failure(item, FetchFailureReason.HTTP_4XX)

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=[failure])):
            result = digester.run()

        # Assert: no status update on failure
        assert result.succeeded == 0
        assert result.failed == 1
        assert result.status_updated == 0

    def test_notification_sent_true_on_failure(self) -> None:
        # Arrange
        item = _make_item(id="page-abc")
        digester, _, _, _ = _make_digester(items=[item])
        failure = self._make_failure(item, FetchFailureReason.HTTP_4XX)

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=[failure])):
            result = digester.run()

        assert result.notification_sent is True

    def test_reason_str_from_exception_when_no_reason_attr(self) -> None:
        # Arrange: error without 'reason' attribute → falls back to str(error)
        item = _make_item(id="page-xyz")
        digester, _, _, notion = _make_digester(items=[item])
        error = RuntimeError("something went wrong")
        failure = FailureInfo(item=item, stage="extract", error=error)

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=[failure])):
            digester.run()

        notion.blocks.children.append.assert_called_once_with(
            block_id="page-xyz",
            children=[_paragraph_block("[処理失敗] something went wrong")],
        )

    def test_notification_sent_false_when_all_skipped(self) -> None:
        # Arrange: super().run() returns success=0, failures=[] (e.g. all items deduped/skipped)
        item = _make_item(id="page-1")
        digester, _, _, _ = _make_digester(items=[item])

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=[])):
            result = digester.run()

        # Assert: no notification when nothing succeeded and no failures
        assert result.notification_sent is False
        assert result.succeeded == 0
        assert result.failed == 0

    def test_success_and_failure_mixed(self) -> None:
        # Arrange: 1 success + 1 failure in the same run
        item_ok = _make_item(id="page-ok", added_at="2024-01-01T00:00:00+00:00")
        item_fail = _make_item(id="page-fail", added_at="2024-02-01T00:00:00+00:00")
        digester, _, slack, notion = _make_digester(items=[item_ok, item_fail])
        failure = FailureInfo(
            item=item_fail, stage="write", error=FetchFailure(FetchFailureReason.TIMEOUT)
        )

        with patch.object(Digester, "run", return_value=RunResult(success=1, failures=[failure])):
            result = digester.run()

        # Assert: both counts correct, notification sent
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.notification_sent is True
        assert result.total_articles == 2
        notion.blocks.children.append.assert_called_once()
        slack.send_failure_summary.assert_called_once()

    def test_multiple_failures_each_get_notion_block(self) -> None:
        # Arrange
        item_a = _make_item(id="page-a", added_at="2024-01-01T00:00:00+00:00")
        item_b = _make_item(id="page-b", added_at="2024-02-01T00:00:00+00:00")
        digester, _, _, notion = _make_digester(items=[item_a, item_b])
        failures = [
            FailureInfo(item=item_a, stage="write", error=FetchFailure(FetchFailureReason.TIMEOUT)),
            FailureInfo(
                item=item_b, stage="write", error=FetchFailure(FetchFailureReason.HTTP_5XX)
            ),
        ]

        with patch.object(Digester, "run", return_value=RunResult(success=0, failures=failures)):
            digester.run()

        assert notion.blocks.children.append.call_count == 2


# ---------------------------------------------------------------------------
# Tests: AddedAt ascending sort
# ---------------------------------------------------------------------------


class TestSorting:
    def _capture_enriched_items(self, digester: ReadLaterDigester) -> list[Item]:
        """Patch Digester.run to capture the enriched items via the public fetch() interface."""
        captured: list[Item] = []

        def _capture(self_inner: ReadLaterDigester, **kwargs: Any) -> RunResult:
            captured.extend(self_inner.source.fetch())
            return RunResult(success=0)

        with patch.object(Digester, "run", _capture):
            digester.run()

        return captured

    def test_older_item_before_newer_item(self) -> None:
        # Arrange: items provided in wrong order (newer first)
        older = _make_item(id="page-old", added_at="2024-01-01T00:00:00+00:00")
        newer = _make_item(id="page-new", added_at="2024-06-01T00:00:00+00:00")
        digester, _, _, _ = _make_digester(items=[newer, older])

        # Act
        captured = self._capture_enriched_items(digester)

        # Assert
        assert captured[0].id == "page-old"
        assert captured[1].id == "page-new"

    def test_secondary_sort_by_id_when_same_added_at(self) -> None:
        # Arrange
        same_date = "2024-03-01T00:00:00+00:00"
        item_b = _make_item(id="page-b", added_at=same_date)
        item_a = _make_item(id="page-a", added_at=same_date)
        digester, _, _, _ = _make_digester(items=[item_b, item_a])

        # Act
        captured = self._capture_enriched_items(digester)

        # Assert: secondary sort by id ascending
        assert captured[0].id == "page-a"
        assert captured[1].id == "page-b"

    def test_item_without_added_at_sorts_to_front(self) -> None:
        # Arrange: no AddedAt → datetime.min → sorts first
        item_no_date = _make_item(id="page-no-date", added_at=None)
        item_with_date = _make_item(id="page-with-date", added_at="2024-01-01T00:00:00+00:00")
        digester, _, _, _ = _make_digester(items=[item_with_date, item_no_date])

        # Act
        captured = self._capture_enriched_items(digester)

        # Assert
        assert captured[0].id == "page-no-date"

    def test_item_with_malformed_date_sorts_to_front(self) -> None:
        # Arrange: malformed ISO → ValueError → datetime.min → sorts first
        bad_page = {"properties": {"AddedAt": {"created_time": "not-a-date"}}}
        item_bad = Item(id="page-bad", payload="https://ex.com", metadata={"page": bad_page})
        item_good = _make_item(id="page-good", added_at="2024-01-01T00:00:00+00:00")
        digester, _, _, _ = _make_digester(items=[item_good, item_bad])

        # Act
        captured = self._capture_enriched_items(digester)

        # Assert: malformed date → datetime.min → front
        assert captured[0].id == "page-bad"

    def test_date_style_property_sorted_correctly(self) -> None:
        # Arrange: date property (not created_time) style
        item_feb = _make_item(
            id="page-feb", added_at="2024-02-01T00:00:00+00:00", use_date_style=True
        )
        item_jan = _make_item(id="page-jan", added_at="2024-01-01T00:00:00+00:00")
        digester, _, _, _ = _make_digester(items=[item_feb, item_jan])

        # Act
        captured = self._capture_enriched_items(digester)

        # Assert: jan before feb
        assert captured[0].id == "page-jan"
        assert captured[1].id == "page-feb"


# ---------------------------------------------------------------------------
# Tests: max items exceeded → WARN log
# ---------------------------------------------------------------------------


class TestMaxItemsWarning:
    def test_warn_logged_when_items_exceed_max(
        self, captured_digester_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        # Arrange: 3 items with max_items_per_run=2
        items = [_make_item(id=f"page-{i}") for i in range(3)]
        digester, _, _, _ = _make_digester(items=items, max_items_per_run=2)

        with patch.object(Digester, "run", return_value=RunResult(success=3)):
            # Act
            digester.run()

        # Assert
        assert any("MAX_ITEMS_PER_RUN" in msg for msg, _ in captured_digester_warnings)

    def test_warn_contains_count_and_max_in_extra(
        self, captured_digester_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        # Arrange
        items = [_make_item(id=f"page-{i}") for i in range(5)]
        digester, _, _, _ = _make_digester(items=items, max_items_per_run=3)

        with patch.object(Digester, "run", return_value=RunResult(success=5)):
            digester.run()

        # Assert: extra dict has count=5, max=3
        matching = [
            extra for msg, extra in captured_digester_warnings if "MAX_ITEMS_PER_RUN" in msg
        ]
        assert matching
        assert matching[0]["count"] == 5
        assert matching[0]["max"] == 3

    def test_no_warn_when_items_at_max(
        self, captured_digester_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        # Arrange: exactly at limit
        items = [_make_item(id=f"page-{i}") for i in range(2)]
        digester, _, _, _ = _make_digester(items=items, max_items_per_run=2)

        with patch.object(Digester, "run", return_value=RunResult(success=2)):
            digester.run()

        # Assert
        assert not any("MAX_ITEMS_PER_RUN" in msg for msg, _ in captured_digester_warnings)


# ---------------------------------------------------------------------------
# Tests: determinism (clock_fn + time.monotonic)
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_target_date_uses_clock_fn(self) -> None:
        # Arrange
        fixed_dt = datetime(2024, 5, 20, 9, 0, 0, tzinfo=UTC)
        digester, _, slack, _ = _make_digester(items=[], clock_fn=lambda: fixed_dt)

        # Act
        digester.run()

        # Assert: heartbeat called with 2024-05-20
        slack.send_heartbeat.assert_called_once_with(target_date="2024-05-20")

    def test_duration_ms_zero_items(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange: started=0.0, after=0.5 → 500ms
        mono_seq = iter([0.0, 0.5])
        monkeypatch.setattr(digester_mod.time, "monotonic", lambda: next(mono_seq))

        digester, _, _, _ = _make_digester(
            items=[], clock_fn=lambda: datetime(2024, 1, 1, tzinfo=UTC)
        )

        # Act
        result = digester.run()

        # Assert
        assert result.duration_ms == 500

    def test_duration_ms_non_empty_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange: started=0.0, after=1.0 → 1000ms
        mono_seq = iter([0.0, 1.0])
        monkeypatch.setattr(digester_mod.time, "monotonic", lambda: next(mono_seq))

        item = _make_item(id="page-1")
        digester, _, _, _ = _make_digester(items=[item])

        with patch.object(Digester, "run", return_value=RunResult(success=1)):
            result = digester.run()

        assert result.duration_ms == 1000


# ---------------------------------------------------------------------------
# Tests: metadata enrichment (index / total / title / url)
# ---------------------------------------------------------------------------


class TestMetadataEnrichment:
    def _capture_enriched(self, digester: ReadLaterDigester) -> list[Item]:
        captured: list[Item] = []

        def _capture(self_inner: ReadLaterDigester, **kwargs: Any) -> RunResult:
            captured.extend(self_inner.source.fetch())
            return RunResult(success=len(captured))

        with patch.object(Digester, "run", _capture):
            digester.run()

        return captured

    def test_index_and_total_injected(self) -> None:
        # Arrange: two items in date order
        items = [
            _make_item(id="page-1", added_at="2024-01-01T00:00:00+00:00"),
            _make_item(id="page-2", added_at="2024-02-01T00:00:00+00:00"),
        ]
        digester, _, _, _ = _make_digester(items=items)

        # Act
        captured = self._capture_enriched(digester)

        # Assert
        assert captured[0].metadata["index"] == 1
        assert captured[0].metadata["total"] == 2
        assert captured[1].metadata["index"] == 2
        assert captured[1].metadata["total"] == 2

    def test_url_from_string_payload(self) -> None:
        # Arrange
        url = "https://example.com/article"
        item = Item(id="page-1", payload=url, metadata={"page": {}})
        digester, _, _, _ = _make_digester(items=[item])

        captured = self._capture_enriched(digester)

        assert captured[0].metadata["url"] == url

    def test_title_extracted_from_page(self) -> None:
        # Arrange
        item = _make_item(id="page-1", title="My Test Title")
        digester, _, _, _ = _make_digester(items=[item])

        captured = self._capture_enriched(digester)

        assert captured[0].metadata["title"] == "My Test Title"


# ---------------------------------------------------------------------------
# Tests: _paragraph_block helper
# ---------------------------------------------------------------------------


class TestParagraphBlock:
    def test_returns_correct_block_structure(self) -> None:
        result = _paragraph_block("hello world")

        assert result == {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": "hello world"}}]},
        }

    def test_handles_empty_string(self) -> None:
        result = _paragraph_block("")

        assert result["paragraph"]["rich_text"][0]["text"]["content"] == ""

    def test_handles_special_characters(self) -> None:
        text = "[処理失敗] http_4xx"
        result = _paragraph_block(text)

        assert result["paragraph"]["rich_text"][0]["text"]["content"] == text


# ---------------------------------------------------------------------------
# Tests: _CachedSource
# ---------------------------------------------------------------------------


class TestCachedSource:
    def test_fetch_yields_all_items(self) -> None:
        # Arrange
        items = [_make_item(id="page-1"), _make_item(id="page-2")]
        cached = _CachedSource(items, original=MagicMock())

        # Act
        result = list(cached.fetch())

        # Assert
        assert result == items

    def test_fetch_yields_empty_when_no_items(self) -> None:
        cached = _CachedSource([], original=MagicMock())
        assert list(cached.fetch()) == []

    def test_delegates_unknown_attribute_to_original(self) -> None:
        # Arrange
        original = MagicMock()
        original.ack_success.return_value = "delegated"
        cached = _CachedSource([], original=original)

        # Act
        result = cached.ack_success("some-item")

        # Assert
        original.ack_success.assert_called_once_with("some-item")
        assert result == "delegated"

    def test_fetch_is_not_delegated_to_original(self) -> None:
        # Arrange: original has a different fetch that should NOT be called
        original = MagicMock()
        original.fetch.return_value = iter([])
        items = [_make_item(id="page-cached")]
        cached = _CachedSource(items, original=original)

        # Act
        result = list(cached.fetch())

        # Assert: items from cache, not from original
        assert result == items
        original.fetch.assert_not_called()
