from __future__ import annotations

import dataclasses

import pytest

from read_later_digest.domain.models import ReadLaterRunResult


def _make_result(**overrides: object) -> ReadLaterRunResult:
    defaults: dict[str, object] = {
        "total_articles": 2,
        "succeeded": 1,
        "failed": 1,
        "notification_sent": True,
        "status_updated": 1,
        "duration_ms": 42,
    }
    defaults.update(overrides)
    return ReadLaterRunResult(**defaults)  # type: ignore[arg-type]


class TestReadLaterRunResult:
    def test_asdict_returns_six_handler_keys(self) -> None:
        result = _make_result()
        out = dataclasses.asdict(result)
        assert set(out.keys()) == {
            "total_articles",
            "succeeded",
            "failed",
            "notification_sent",
            "status_updated",
            "duration_ms",
        }

    def test_asdict_values_match_constructor_args(self) -> None:
        result = _make_result(
            total_articles=5,
            succeeded=3,
            failed=2,
            notification_sent=False,
            status_updated=3,
            duration_ms=1234,
        )
        out = dataclasses.asdict(result)
        assert out["total_articles"] == 5
        assert out["succeeded"] == 3
        assert out["failed"] == 2
        assert out["notification_sent"] is False
        assert out["status_updated"] == 3
        assert out["duration_ms"] == 1234

    def test_is_frozen(self) -> None:
        result = _make_result()
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.total_articles = 5  # type: ignore[misc]

    def test_all_zero_values_are_valid(self) -> None:
        result = ReadLaterRunResult(
            total_articles=0,
            succeeded=0,
            failed=0,
            notification_sent=False,
            status_updated=0,
            duration_ms=0,
        )
        out = dataclasses.asdict(result)
        assert out == {
            "total_articles": 0,
            "succeeded": 0,
            "failed": 0,
            "notification_sent": False,
            "status_updated": 0,
            "duration_ms": 0,
        }

    def test_large_values(self) -> None:
        result = ReadLaterRunResult(
            total_articles=10_000,
            succeeded=9_999,
            failed=1,
            notification_sent=True,
            status_updated=9_999,
            duration_ms=999_999,
        )
        out = dataclasses.asdict(result)
        assert out == {
            "total_articles": 10_000,
            "succeeded": 9_999,
            "failed": 1,
            "notification_sent": True,
            "status_updated": 9_999,
            "duration_ms": 999_999,
        }
