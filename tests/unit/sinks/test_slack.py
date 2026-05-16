from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from digestkit.types import Digest, Item

from read_later_digest.sinks.slack import SlackBlockKitSink

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
) -> tuple[SlackBlockKitSink, MagicMock]:
    """SlackBlockKitSink と mock httpx.Client を返す."""
    client = MagicMock(spec=httpx.Client)
    response = MagicMock()
    response.raise_for_status.return_value = None
    client.post.return_value = response
    return SlackBlockKitSink(webhook_url=webhook_url, client=client), client


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

    def test_header_uses_payload_as_title_when_title_missing(self) -> None:
        # Arrange — title メタデータなし、payload を fallback
        item = Item(id="p3", payload="https://fallback.example.com", metadata={})
        sink, client = _make_sink_with_mock()
        # Act
        sink.write(_digest(), item)
        # Assert
        blocks = client.post.call_args.kwargs["json"]["blocks"]
        assert "https://fallback.example.com" in blocks[0]["text"]["text"]


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
