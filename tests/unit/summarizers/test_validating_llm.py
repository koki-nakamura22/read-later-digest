from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
from digestkit.summarizers import LLMSummarizer
from digestkit.types import Digest as DigestkitDigest
from digestkit.types import Item
from pydantic import ValidationError

from read_later_digest.domain.models import ArticleType, Priority
from read_later_digest.exceptions import LLMError
from read_later_digest.summarizers.validating_llm import (
    ValidatingLLMSummarizer,
    _extract_json,
    parse_summary,
)

# ---------- helpers ----------


def _digest(summary: str) -> DigestkitDigest:
    return DigestkitDigest(summary=summary, tokens_in=0, tokens_out=0, latency_ms=0, model="test")


def _item() -> Item:
    return Item(id="i1", payload=None)


def _valid_json(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "summary_lines": ["a", "b", "c"],
        "key_points": ["x", "y", "z"],
    }
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


def _inner_mock() -> MagicMock:
    return MagicMock(spec=LLMSummarizer)


# ---------- _extract_json ----------


class TestExtractJson:
    def test_returns_bare_json_object_unchanged(self) -> None:
        # Arrange
        raw = '{"key": "value"}'
        # Act
        result = _extract_json(raw)
        # Assert
        assert '"key"' in result
        assert '"value"' in result

    def test_extracts_json_from_surrounding_prose(self) -> None:
        # Arrange
        raw = '前置き\n{"key": "value"}\n後置き'
        # Act
        result = _extract_json(raw)
        # Assert
        assert '"key"' in result
        assert '"value"' in result

    def test_raises_llm_error_when_no_json_object_found(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(LLMError, match="no JSON object found"):
            _extract_json("plain text without braces")

    def test_raises_llm_error_for_empty_string(self) -> None:
        # Arrange / Act / Assert
        with pytest.raises(LLMError, match="no JSON object found"):
            _extract_json("")

    def test_extracts_multiline_json(self) -> None:
        # Arrange
        raw = '{\n  "summary_lines": ["a", "b"]\n}'
        # Act
        result = _extract_json(raw)
        # Assert
        assert "summary_lines" in result


# ---------- parse_summary ----------


class TestParseSummary:
    def test_returns_article_summary_with_correct_summary_lines(self) -> None:
        # Arrange
        digest = _digest('{"summary_lines": ["a", "b", "c"], "key_points": ["x"]}')
        # Act
        result = parse_summary(digest)
        # Assert
        assert result.summary_lines == ["a", "b", "c"]

    def test_returns_article_summary_with_correct_key_points(self) -> None:
        # Arrange
        digest = _digest('{"summary_lines": ["a"], "key_points": ["x", "y"]}')
        # Act
        result = parse_summary(digest)
        # Assert
        assert result.key_points == ["x", "y"]

    def test_type_alias_in_json_maps_to_type_field(self) -> None:
        # Arrange — LLM outputs "type" per system prompt schema
        digest = _digest(
            '{"summary_lines": ["a"], "key_points": ["b"], "type": "技術", "priority": "高"}'
        )
        # Act
        result = parse_summary(digest)
        # Assert
        assert result.type_ is ArticleType.TECH
        assert result.priority is Priority.HIGH

    def test_type_underscore_key_accepted_via_populate_by_name(self) -> None:
        # Arrange — "type_" (Python field name) works because populate_by_name=True
        digest = _digest(
            '{"summary_lines": ["a", "b", "c"], "key_points": ["x"], "type_": "記事", "priority": "中"}'
        )
        # Act
        result = parse_summary(digest)
        # Assert
        assert result.type_ is ArticleType.ARTICLE
        assert result.priority is Priority.MID

    def test_unknown_type_string_becomes_none_via_lenient_coercion(self) -> None:
        # Arrange — unrecognized enum value → None (not a validation error)
        digest = _digest(
            '{"summary_lines": ["a"], "key_points": ["b"], "type": "unknown", "priority": "unknown"}'
        )
        # Act
        result = parse_summary(digest)
        # Assert
        assert result.type_ is None
        assert result.priority is None

    def test_null_type_and_priority_are_stored_as_none(self) -> None:
        # Arrange
        digest = _digest(
            '{"summary_lines": ["a"], "key_points": ["b"], "type": null, "priority": null}'
        )
        # Act
        result = parse_summary(digest)
        # Assert
        assert result.type_ is None
        assert result.priority is None

    def test_no_json_in_summary_raises_llm_error(self) -> None:
        # Arrange
        digest = _digest("no json object here")
        # Act / Assert
        with pytest.raises(LLMError, match="no JSON object found"):
            parse_summary(digest)

    def test_missing_required_field_raises_validation_error(self) -> None:
        # Arrange — summary_lines is required
        digest = _digest('{"key_points": ["x"]}')
        # Act / Assert
        with pytest.raises(ValidationError):
            parse_summary(digest)

    def test_returns_article_summary_instance(self) -> None:
        # Arrange
        from read_later_digest.domain.models import ArticleSummary

        digest = _digest('{"summary_lines": ["a", "b", "c"], "key_points": ["x"]}')
        # Act
        result = parse_summary(digest)
        # Assert
        assert isinstance(result, ArticleSummary)


# ---------- ValidatingLLMSummarizer ----------


class TestValidatingLLMSummarizerSummarize:
    def test_returns_digest_immediately_when_first_response_is_valid(self) -> None:
        # Arrange
        inner = _inner_mock()
        valid_digest = _digest(_valid_json())
        inner.summarize.return_value = valid_digest
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act
        result = summarizer.summarize("article text", _item())
        # Assert
        assert result is valid_digest
        assert inner.summarize.call_count == 1

    def test_retries_once_when_first_response_is_invalid(self) -> None:
        # Arrange
        inner = _inner_mock()
        invalid_digest = _digest("not json")
        valid_digest = _digest(_valid_json())
        inner.summarize.side_effect = [invalid_digest, valid_digest]
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act
        result = summarizer.summarize("article text", _item())
        # Assert
        assert result is valid_digest
        assert inner.summarize.call_count == 2

    def test_raises_llm_error_when_all_retries_exhausted(self) -> None:
        # Arrange
        inner = _inner_mock()
        inner.summarize.return_value = _digest("not json")
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act / Assert
        with pytest.raises(LLMError, match="schema invalid"):
            summarizer.summarize("article text", _item())
        assert inner.summarize.call_count == 2  # initial + 1 retry

    def test_error_message_includes_total_attempt_count(self) -> None:
        # Arrange — max_schema_retries=2 → 3 total attempts
        inner = _inner_mock()
        inner.summarize.return_value = _digest("not json")
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=2)
        # Act / Assert
        with pytest.raises(LLMError, match="3 attempts"):
            summarizer.summarize("article text", _item())
        assert inner.summarize.call_count == 3

    def test_no_retry_when_max_schema_retries_is_zero(self) -> None:
        # Arrange
        inner = _inner_mock()
        inner.summarize.return_value = _digest("not json")
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=0)
        # Act / Assert
        with pytest.raises(LLMError, match="schema invalid"):
            summarizer.summarize("article text", _item())
        assert inner.summarize.call_count == 1

    def test_raises_llm_error_when_json_present_but_schema_invalid(self) -> None:
        # Arrange — JSON object found but missing required field "summary_lines"
        inner = _inner_mock()
        inner.summarize.return_value = _digest('{"key_points": ["x"]}')
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act / Assert
        with pytest.raises(LLMError, match="schema invalid"):
            summarizer.summarize("article text", _item())
        assert inner.summarize.call_count == 2

    def test_default_max_schema_retries_is_one(self) -> None:
        # Arrange — no explicit max_schema_retries → default is 1
        inner = _inner_mock()
        inner.summarize.return_value = _digest("not json")
        summarizer = ValidatingLLMSummarizer(inner)
        # Act / Assert
        with pytest.raises(LLMError):
            summarizer.summarize("article text", _item())
        assert inner.summarize.call_count == 2  # 1 + default 1 retry

    def test_inner_exception_propagates_without_schema_retry(self) -> None:
        # Arrange — RuntimeError from inner is not a schema error; must not be retried
        inner = _inner_mock()
        inner.summarize.side_effect = RuntimeError("inner failure")
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act / Assert
        with pytest.raises(RuntimeError, match="inner failure"):
            summarizer.summarize("article text", _item())
        assert inner.summarize.call_count == 1

    def test_length_kwarg_is_accepted(self) -> None:
        # Arrange — length is an optional kwarg; must not raise TypeError
        inner = _inner_mock()
        inner.summarize.return_value = _digest(_valid_json())
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act / Assert — no TypeError
        result = summarizer.summarize("article text", _item(), length="long")
        assert result is not None


class TestValidatingLLMSummarizerLogging:
    def test_warning_is_logged_on_schema_validation_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Arrange
        from read_later_digest.summarizers import validating_llm as vllm_module

        captured: list[tuple[str, dict[str, Any]]] = []

        def _capture(msg: str, **kwargs: Any) -> None:
            captured.append((msg, kwargs.get("extra", {})))

        monkeypatch.setattr(vllm_module.logger, "warning", _capture)
        inner = _inner_mock()
        invalid_digest = _digest("not json")
        valid_digest = _digest(_valid_json())
        inner.summarize.side_effect = [invalid_digest, valid_digest]
        summarizer = ValidatingLLMSummarizer(inner, max_schema_retries=1)
        # Act
        summarizer.summarize("article text", _item())
        # Assert
        assert len(captured) == 1
        msg, extra = captured[0]
        assert msg == "schema validation failed"
        assert extra.get("attempt") == 1
        assert extra.get("item_id") == "i1"
