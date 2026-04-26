"""Unit tests for scripts/gen-env.py.

`gen-env.py` lives outside the package, so we load it via importlib from its
absolute path and exercise the pure helpers (`parse_overrides`,
`collect_overrides`) plus the `PARAM_TO_ENV` mapping. The main()-level file IO
path is covered by exercising the same helpers it composes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
GEN_ENV_PATH = ROOT / "scripts" / "gen-env.py"


def _load_gen_env() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_env", GEN_ENV_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_env"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen_env() -> ModuleType:
    return _load_gen_env()


# ---------------------------------------------------------------------------
# parse_overrides
# ---------------------------------------------------------------------------


class TestParseOverrides:
    def test_list_form_splits_on_equals(self, gen_env: ModuleType) -> None:
        result = gen_env.parse_overrides(["NotionDbId=abc", "MailFrom=x@example.com"])
        assert result == {"NotionDbId": "abc", "MailFrom": "x@example.com"}

    def test_string_form_splits_on_whitespace(self, gen_env: ModuleType) -> None:
        result = gen_env.parse_overrides("NotionDbId=abc MailTo=y@example.com")
        assert result == {"NotionDbId": "abc", "MailTo": "y@example.com"}

    def test_value_with_equals_is_preserved(self, gen_env: ModuleType) -> None:
        # split with maxsplit=1 — the first '=' is the separator.
        result = gen_env.parse_overrides(["LlmModel=claude-sonnet=4"])
        assert result == {"LlmModel": "claude-sonnet=4"}

    def test_token_without_equals_is_ignored(self, gen_env: ModuleType) -> None:
        result = gen_env.parse_overrides(["NotionDbId=abc", "broken-token"])
        assert result == {"NotionDbId": "abc"}

    def test_empty_input_returns_empty(self, gen_env: ModuleType) -> None:
        assert gen_env.parse_overrides([]) == {}
        assert gen_env.parse_overrides("") == {}


# ---------------------------------------------------------------------------
# collect_overrides
# ---------------------------------------------------------------------------


class TestCollectOverrides:
    def test_walks_nested_env_command_parameters(self, gen_env: ModuleType) -> None:
        data = {
            "default": {
                "deploy": {
                    "parameters": {
                        "parameter_overrides": ["NotionDbId=abc", "MailFrom=x@example.com"]
                    }
                }
            }
        }
        assert gen_env.collect_overrides(data) == {
            "NotionDbId": "abc",
            "MailFrom": "x@example.com",
        }

    def test_merges_across_multiple_env_sections(self, gen_env: ModuleType) -> None:
        data = {
            "default": {"deploy": {"parameters": {"parameter_overrides": ["A=1"]}}},
            "prod": {"deploy": {"parameters": {"parameter_overrides": ["B=2"]}}},
        }
        assert gen_env.collect_overrides(data) == {"A": "1", "B": "2"}

    def test_later_override_wins_on_key_collision(self, gen_env: ModuleType) -> None:
        # collect_overrides walks dict.values() in insertion order; the later
        # section's value should overwrite the earlier one. We assert that the
        # function produces *some* deterministic merge (last writer wins).
        data = {
            "a": {"deploy": {"parameters": {"parameter_overrides": ["K=first"]}}},
            "b": {"deploy": {"parameters": {"parameter_overrides": ["K=second"]}}},
        }
        merged = gen_env.collect_overrides(data)
        assert merged["K"] == "second"

    def test_skips_non_dict_or_missing_overrides(self, gen_env: ModuleType) -> None:
        data = {
            "default": {
                "global": {"parameters": {"stack_name": "x"}},  # no parameter_overrides
                "build": {"parameters": {"parameter_overrides": None}},  # wrong type
                "deploy": "not-a-dict",  # skipped entirely
            }
        }
        assert gen_env.collect_overrides(data) == {}

    def test_empty_data_returns_empty(self, gen_env: ModuleType) -> None:
        assert gen_env.collect_overrides({}) == {}


# ---------------------------------------------------------------------------
# PARAM_TO_ENV mapping
# ---------------------------------------------------------------------------

# All Lambda Parameters that must be exposed to local execution with the same
# UPPER_SNAKE env-var name as the Lambda Environment.Variables block.
EXPECTED_MAPPINGS = {
    "NotionDbId": "NOTION_DB_ID",
    "NotionStatusUnread": "NOTION_STATUS_UNREAD",
    "NotionStatusProcessed": "NOTION_STATUS_PROCESSED",
    "MailFrom": "MAIL_FROM",
    "MailTo": "MAIL_TO",
    "NotifyChannels": "NOTIFY_CHANNELS",
    "NotionStatusProperty": "NOTION_STATUS_PROPERTY",
    "NotionTypeProperty": "NOTION_TYPE_PROPERTY",
    "NotionPriorityProperty": "NOTION_PRIORITY_PROPERTY",
    "LlmModel": "LLM_MODEL",
    "LlmConcurrency": "LLM_CONCURRENCY",
    "LlmBodyMaxChars": "LLM_BODY_MAX_CHARS",
    "LlmMaxRateLimitRetries": "LLM_MAX_RATE_LIMIT_RETRIES",
    "LlmInitialBackoffSec": "LLM_INITIAL_BACKOFF_SEC",
    "FetchTimeoutSec": "FETCH_TIMEOUT_SEC",
    "SlackTimeoutSec": "SLACK_TIMEOUT_SEC",
}


class TestParamToEnvMapping:
    @pytest.mark.parametrize(("pascal", "upper_snake"), list(EXPECTED_MAPPINGS.items()))
    def test_each_expected_mapping_is_present(
        self, gen_env: ModuleType, pascal: str, upper_snake: str
    ) -> None:
        assert gen_env.PARAM_TO_ENV[pascal] == upper_snake

    def test_no_unexpected_mappings(self, gen_env: ModuleType) -> None:
        # Detects accidental drift: anything added to PARAM_TO_ENV must also
        # be reflected in EXPECTED_MAPPINGS (and in template.yaml / tmpl).
        assert set(gen_env.PARAM_TO_ENV.keys()) == set(EXPECTED_MAPPINGS.keys())


# ---------------------------------------------------------------------------
# quote
# ---------------------------------------------------------------------------


class TestQuote:
    @pytest.mark.parametrize(
        "value",
        ["plain", "claude-sonnet-4-6", "30000", "1.0"],
    )
    def test_unquoted_for_safe_values(self, gen_env: ModuleType, value: str) -> None:
        assert gen_env.quote(value) == value

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("with space", '"with space"'),
            ('has"quote', '"has\\"quote"'),
            ("has#hash", '"has#hash"'),
            ("has$dollar", '"has$dollar"'),
        ],
    )
    def test_quoted_and_escaped_for_unsafe_values(
        self, gen_env: ModuleType, value: str, expected: str
    ) -> None:
        assert gen_env.quote(value) == expected
