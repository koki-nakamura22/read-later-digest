from __future__ import annotations

import pytest

from read_later_digest.config import Config

REQUIRED_BASE_ENV = {
    "NOTION_DB_ID": "db-id",
    "NOTION_TOKEN": "tok",
    "ANTHROPIC_API_KEY": "anth",
}


def _set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED_BASE_ENV.items():
        monkeypatch.setenv(k, v)


class TestConfigFromEnv:
    def test_required_vars_produce_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)

        cfg = Config.from_env()

        assert cfg.notion_db_id == "db-id"
        assert cfg.notion_token == "tok"
        assert cfg.anthropic_api_key == "anth"

    def test_missing_notion_db_id_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.delenv("NOTION_DB_ID")

        with pytest.raises(KeyError):
            Config.from_env()

    def test_missing_notion_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.delenv("NOTION_TOKEN")

        with pytest.raises(RuntimeError, match="NOTION_TOKEN"):
            Config.from_env()

    def test_missing_anthropic_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.delenv("ANTHROPIC_API_KEY")

        with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
            Config.from_env()

    def test_slack_webhook_url_is_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

        cfg = Config.from_env()

        assert cfg.slack_webhook_url is None

    def test_slack_webhook_url_is_read_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")

        cfg = Config.from_env()

        assert cfg.slack_webhook_url == "https://hooks.slack.com/services/x"

    def test_max_items_per_run_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.delenv("MAX_ITEMS_PER_RUN", raising=False)

        cfg = Config.from_env()

        assert cfg.max_items_per_run == 30

    def test_max_items_per_run_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("MAX_ITEMS_PER_RUN", "10")

        cfg = Config.from_env()

        assert cfg.max_items_per_run == 10

    def test_max_items_per_run_invalid_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("MAX_ITEMS_PER_RUN", "not-a-number")

        with pytest.raises(ValueError):
            Config.from_env()
