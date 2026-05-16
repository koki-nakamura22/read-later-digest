from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from digestkit.digester import FailureInfo
from digestkit.types import Digest, Item

from read_later_digest.domain.models import FetchFailureReason
from read_later_digest.sinks.slack import SlackBlockKitSink


class _FetchFailureError(Exception):
    """Test-local stub for an error that has a FetchFailureReason attribute."""

    def __init__(self, reason: FetchFailureReason, status_code: int) -> None:
        self.reason = reason
        self.status_code = status_code


# ---------- helpers ----------

_WEBHOOK_URL = "https://hooks.slack.example.com/services/T/B/X"


def _digest(
    *,
    type_: str | None = "記事",
    priority: str | None = "高",
) -> Digest:
    type_fragment = f'"type":"{type_}"' if type_ is not None else '"type":null'
    priority_fragment = f'"priority":"{priority}"' if priority is not None else '"priority":null'
    summary_json = (
        '{"summary_lines":["行1","行2","行3"],"key_points":["ポイント1"],'
        + type_fragment
        + ","
        + priority_fragment
        + "}"
    )
    return Digest(summary=summary_json, tokens_in=10, tokens_out=20, latency_ms=100, model="test")


def _item_with_meta(
    *,
    index: int | None = 1,
    total: int | None = 3,
    title: str = "テスト記事タイトル",
    url: str = "https://example.com/article",
) -> Item:
    meta: dict = {"title": title, "url": url}
    if index is not None:
        meta["index"] = index
    if total is not None:
        meta["total"] = total
    return Item(id="p1", payload=url, metadata=meta)


def _make_sink_with_mock(
    webhook_url: str = _WEBHOOK_URL,
    subject_prefix: str = "[read-later-digest]",
) -> tuple[SlackBlockKitSink, MagicMock]:
    """SlackBlockKitSink と mock httpx.Client を返す."""
    client = MagicMock(spec=httpx.Client)
    response = MagicMock()
    response.raise_for_status.return_value = None
    client.post.return_value = response
    return SlackBlockKitSink(
        webhook_url=webhook_url, client=client, subject_prefix=subject_prefix
    ), client


# ---------- __init__ ----------


class TestSlackBlockKitSinkInit:
    def test_accepts_injected_client(self) -> None:
        # Arrange / Act / Assert
        client = MagicMock(spec=httpx.Client)
        SlackBlockKitSink(webhook_url=_WEBHOOK_URL, client=client)  # no exception

    def test_creates_internal_client_when_none_given(self) -> None:
        # Arrange / Act
        sink = SlackBlockKitSink(webhook_url=_WEBHOOK_URL)
        # Assert — close() can be called without error
        sink.close()

    def test_close_does_not_close_injected_client(self) -> None:
        # Arrange
        client = MagicMock(spec=httpx.Client)
        sink = SlackBlockKitSink(webhook_url=_WEBHOOK_URL, client=client)
        # Act
        sink.close()
        # Assert — externally injected client is NOT closed by sink
        client.close.assert_not_called()

    def test_close_closes_internally_owned_client(self) -> None:
        # Arrange — inject a mock to avoid real network; simulate "no client passed"
        # by directly patching the internal state after construction
        internal_client = MagicMock(spec=httpx.Client)
        sink = SlackBlockKitSink(webhook_url=_WEBHOOK_URL)
        sink._client = internal_client  # replace real client with mock for assertion
        sink._owns_client = True
        # Act
        sink.close()
        # Assert
        internal_client.close.assert_called_once()


# ---------- write — Block Kit ペイロード構造 ----------


