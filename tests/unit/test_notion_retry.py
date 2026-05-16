from __future__ import annotations

from email.utils import formatdate

import httpx
import pytest
from notion_client.errors import APIErrorCode, APIResponseError

import read_later_digest.notion_retry as notion_retry_mod
from read_later_digest.notion_retry import with_retry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rate_limited(retry_after: str | None = None) -> APIResponseError:
    headers = httpx.Headers({"Retry-After": retry_after}) if retry_after else httpx.Headers()
    return APIResponseError(
        code=APIErrorCode.RateLimited,
        status=429,
        message="rate limited",
        headers=headers,
        raw_body_text="",
    )


def _unauthorized() -> APIResponseError:
    return APIResponseError(
        code=APIErrorCode.Unauthorized,
        status=401,
        message="unauthorized",
        headers=httpx.Headers(),
        raw_body_text="",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWithRetrySuccess:
    def test_returns_value_when_first_call_succeeds(self) -> None:
        # Arrange
        sleeps: list[float] = []

        # Act
        result = with_retry(
            lambda: "ok", max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append
        )

        # Assert
        assert result == "ok"
        assert sleeps == []

    def test_recovers_after_single_429(self) -> None:
        # Arrange
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited()
            return "ok"

        sleeps: list[float] = []

        # Act
        result = with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        # Assert
        assert result == "ok"
        assert sleeps == [1.0]

    def test_exponential_backoff_progression(self) -> None:
        # Arrange: 4 calls needed — first 3 raise 429, 4th succeeds
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) < 4:
                raise _rate_limited()
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        # Assert: 1.0 * 2^0, 1.0 * 2^1, 1.0 * 2^2
        assert sleeps == [1.0, 2.0, 4.0]


class TestWithRetryFailure:
    def test_raises_last_429_when_max_retries_exceeded(self) -> None:
        # Arrange: always raises 429
        def func() -> str:
            raise _rate_limited()

        sleeps: list[float] = []

        # Act & Assert
        with pytest.raises(APIResponseError) as exc_info:
            with_retry(func, max_retries=2, initial_backoff_sec=1.0, sleep=sleeps.append)

        assert exc_info.value.status == 429
        assert len(sleeps) == 2  # slept once per retry (max_retries=2)

    def test_does_not_retry_non_429(self) -> None:
        # Arrange
        sleeps: list[float] = []

        def func() -> str:
            raise _unauthorized()

        # Act & Assert
        with pytest.raises(APIResponseError) as exc_info:
            with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        assert exc_info.value.status == 401
        assert sleeps == []

    def test_no_retry_when_max_retries_zero(self) -> None:
        # Arrange
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            raise _rate_limited()

        sleeps: list[float] = []

        # Act & Assert
        with pytest.raises(APIResponseError) as exc_info:
            with_retry(func, max_retries=0, initial_backoff_sec=1.0, sleep=sleeps.append)

        assert exc_info.value.status == 429
        assert len(calls) == 1
        assert sleeps == []


class TestRetryAfterHeader:
    def test_respects_retry_after_integer_seconds(self) -> None:
        # Arrange
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited(retry_after="7")
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        # Assert
        assert sleeps == [7.0]

    def test_respects_retry_after_http_date(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange: fix time.time so the future date is deterministic
        fixed_now = 1_000_000.0
        monkeypatch.setattr(notion_retry_mod.time, "time", lambda: fixed_now)

        future_ts = fixed_now + 15.0
        retry_after_date = formatdate(future_ts, usegmt=True)

        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited(retry_after=retry_after_date)
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        # Assert: time.time is fully fixed so the delta is exact
        assert sleeps == [15.0]

    def test_fallback_to_exponential_on_invalid_retry_after(self) -> None:
        # Arrange
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited(retry_after="not-a-date-or-number")
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=2.0, sleep=sleeps.append)

        # Assert: falls back to initial_backoff_sec * 2^0 = 2.0
        assert sleeps == [2.0]

    def test_retry_after_zero_sleeps_zero_seconds(self) -> None:
        # Arrange: "0" is a valid non-negative Retry-After
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited(retry_after="0")
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        # Assert
        assert sleeps == [0.0]

    def test_retry_after_negative_clamps_to_zero(self) -> None:
        # Arrange: negative value is clamped to 0.0 by max(0.0, ...)
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited(retry_after="-5")
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=1.0, sleep=sleeps.append)

        # Assert
        assert sleeps == [0.0]

    def test_retry_after_empty_string_falls_back_to_exponential(self) -> None:
        # Arrange
        calls: list[int] = []

        def func() -> str:
            calls.append(1)
            if len(calls) == 1:
                raise _rate_limited(retry_after="   ")
            return "ok"

        sleeps: list[float] = []

        # Act
        with_retry(func, max_retries=3, initial_backoff_sec=3.0, sleep=sleeps.append)

        # Assert: fallback to 3.0 * 2^0
        assert sleeps == [3.0]
