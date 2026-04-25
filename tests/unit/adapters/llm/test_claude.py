from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from read_later_digest.adapters.llm import claude as claude_module
from read_later_digest.adapters.llm.claude import ClaudeLLMClient
from read_later_digest.domain.models import ArticleType, Priority
from read_later_digest.exceptions import LLMError


@dataclass
class _FakeBlock:
    text: str


@dataclass
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class _FakeResponse:
    content: list[_FakeBlock]
    usage: _FakeUsage = field(default_factory=_FakeUsage)


class _FakeRateLimitError(Exception):
    pass


class _FakeMessagesAPI:
    """Fake of anthropic AsyncAnthropic.messages.

    Each call to create() pops the next item from a queue. Items can be:
      - a _FakeResponse  -> returned as-is
      - an Exception     -> raised
    """

    def __init__(self, responses: Iterable[_FakeResponse | Exception]) -> None:
        self._responses: list[_FakeResponse | Exception] = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeMessagesAPI.create called more times than expected")
        nxt = self._responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


class _FakeAnthropic:
    def __init__(self, messages: _FakeMessagesAPI) -> None:
        self.messages = messages


def _ok_payload(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary_lines": ["要約1", "要約2", "要約3"],
        "key_points": ["要点1", "要点2", "要点3"],
        "type": "技術",
        "priority": "高",
    }
    base.update(overrides)
    return base


def _response_with(payload: dict[str, Any] | str) -> _FakeResponse:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return _FakeResponse(content=[_FakeBlock(text=text)])


@pytest.fixture
def patch_rate_limit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(claude_module, "_AnthropicRateLimitError", _FakeRateLimitError)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def _instant(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant)
    yield


class TestClaudeLLMClientHappyPath:
    async def test_returns_article_summary(self) -> None:
        api = _FakeMessagesAPI([_response_with(_ok_payload())])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        summary = await client.summarize(title="t", body="b")

        assert summary.summary_lines == ["要約1", "要約2", "要約3"]
        assert len(summary.key_points) == 3
        assert summary.type_ is ArticleType.TECH
        assert summary.priority is Priority.HIGH

    async def test_passes_prompt_caching_marker(self) -> None:
        api = _FakeMessagesAPI([_response_with(_ok_payload())])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        await client.summarize(title="t", body="b")

        system = api.calls[0]["system"]
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    async def test_truncates_body(self) -> None:
        api = _FakeMessagesAPI([_response_with(_ok_payload())])
        client = ClaudeLLMClient(client=_FakeAnthropic(api), body_max_chars=10)

        await client.summarize(title="t", body="x" * 100)

        user_content = api.calls[0]["messages"][0]["content"]
        body_section = user_content.split("# 本文\n", 1)[1]
        assert body_section == "x" * 10


class TestClaudeLLMClientEnumCoercion:
    async def test_unknown_type_becomes_none(self) -> None:
        api = _FakeMessagesAPI([_response_with(_ok_payload(type="未知", priority="不明"))])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        summary = await client.summarize(title="t", body="b")

        assert summary.type_ is None
        assert summary.priority is None

    async def test_extracts_json_from_prose(self) -> None:
        wrapped = "前置き\n" + json.dumps(_ok_payload(), ensure_ascii=False) + "\n後置き"
        api = _FakeMessagesAPI([_response_with(wrapped)])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        summary = await client.summarize(title="t", body="b")
        assert summary.summary_lines[0] == "要約1"


class TestClaudeLLMClientSchemaRetry:
    async def test_invalid_then_valid_returns_summary(self) -> None:
        bad = _response_with(_ok_payload(summary_lines=["only-one"]))
        good = _response_with(_ok_payload())
        api = _FakeMessagesAPI([bad, good])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        summary = await client.summarize(title="t", body="b")

        assert len(api.calls) == 2
        assert summary.summary_lines == ["要約1", "要約2", "要約3"]

    async def test_invalid_twice_raises_llm_error(self) -> None:
        bad = _response_with(_ok_payload(summary_lines=["only-one"]))
        api = _FakeMessagesAPI([bad, bad])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        with pytest.raises(LLMError):
            await client.summarize(title="t", body="b")
        assert len(api.calls) == 2

    async def test_non_json_response_raises_llm_error(self) -> None:
        api = _FakeMessagesAPI(
            [_response_with("no json here at all"), _response_with("still none")]
        )
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        with pytest.raises(LLMError):
            await client.summarize(title="t", body="b")


@pytest.mark.usefixtures("patch_rate_limit_error", "no_sleep")
class TestClaudeLLMClientRateLimit:
    async def test_retries_then_succeeds(self) -> None:
        api = _FakeMessagesAPI(
            [
                _FakeRateLimitError("429"),
                _FakeRateLimitError("429"),
                _response_with(_ok_payload()),
            ]
        )
        client = ClaudeLLMClient(client=_FakeAnthropic(api), max_rate_limit_retries=3)

        summary = await client.summarize(title="t", body="b")

        assert len(api.calls) == 3
        assert summary.priority is Priority.HIGH

    async def test_retries_exhausted_raises_llm_error(self) -> None:
        api = _FakeMessagesAPI([_FakeRateLimitError("429") for _ in range(4)])
        client = ClaudeLLMClient(client=_FakeAnthropic(api), max_rate_limit_retries=3)

        with pytest.raises(LLMError):
            await client.summarize(title="t", body="b")
        assert len(api.calls) == 4


class TestClaudeLLMClientGenericFailure:
    async def test_unexpected_exception_is_wrapped(self) -> None:
        api = _FakeMessagesAPI([RuntimeError("boom")])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        with pytest.raises(LLMError):
            await client.summarize(title="t", body="b")

    async def test_empty_content_raises_llm_error(self) -> None:
        api = _FakeMessagesAPI([_FakeResponse(content=[])])
        client = ClaudeLLMClient(client=_FakeAnthropic(api))

        with pytest.raises(LLMError):
            await client.summarize(title="t", body="b")