class TestSlackBlockKitSinkWritePayload:
    def test_posts_to_webhook_url(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock(webhook_url=_WEBHOOK_URL)
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        call_args = client.post.call_args
        assert call_args.args[0] == _WEBHOOK_URL

    def test_payload_has_blocks_key(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        payload = client.post.call_args.kwargs["json"]
        assert "blocks" in payload

    def test_per_article_blocks_count(self) -> None:
        # Arrange — header + URL + タグ + 要約 + ポイント = 5 blocks
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert len(blocks) == 5

    def test_header_block_contains_index_and_total(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta(index=1, total=3, title="タイトル"))
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["text"]["text"] == "[1/3] タイトル"

    def test_header_block_type_is_header(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["type"] == "header"

    def test_url_block_contains_mrkdwn_link(self) -> None:
        # Arrange
        url = "https://example.com/article"
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta(url=url))
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert f"<{url}|記事を開く>" in blocks[1]["text"]["text"]

    def test_tag_block_contains_type_and_priority(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(type_="記事", priority="高"), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        tag_text = blocks[2]["text"]["text"]
        assert "記事" in tag_text
        assert "高" in tag_text

    def test_summary_block_contains_bullet_lines(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        summary_text = blocks[3]["text"]["text"]
        assert "• 行1" in summary_text
        assert "• 行2" in summary_text
        assert "• 行3" in summary_text

    def test_key_points_block_contains_bullet_items(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        kp_text = blocks[4]["text"]["text"]
        assert "• ポイント1" in kp_text

    def test_raises_for_status_is_called(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), _item_with_meta())
        # Assert
        response = client.post.return_value
        response.raise_for_status.assert_called_once()


# ---------- write — index/total なし ----------


class TestSlackBlockKitSinkHeaderWithoutIndexTotal:
    def test_header_shows_title_only_when_no_index_total(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        item = _item_with_meta(index=None, total=None, title="タイトルのみ")
        # Act
        sink.write(_digest(), item)
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["text"]["text"] == "タイトルのみ"

    def test_header_shows_title_only_when_index_present_but_total_absent(self) -> None:
        # Arrange — index あり、total なしの場合はプレフィックスなし
        sink, client = _make_sink_with_mock()
        meta = {"title": "部分メタ", "url": "https://example.com", "index": 2}
        item = Item(id="p2", payload="https://example.com", metadata=meta)
        # Act
        sink.write(_digest(), item)
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["text"]["text"] == "部分メタ"

    def test_header_shows_title_only_when_total_present_but_index_absent(self) -> None:
        # Arrange — total あり、index なしの場合もプレフィックスなし
        sink, client = _make_sink_with_mock()
        meta = {"title": "逆パターン", "url": "https://example.com", "total": 3}
        item = Item(id="p4", payload="https://example.com", metadata=meta)
        # Act
        sink.write(_digest(), item)
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["text"]["text"] == "逆パターン"

    def test_header_uses_payload_as_title_when_title_missing(self) -> None:
        # Arrange — title メタデータなし、payload を fallback
        item = Item(id="p3", payload="https://fallback.example.com", metadata={})
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), item)
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "https://fallback.example.com" in blocks[0]["text"]["text"]

    def test_metadata_none_falls_back_to_payload(self) -> None:
        # Arrange — metadata 自体が None の場合、payload を title/url に使う
        item = Item(id="p5", payload="https://payload.example.com", metadata=None)
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), item)
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "https://payload.example.com" in blocks[0]["text"]["text"]
        assert "<https://payload.example.com|記事を開く>" in blocks[1]["text"]["text"]


# ---------- write — Type / Priority None ----------


class TestSlackBlockKitSinkNullableMetadata:
    def test_type_none_shows_uncategorized(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(type_=None, priority="高"), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "未分類" in blocks[2]["text"]["text"]

    def test_priority_none_shows_unset(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(type_="記事", priority=None), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "未設定" in blocks[2]["text"]["text"]

    def test_both_none_shows_both_fallback_labels(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(type_=None, priority=None), _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        tag_text = blocks[2]["text"]["text"]
        assert "未分類" in tag_text
        assert "未設定" in tag_text

    def test_summary_block_empty_lines(self) -> None:
        # Arrange — summary_lines が空リストの場合は bullet 行なし (境界値: 0件)
        empty_digest = Digest(
            summary='{"summary_lines":[],"key_points":["x"],"type":null,"priority":null}',
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            model="test",
        )
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(empty_digest, _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        summary_text = blocks[3]["text"]["text"]
        assert summary_text == "*3 行要約*\n"

    def test_key_points_block_empty(self) -> None:
        # Arrange — key_points が空リストの場合は bullet 行なし (境界値: 0件)
        empty_digest = Digest(
            summary='{"summary_lines":["a"],"key_points":[],"type":null,"priority":null}',
            tokens_in=0,
            tokens_out=0,
            latency_ms=0,
            model="test",
        )
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(empty_digest, _item_with_meta())
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        kp_text = blocks[4]["text"]["text"]
        assert kp_text == "*重要ポイント*\n"


# ---------- write — 非 2xx 異常系 ----------


class TestSlackBlockKitSinkNon2xx:
    def test_raises_http_status_error_on_non_2xx(self) -> None:
        # Arrange
        client = MagicMock(spec=httpx.Client)
        response = MagicMock()
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500 Internal Server Error",
            request=MagicMock(),
            response=MagicMock(),
        )
        client.post.return_value = response
        sink = SlackBlockKitSink(webhook_url=_WEBHOOK_URL, client=client)
        # Act / Assert
        with pytest.raises(httpx.HTTPStatusError):
            sink.write(_digest(), _item_with_meta())

    def test_exception_is_not_swallowed(self) -> None:
        # Arrange — post 自体が例外を投げるケース
        client = MagicMock(spec=httpx.Client)
        client.post.side_effect = httpx.ConnectError("connection refused")
        sink = SlackBlockKitSink(webhook_url=_WEBHOOK_URL, client=client)
        # Act / Assert
        with pytest.raises(httpx.ConnectError):
            sink.write(_digest(), _item_with_meta())


# ---------- send_failure_summary ----------


class TestSendFailureSummary:
    def test_posts_aggregated_payload(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(
                    id="p1",
                    payload="https://example.com",
                    metadata={"title": "失敗記事", "url": "https://example.com"},
                ),
                stage="extract",
                error=_FetchFailureError(reason=FetchFailureReason.HTTP_4XX, status_code=404),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "処理失敗 1 件" in blocks[0]["text"]["text"]
        assert "失敗記事" in blocks[1]["text"]["text"]

    def test_skips_when_empty(self) -> None:
        # Arrange
        client = MagicMock(spec=httpx.Client)
        sink = SlackBlockKitSink(webhook_url=_WEBHOOK_URL, client=client)
        # Act
        sink.send_failure_summary([], target_date="2026-05-16")
        # Assert — no HTTP call made
        client.post.assert_not_called()

    def test_header_block_type_is_header(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(id="p1", payload="https://example.com", metadata={}),
                stage="extract",
                error=_FetchFailureError(reason=FetchFailureReason.TIMEOUT, status_code=0),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["type"] == "header"

    def test_section_block_contains_reason_value(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(
                    id="p1",
                    payload="https://example.com",
                    metadata={"title": "記事", "url": "https://example.com"},
                ),
                stage="extract",
                error=_FetchFailureError(reason=FetchFailureReason.HTTP_4XX, status_code=404),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert — reason.value = "http_4xx"
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "http_4xx" in blocks[1]["text"]["text"]

    def test_falls_back_to_str_error_when_no_reason(self) -> None:
        # Arrange — error without reason attribute
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(id="p1", payload="https://example.com", metadata={}),
                stage="extract",
                error=ValueError("something went wrong"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "something went wrong" in blocks[1]["text"]["text"]

    def test_falls_back_payload_when_no_title_or_url_in_metadata(self) -> None:
        # Arrange — metadata has neither title nor url
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(id="p1", payload="https://fallback.example.com", metadata={}),
                stage="extract",
                error=_FetchFailureError(reason=FetchFailureReason.NETWORK, status_code=0),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "https://fallback.example.com" in blocks[1]["text"]["text"]

    def test_metadata_none_uses_payload(self) -> None:
        # Arrange — metadata is None
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(id="p1", payload="https://payload.example.com", metadata=None),
                stage="extract",
                error=ValueError("err"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "https://payload.example.com" in blocks[1]["text"]["text"]

    def test_multiple_failures_all_listed(self) -> None:
        # Arrange — 2 failures → both appear in section text
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(
                    id="p1",
                    payload="https://a.example.com",
                    metadata={"title": "記事A", "url": "https://a.example.com"},
                ),
                stage="extract",
                error=_FetchFailureError(reason=FetchFailureReason.HTTP_4XX, status_code=404),
            ),
            FailureInfo(
                item=Item(
                    id="p2",
                    payload="https://b.example.com",
                    metadata={"title": "記事B", "url": "https://b.example.com"},
                ),
                stage="extract",
                error=_FetchFailureError(reason=FetchFailureReason.HTTP_5XX, status_code=503),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "処理失敗 2 件" in blocks[0]["text"]["text"]
        section_text = blocks[1]["text"]["text"]
        assert "記事A" in section_text
        assert "記事B" in section_text

    def test_exactly_two_blocks_returned(self) -> None:
        # Arrange — failure_blocks always: [header, section]
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(id="p1", payload="https://example.com", metadata={}),
                stage="extract",
                error=ValueError("err"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert len(blocks) == 2

    def test_reason_with_no_value_attr_uses_str_reason(self) -> None:
        # Arrange — error.reason is a plain string (no .value attribute)
        class _ErrWithStrReasonError(Exception):
            reason = "plain-string-reason"

        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(
                    id="p1",
                    payload="https://example.com",
                    metadata={"title": "記事", "url": "https://example.com"},
                ),
                stage="extract",
                error=_ErrWithStrReasonError("err"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert — str(reason) used, not reason.value
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "plain-string-reason" in blocks[1]["text"]["text"]

    def test_title_absent_url_present_uses_url_as_label(self) -> None:
        # Arrange — metadata has url but not title; url is used as both label and link
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(
                    id="p1",
                    payload="https://payload.example.com",
                    metadata={"url": "https://meta.example.com"},
                ),
                stage="extract",
                error=ValueError("err"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert — url appears as both label and link target
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        section_text = blocks[1]["text"]["text"]
        assert "https://meta.example.com" in section_text

    def test_title_present_url_absent_uses_payload_as_url(self) -> None:
        # Arrange — metadata has title but not url; payload is used as link target
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(
                    id="p1",
                    payload="https://payload.example.com",
                    metadata={"title": "タイトルのみ"},
                ),
                stage="extract",
                error=ValueError("err"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert — title as label, payload as link url
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        section_text = blocks[1]["text"]["text"]
        assert "タイトルのみ" in section_text
        assert "https://payload.example.com" in section_text

    def test_raises_for_status_called(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        failures = [
            FailureInfo(
                item=Item(id="p1", payload="https://example.com", metadata={}),
                stage="extract",
                error=ValueError("err"),
            ),
        ]
        # Act
        sink.send_failure_summary(failures, target_date="2026-05-16")
        # Assert
        client.post.return_value.raise_for_status.assert_called_once()


# ---------- send_heartbeat ----------


class TestSendHeartbeat:
    def test_posts_zero_item_message(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.send_heartbeat(target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "本日の未読 0 件" in blocks[0]["text"]["text"]

    def test_block_type_is_section(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.send_heartbeat(target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert blocks[0]["type"] == "section"

    def test_target_date_in_message(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.send_heartbeat(target_date="2099-12-31")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "2099-12-31" in blocks[0]["text"]["text"]

    def test_exactly_one_block_returned(self) -> None:
        # Arrange — heartbeat always emits exactly 1 block
        sink, client = _make_sink_with_mock()
        # Act
        sink.send_heartbeat(target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert len(blocks) == 1

    def test_subject_prefix_in_message(self) -> None:
        # Arrange — custom subject_prefix should appear in heartbeat text
        sink, client = _make_sink_with_mock(subject_prefix="[custom-prefix]")
        # Act
        sink.send_heartbeat(target_date="2026-05-16")
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "[custom-prefix]" in blocks[0]["text"]["text"]

    def test_raises_for_status_called(self) -> None:
        # Arrange
        sink, client = _make_sink_with_mock()
        # Act
        sink.send_heartbeat(target_date="2026-05-16")
        # Assert
        client.post.return_value.raise_for_status.assert_called_once()
