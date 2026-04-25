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
