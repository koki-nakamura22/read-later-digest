from __future__ import annotations

import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime

from notion_client.errors import APIResponseError


def _parse_retry_after(value: str) -> float | None:
    """Retry-After ヘッダ値を秒数に変換. 不正値は None."""
    value = value.strip()
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, dt.timestamp() - time.time())


def with_retry[T](
    func: Callable[[], T],
    *,
    max_retries: int,
    initial_backoff_sec: float,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """429 限定の指数バックオフ retry. max_retries=0 で retry なし.

    Notes:
        Retry-After ヘッダがあればその値を sleep に使う.
        無ければ initial_backoff_sec * 2**attempt で fallback.
    """
    sleep_fn = sleep if sleep is not None else time.sleep
    attempt = 0
    while True:
        try:
            return func()
        except APIResponseError as e:
            if getattr(e, "status", None) != 429 or attempt >= max_retries:
                raise
            headers = getattr(e, "headers", None)
            retry_after = headers.get("Retry-After") if headers else None
            parsed = _parse_retry_after(retry_after) if retry_after is not None else None
            delay = parsed if parsed is not None else initial_backoff_sec * (2**attempt)
            sleep_fn(delay)
            attempt += 1
