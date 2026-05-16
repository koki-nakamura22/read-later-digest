from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from digestkit.digester import ConfigurationError
from digestkit.protocols import Sink
from digestkit.types import Digest, Item

from read_later_digest.sinks.ordered import OrderedSink

# ---------- helpers ----------


def _digest() -> Digest:
    return Digest(summary="s", tokens_in=0, tokens_out=0, latency_ms=0, model="test")


def _item() -> Item:
    return Item(id="i1", payload=None)


def _sink() -> MagicMock:
    return MagicMock(spec=Sink)


# ---------- __init__ ----------


class TestOrderedSinkInit:
    def test_accepts_single_sink(self) -> None:
        # Arrange / Act / Assert
        OrderedSink([_sink()])  # no exception

    def test_accepts_multiple_sinks(self) -> None:
        # Arrange / Act / Assert
        OrderedSink([_sink(), _sink(), _sink()])  # no exception

    def test_empty_list_raises_configuration_error(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(ConfigurationError):
            OrderedSink([])


# ---------- write — 正常系 ----------


class TestOrderedSinkWriteSuccess:
    def test_calls_all_sinks_when_no_failure(self) -> None:
        # Arrange
        s1, s2 = _sink(), _sink()
        ordered = OrderedSink([s1, s2])
        digest, item = _digest(), _item()
        # Act
        ordered.write(digest, item)
        # Assert
        s1.write.assert_called_once_with(digest, item)
        s2.write.assert_called_once_with(digest, item)

    def test_calls_sinks_in_order(self) -> None:
        # Arrange
        call_order: list[str] = []
        s1 = MagicMock(spec=Sink)
        s1.write.side_effect = lambda *_: call_order.append("s1")
        s2 = MagicMock(spec=Sink)
        s2.write.side_effect = lambda *_: call_order.append("s2")
        ordered = OrderedSink([s1, s2])
        # Act
        ordered.write(_digest(), _item())
        # Assert
        assert call_order == ["s1", "s2"]

    def test_single_sink_is_called(self) -> None:
        # Arrange
        s = _sink()
        ordered = OrderedSink([s])
        digest, item = _digest(), _item()
        # Act
        ordered.write(digest, item)
        # Assert
        s.write.assert_called_once_with(digest, item)


# ---------- write — 異常系 ----------


class TestOrderedSinkWriteFailure:
    def test_first_sink_failure_propagates_exception(self) -> None:
        # Arrange
        s1, s2 = _sink(), _sink()
        s1.write.side_effect = RuntimeError("boom")
        ordered = OrderedSink([s1, s2])
        # Act / Assert
        with pytest.raises(RuntimeError, match="boom"):
            ordered.write(_digest(), _item())

    def test_first_sink_failure_skips_second_sink(self) -> None:
        # Arrange
        s1, s2 = _sink(), _sink()
        s1.write.side_effect = RuntimeError("boom")
        ordered = OrderedSink([s1, s2])
        # Act
        with pytest.raises(RuntimeError):
            ordered.write(_digest(), _item())
        # Assert
        s1.write.assert_called_once()
        s2.write.assert_not_called()

    def test_second_sink_failure_first_sink_was_called(self) -> None:
        # Arrange
        s1, s2 = _sink(), _sink()
        s2.write.side_effect = ValueError("bad value")
        ordered = OrderedSink([s1, s2])
        digest, item = _digest(), _item()
        # Act
        with pytest.raises(ValueError, match="bad value"):
            ordered.write(digest, item)
        # Assert — s1 was called (passed), s2 was called (raised), stops there
        s1.write.assert_called_once_with(digest, item)
        s2.write.assert_called_once()

    def test_second_sink_failure_propagates_exception(self) -> None:
        # Arrange
        s1, s2 = _sink(), _sink()
        s2.write.side_effect = ValueError("bad value")
        ordered = OrderedSink([s1, s2])
        # Act / Assert
        with pytest.raises(ValueError, match="bad value"):
            ordered.write(_digest(), _item())

    def test_second_sink_failure_skips_third_sink(self) -> None:
        # Arrange
        s1, s2, s3 = _sink(), _sink(), _sink()
        s2.write.side_effect = RuntimeError("mid fail")
        ordered = OrderedSink([s1, s2, s3])
        # Act
        with pytest.raises(RuntimeError):
            ordered.write(_digest(), _item())
        # Assert
        s3.write.assert_not_called()

    def test_exception_type_is_preserved(self) -> None:
        # Arrange — 独自例外がそのまま伝播することを確認
        class CustomError(Exception):
            pass

        s1 = _sink()
        s1.write.side_effect = CustomError("custom")
        ordered = OrderedSink([s1])
        # Act / Assert
        with pytest.raises(CustomError, match="custom"):
            ordered.write(_digest(), _item())
