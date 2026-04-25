from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "notion"


@pytest.fixture
def load_fixture() -> Callable[[str], dict[str, Any]]:
    def _load(name: str) -> dict[str, Any]:
        path = FIXTURES_DIR / name
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    return _load


class FakeDatabasesAPI:
    """Test double for notion_client.Client.databases.

    Each call to query() pops the next response from a queue. Responses can be:
      - a dict (returned as-is)
      - an Exception instance (raised)
    """

    def __init__(self, responses: Iterable[dict[str, Any] | Exception]) -> None:
        self._responses: list[dict[str, Any] | Exception] = list(responses)
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeDatabasesAPI.query called more times than expected")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class FakeNotionClient:
    def __init__(self, databases: FakeDatabasesAPI) -> None:
        self.databases = databases


@pytest.fixture
def make_fake_client() -> Callable[[Iterable[dict[str, Any] | Exception]], FakeNotionClient]:
    def _factory(responses: Iterable[dict[str, Any] | Exception]) -> FakeNotionClient:
        return FakeNotionClient(FakeDatabasesAPI(responses))

    return _factory


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Skip asyncio.sleep delays in retry tests for fast execution."""

    async def _instant(_: float) -> None:
        return None

    import asyncio

    monkeypatch.setattr(asyncio, "sleep", _instant)
    yield


def make_notion_page(
    *,
    page_id: str,
    title: str = "Sample",
    url: str | None = "https://example.com/sample",
    added_at: str | None = "2026-04-20T00:00:00+09:00",
    age_days: int | None = 0,
) -> dict[str, Any]:
    """Build a Notion `databases.query` result entry with sensible defaults.

    Pass `None` for `url` or `added_at` to simulate the corresponding missing-property
    case. Title may be the empty string to simulate an empty title.
    """
    title_items = [{"plain_text": title}] if title != "" else []
    return {
        "id": page_id,
        "properties": {
            "Name": {"title": title_items},
            "URL": {"url": url},
            "AddedAt": {"date": {"start": added_at} if added_at is not None else None},
            "Age": {"formula": {"number": age_days}},
        },
    }


def make_query_response(
    results: list[dict[str, Any]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    """Build a `databases.query` API response wrapping the given page results."""
    return {
        "object": "list",
        "results": results,
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


@pytest.fixture
def captured_notion_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    """Capture `logger.warning` calls from the Notion adapter module.

    Powertools Logger does not propagate to the root logger by default, so caplog
    cannot see these messages. We patch the module-level logger reference instead.
    """
    from read_later_digest.adapters import notion_repository as notion_module

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        captured.append((msg, kwargs.get("extra", {})))

    monkeypatch.setattr(notion_module.logger, "warning", _capture)
    return captured


@pytest.fixture
def captured_fetcher_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    """Capture `logger.warning` calls from the article fetcher module."""
    from read_later_digest.adapters import article_fetcher as fetcher_module

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        captured.append((msg, kwargs.get("extra", {})))

    monkeypatch.setattr(fetcher_module.logger, "warning", _capture)
    return captured
