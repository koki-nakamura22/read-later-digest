from __future__ import annotations

import pytest
from pydantic import ValidationError

from read_later_digest.adapters.llm.schema import SummaryPayload
from read_later_digest.domain.models import ArticleType, Priority


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "summary_lines": ["a", "b", "c"],
        "key_points": ["p1", "p2", "p3"],
        "type": "技術",
        "priority": "高",
    }
    base.update(overrides)
    return base


class TestSummaryPayload:
    def test_valid_payload_parses_enums(self) -> None:
        payload = SummaryPayload(**_valid_kwargs())
        assert payload.type is ArticleType.TECH
        assert payload.priority is Priority.HIGH

    def test_summary_lines_must_be_exactly_three(self) -> None:
        with pytest.raises(ValidationError):
            SummaryPayload(**_valid_kwargs(summary_lines=["a", "b"]))
        with pytest.raises(ValidationError):
            SummaryPayload(**_valid_kwargs(summary_lines=["a", "b", "c", "d"]))

    @pytest.mark.parametrize("count", [2, 6])
    def test_key_points_out_of_range_rejected(self, count: int) -> None:
        with pytest.raises(ValidationError):
            SummaryPayload(**_valid_kwargs(key_points=[f"p{i}" for i in range(count)]))

    @pytest.mark.parametrize("count", [3, 4, 5])
    def test_key_points_within_range_accepted(self, count: int) -> None:
        payload = SummaryPayload(**_valid_kwargs(key_points=[f"p{i}" for i in range(count)]))
        assert len(payload.key_points) == count

    def test_empty_string_in_summary_lines_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SummaryPayload(**_valid_kwargs(summary_lines=["a", "  ", "c"]))

    def test_unknown_type_value_is_coerced_to_none(self) -> None:
        payload = SummaryPayload(**_valid_kwargs(type="不明"))
        assert payload.type is None

    def test_unknown_priority_value_is_coerced_to_none(self) -> None:
        payload = SummaryPayload(**_valid_kwargs(priority="ヤバい"))
        assert payload.priority is None

    def test_explicit_null_type_and_priority(self) -> None:
        payload = SummaryPayload(**_valid_kwargs(type=None, priority=None))
        assert payload.type is None
        assert payload.priority is None

    def test_strips_whitespace_in_entries(self) -> None:
        payload = SummaryPayload(**_valid_kwargs(summary_lines=["  a ", "b", "c  "]))
        assert payload.summary_lines == ["a", "b", "c"]
