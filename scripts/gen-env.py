#!/usr/bin/env python3
"""Generate `.env` from `samconfig.toml` for local `uv run` execution.

samconfig.toml is the single source of truth for env vars, driving both:
  - `sam deploy` (via standard [*.deploy.parameters].parameter_overrides)
  - local `uv run` (via this script, which materializes `.env`)

Two sources are merged into `.env`:
  1. parameter_overrides (PascalCase Parameter names) — converted to
     Lambda env-var names (UPPER_SNAKE) using PARAM_TO_ENV below.
     The mapping mirrors template.yaml's Environment.Variables block.
  2. The `[local]` table — local-only env vars (secrets, overrides not
     exposed as Lambda Parameters). Already in UPPER_SNAKE form.

Usage:
    uv run python scripts/gen-env.py
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SAMCONFIG = ROOT / "samconfig.toml"
ENV_FILE = ROOT / ".env"

PARAM_TO_ENV: dict[str, str] = {
    "NotionDbId": "NOTION_DB_ID",
    "NotionToken": "NOTION_TOKEN",
    "AnthropicApiKey": "ANTHROPIC_API_KEY",
    "SlackWebhookUrl": "SLACK_WEBHOOK_URL",
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


def parse_overrides(raw: str | list[str]) -> dict[str, str]:
    tokens = raw if isinstance(raw, list) else raw.split()
    out: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def collect_overrides(data: dict[str, Any]) -> dict[str, str]:
    """Walk every [<env>.<command>.parameters] section and merge their overrides."""
    merged: dict[str, str] = {}
    for env_section in data.values():
        if not isinstance(env_section, dict):
            continue
        for cmd_section in env_section.values():
            if not isinstance(cmd_section, dict):
                continue
            params = cmd_section.get("parameters")
            if not isinstance(params, dict):
                continue
            raw = params.get("parameter_overrides")
            if raw is None:
                continue
            if not isinstance(raw, (str, list)):
                continue
            merged.update(parse_overrides(raw))
    return merged


def quote(value: str) -> str:
    if any(ch in value for ch in " \t\"'#$`\\"):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def main() -> int:
    if not SAMCONFIG.exists():
        print(
            f"error: {SAMCONFIG.name} not found. Run: cp samconfig.toml.tmpl samconfig.toml",
            file=sys.stderr,
        )
        return 1

    with SAMCONFIG.open("rb") as f:
        data = tomllib.load(f)

    env: dict[str, str] = {}
    for param_name, value in collect_overrides(data).items():
        if param_name not in PARAM_TO_ENV:
            continue
        # Empty values (e.g. SlackWebhookUrl="" when slack channel is disabled)
        # must stay out of .env so config.py's `os.environ.get(...) or None`
        # treats them as unset rather than seeing an empty string.
        if value == "":
            continue
        env[PARAM_TO_ENV[param_name]] = value

    local = data.get("local", {})
    if not isinstance(local, dict):
        print("error: [local] must be a table", file=sys.stderr)
        return 1
    for key, value in local.items():
        env[str(key)] = str(value)

    lines = [
        "# AUTO-GENERATED from samconfig.toml. DO NOT EDIT.",
        "# Regenerate via: uv run python scripts/gen-env.py",
        "",
    ]
    lines.extend(f"{key}={quote(env[key])}" for key in sorted(env))
    lines.append("")

    ENV_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {ENV_FILE.relative_to(ROOT)} ({len(env)} vars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
