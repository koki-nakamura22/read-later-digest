"""Drift guard: template.yaml Parameter defaults must match config.py defaults.

Three places hold the same set of defaults and they must stay in sync:

  1. `template.yaml` — CloudFormation Parameter `Default:` (Lambda)
  2. `samconfig.toml.tmpl` — `parameter_overrides` (deploy seed + local)
  3. `src/read_later_digest/config.py` — `Config` field defaults (runtime fallback)

This test parses template.yaml's `Parameters:` block with a minimal line-based
parser (no PyYAML dependency, since the file uses CFN intrinsics like `!Ref`)
and asserts each parameter's `Default:` matches the corresponding `Config`
default. Drift in any of the three would cause real runtime divergence
between local and Lambda — the very thing this feature exists to prevent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from read_later_digest.config import Config

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = ROOT / "template.yaml"
SAMCONFIG_TMPL_PATH = ROOT / "samconfig.toml.tmpl"


def _parse_parameter_defaults(text: str) -> dict[str, str]:
    """Extract `{ParameterName: DefaultValueAsString}` from template.yaml.

    Looks at the `Parameters:` block only. Handles quoted ('...') and unquoted
    scalar defaults. Stops at the next top-level key (e.g. `Globals:`).
    """
    out: dict[str, str] = {}
    in_params = False
    current_name: str | None = None

    name_re = re.compile(r"^  ([A-Za-z][A-Za-z0-9]*):\s*$")
    default_re = re.compile(r"^    Default:\s*(.+?)\s*$")

    for raw_line in text.splitlines():
        # Top-level section header (no leading spaces, ends with ':').
        if re.match(r"^[A-Za-z][A-Za-z0-9]*:\s*$", raw_line):
            in_params = raw_line.startswith("Parameters:")
            current_name = None
            continue
        if not in_params:
            continue
        m = name_re.match(raw_line)
        if m:
            current_name = m.group(1)
            continue
        d = default_re.match(raw_line)
        if d and current_name is not None:
            value = d.group(1)
            if (value.startswith("'") and value.endswith("'")) or (
                value.startswith('"') and value.endswith('"')
            ):
                value = value[1:-1]
            out[current_name] = value
    return out


# (PascalCase param name in template.yaml, attribute on Config, expected value type)
EXPECTED: list[tuple[str, str | None, Any]] = [
    ("NotionStatusUnread", "notion_status_unread", str),
    ("NotionStatusProcessed", "notion_status_processed", str),
    ("NotifyChannels", None, str),  # not a single Config attr; checked separately
    ("NotionStatusProperty", "notion_status_property", str),
    ("NotionTypeProperty", "notion_type_property", str),
    ("NotionPriorityProperty", "notion_priority_property", str),
    ("LlmModel", "llm_model", str),
    ("LlmConcurrency", "llm_concurrency", int),
    ("LlmBodyMaxChars", "llm_body_max_chars", int),
    ("LlmMaxRateLimitRetries", "llm_max_rate_limit_retries", int),
    ("LlmInitialBackoffSec", "llm_initial_backoff_sec", float),
    ("FetchTimeoutSec", "fetch_timeout_sec", float),
    ("SlackTimeoutSec", "slack_timeout_sec", float),
]


@pytest.fixture(scope="module")
def template_defaults() -> dict[str, str]:
    return _parse_parameter_defaults(TEMPLATE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config_defaults() -> Config:
    # Config.from_env requires NOTION_DB_ID etc., so we instantiate the
    # dataclass directly — its field defaults are what we want to compare.
    # The minimum required positional args are stubbed with empty strings.
    return Config(
        notion_db_id="",
        notion_token="",
        anthropic_api_key="",
        notification_channels=frozenset(),
    )


class TestTemplateParameterDefaults:
    def test_parser_finds_all_expected_parameters(
        self, template_defaults: dict[str, str]
    ) -> None:
        names = {name for name, _, _ in EXPECTED}
        missing = names - set(template_defaults)
        assert not missing, f"template.yaml is missing Default for: {sorted(missing)}"

    @pytest.mark.parametrize(
        ("param_name", "config_attr", "value_type"),
        [(p, a, t) for p, a, t in EXPECTED if a is not None],
    )
    def test_template_default_matches_config_default(
        self,
        template_defaults: dict[str, str],
        config_defaults: Config,
        param_name: str,
        config_attr: str,
        value_type: type,
    ) -> None:
        raw_template = template_defaults[param_name]
        coerced_template = value_type(raw_template)
        config_value = getattr(config_defaults, config_attr)
        assert coerced_template == config_value, (
            f"template.yaml.Parameters.{param_name}.Default={raw_template!r} "
            f"!= Config.{config_attr}={config_value!r}"
        )

    def test_notify_channels_default_is_mail(self, template_defaults: dict[str, str]) -> None:
        # NotifyChannels has no direct Config attribute; the default lives in
        # Config.from_env via os.environ.get("NOTIFY_CHANNELS", "mail").
        # We only assert template.yaml stays "mail" so Lambda matches local.
        assert template_defaults["NotifyChannels"] == "mail"


class TestSamconfigTmplDriftGuard:
    """samconfig.toml.tmpl と template.yaml / gen-env.py のキー集合一致を検証。

    tmpl の parameter_overrides に Lambda Parameter を追加し忘れる/逆に
    存在しない PascalCase を書いてしまう、といった片側ドリフトを検知する。
    """

    @pytest.fixture(scope="class")
    def tmpl_override_keys(self) -> set[str]:
        import tomllib

        with SAMCONFIG_TMPL_PATH.open("rb") as f:
            data = tomllib.load(f)
        overrides = data["default"]["deploy"]["parameters"]["parameter_overrides"]
        keys: set[str] = set()
        for token in overrides:
            assert isinstance(token, str)
            assert "=" in token, f"malformed override entry: {token!r}"
            key, _ = token.split("=", 1)
            keys.add(key.strip())
        return keys

    def test_tmpl_keys_match_param_to_env(self, tmpl_override_keys: set[str]) -> None:
        # PARAM_TO_ENV is the union of all PascalCase params that should be
        # exposed as Lambda env vars. samconfig.toml.tmpl must seed every one
        # of them in parameter_overrides so a fresh `cp tmpl samconfig.toml`
        # produces a deployable + locally-runnable config without manual edits.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "gen_env_for_tmpl_check", ROOT / "scripts" / "gen-env.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        param_to_env_keys = set(module.PARAM_TO_ENV.keys())

        missing = param_to_env_keys - tmpl_override_keys
        extra = tmpl_override_keys - param_to_env_keys
        assert not missing, f"samconfig.toml.tmpl is missing parameter_overrides for: {sorted(missing)}"
        assert not extra, f"samconfig.toml.tmpl has stale parameter_overrides for: {sorted(extra)}"
