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
    "NotionToken": "NOTION_TOKEN",
    "AnthropicApiKey": "ANTHROPIC_API_KEY",
    "SlackWebhookUrl": "SLACK_WEBHOOK_URL",
    "NotionStatusUnread": "NOTION_STATUS_UNREAD",
    "NotionStatusProcessed": "NOTION_STATUS_PROCESSED",
    "MailFrom": "MAIL_FROM",
    "MailTo": "MAIL_TO",
    "NotifyChannels": "NOTIFY_CHANNELS",
    "NotifyGranularityMail": "NOTIFY_GRANULARITY_MAIL",
    "NotifyGranularitySlack": "NOTIFY_GRANULARITY_SLACK",
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


# ---------------------------------------------------------------------------
# main() — integration: samconfig.toml -> .env
# ---------------------------------------------------------------------------


SAMPLE_SAMCONFIG = """\
version = 0.1

[default.deploy.parameters]
parameter_overrides = [
    "NotionDbId=db123",
    "NotionToken=secret_abc",
    "AnthropicApiKey=sk-ant-zzz",
    "SlackWebhookUrl=",
    "MailFrom=from@example.com",
    "MailTo=to@example.com",
    "NotifyChannels=mail",
    "NotifyGranularityMail=digest",
    "NotifyGranularitySlack=digest",
    "NotionStatusUnread=未読",
    "NotionStatusProcessed=処理済み",
    "NotionStatusProperty=Status",
    "NotionTypeProperty=Type",
    "NotionPriorityProperty=Priority",
    "LlmModel=claude-sonnet-4-6",
    "LlmConcurrency=5",
    "LlmBodyMaxChars=30000",
    "LlmMaxRateLimitRetries=3",
    "LlmInitialBackoffSec=1.0",
    "FetchTimeoutSec=15.0",
    "SlackTimeoutSec=10.0",
    "LambdaTimeoutSeconds=600",
]

[local]
AWS_REGION = "ap-northeast-1"
"""


def _parse_dotenv(text: str) -> dict[str, str]:
    """Tiny .env parser sufficient for asserting on gen-env.py output."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        out[key] = value
    return out


class TestMainIntegration:
    """Drive main() against an isolated samconfig.toml and inspect the .env."""

    def _run(self, gen_env: ModuleType, tmp_path: Path) -> dict[str, str]:
        sam = tmp_path / "samconfig.toml"
        env_file = tmp_path / ".env"
        sam.write_text(SAMPLE_SAMCONFIG, encoding="utf-8")

        # main() reads module-level SAMCONFIG / ENV_FILE / ROOT — redirect all.
        # ROOT is used by main() for a cosmetic relative_to() in its log line,
        # so it must contain the env_file path or the call raises.
        original_root = gen_env.ROOT
        original_sam = gen_env.SAMCONFIG
        original_env = gen_env.ENV_FILE
        gen_env.ROOT = tmp_path
        gen_env.SAMCONFIG = sam
        gen_env.ENV_FILE = env_file
        try:
            rc = gen_env.main()
        finally:
            gen_env.ROOT = original_root
            gen_env.SAMCONFIG = original_sam
            gen_env.ENV_FILE = original_env
        assert rc == 0
        return _parse_dotenv(env_file.read_text(encoding="utf-8"))

    def test_secrets_emitted_in_upper_snake(self, gen_env: ModuleType, tmp_path: Path) -> None:
        env = self._run(gen_env, tmp_path)
        assert env["NOTION_TOKEN"] == "secret_abc"
        assert env["ANTHROPIC_API_KEY"] == "sk-ant-zzz"

    def test_empty_value_param_is_skipped(self, gen_env: ModuleType, tmp_path: Path) -> None:
        # SlackWebhookUrl="" must NOT produce SLACK_WEBHOOK_URL= in .env, so
        # config.py's `os.environ.get(...) or None` treats it as unset rather
        # than as an empty string (which would pass the "set" check).
        env = self._run(gen_env, tmp_path)
        assert "SLACK_WEBHOOK_URL" not in env

    def test_local_section_is_merged(self, gen_env: ModuleType, tmp_path: Path) -> None:
        env = self._run(gen_env, tmp_path)
        assert env["AWS_REGION"] == "ap-northeast-1"

    def test_unmapped_parameter_is_omitted(self, gen_env: ModuleType, tmp_path: Path) -> None:
        # LambdaTimeoutSeconds is not in PARAM_TO_ENV (it shapes the stack,
        # not the runtime). It must not bleed into .env as a stray var.
        env = self._run(gen_env, tmp_path)
        assert "LAMBDA_TIMEOUT_SECONDS" not in env
        assert "LambdaTimeoutSeconds" not in env

    def test_mapped_non_secret_value_round_trips(self, gen_env: ModuleType, tmp_path: Path) -> None:
        env = self._run(gen_env, tmp_path)
        assert env["NOTION_DB_ID"] == "db123"
        assert env["MAIL_FROM"] == "from@example.com"
        assert env["NOTIFY_CHANNELS"] == "mail"
