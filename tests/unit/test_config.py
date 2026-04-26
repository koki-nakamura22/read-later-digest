from __future__ import annotations

import pytest

from read_later_digest.config import (
    Config,
    NotificationChannel,
    NotifyGranularity,
    _parse_notification_channels,
    _parse_notify_granularity,
)

REQUIRED_BASE_ENV = {
    "NOTION_DB_ID": "db-id",
    "NOTION_TOKEN": "tok",
    "ANTHROPIC_API_KEY": "anth",
}


def _set_base_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in REQUIRED_BASE_ENV.items():
        monkeypatch.setenv(k, v)


class TestParseNotificationChannels:
    def test_default_single_mail(self) -> None:
        assert _parse_notification_channels("mail") == frozenset({NotificationChannel.MAIL})

    def test_single_slack(self) -> None:
        assert _parse_notification_channels("slack") == frozenset({NotificationChannel.SLACK})

    def test_combined_mail_slack(self) -> None:
        assert _parse_notification_channels("mail,slack") == frozenset(
            {NotificationChannel.MAIL, NotificationChannel.SLACK}
        )

    def test_whitespace_and_case_are_normalized(self) -> None:
        assert _parse_notification_channels(" Mail , SLACK ") == frozenset(
            {NotificationChannel.MAIL, NotificationChannel.SLACK}
        )

    def test_duplicate_tokens_dedup(self) -> None:
        assert _parse_notification_channels("mail,mail,slack") == frozenset(
            {NotificationChannel.MAIL, NotificationChannel.SLACK}
        )

    def test_empty_string_raises(self) -> None:
        with pytest.raises(RuntimeError, match="NOTIFY_CHANNELS' is empty"):
            _parse_notification_channels("")

    def test_only_whitespace_raises(self) -> None:
        with pytest.raises(RuntimeError, match="NOTIFY_CHANNELS' is empty"):
            _parse_notification_channels(" , , ")

    def test_unknown_channel_raises_with_listing(self) -> None:
        with pytest.raises(RuntimeError, match=r"unknown notification channels.*\['line'\]"):
            _parse_notification_channels("mail,line")


class TestConfigFromEnvMailOnly:
    def test_default_channels_is_mail_and_requires_mail_envs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.delenv("NOTIFY_CHANNELS", raising=False)
        monkeypatch.setenv("MAIL_FROM", "from@example.com")
        monkeypatch.setenv("MAIL_TO", "to@example.com")

        cfg = Config.from_env()
        assert cfg.notification_channels == frozenset({NotificationChannel.MAIL})
        assert cfg.mail_from == "from@example.com"
        assert cfg.mail_to == ["to@example.com"]
        assert cfg.slack_webhook_url is None

    def test_mail_channel_without_mail_from_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("NOTIFY_CHANNELS", "mail")
        monkeypatch.delenv("MAIL_FROM", raising=False)
        monkeypatch.setenv("MAIL_TO", "to@example.com")

        with pytest.raises(RuntimeError, match="'MAIL_FROM' is not set"):
            Config.from_env()

    def test_mail_channel_with_empty_mail_to_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("NOTIFY_CHANNELS", "mail")
        monkeypatch.setenv("MAIL_FROM", "from@example.com")
        monkeypatch.setenv("MAIL_TO", "")

        with pytest.raises(RuntimeError, match="'MAIL_TO' is empty"):
            Config.from_env()


class TestConfigFromEnvSlackOnly:
    def test_slack_only_does_not_require_mail_envs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("NOTIFY_CHANNELS", "slack")
        monkeypatch.delenv("MAIL_FROM", raising=False)
        monkeypatch.delenv("MAIL_TO", raising=False)
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/x")

        cfg = Config.from_env()
        assert cfg.notification_channels == frozenset({NotificationChannel.SLACK})
        assert cfg.mail_from == ""
        assert cfg.mail_to == []
        assert cfg.slack_webhook_url == "https://hooks.slack.com/services/x"

    def test_slack_channel_without_webhook_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("NOTIFY_CHANNELS", "slack")
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

        with pytest.raises(RuntimeError, match="'SLACK_WEBHOOK_URL' is not set"):
            Config.from_env()


class TestParseNotifyGranularity:
    def test_default_value_is_digest_when_explicit(self) -> None:
        assert _parse_notify_granularity("digest") is NotifyGranularity.DIGEST

    def test_per_article_value(self) -> None:
        assert _parse_notify_granularity("per_article") is NotifyGranularity.PER_ARTICLE

    def test_whitespace_and_case_normalized(self) -> None:
        assert _parse_notify_granularity("  PER_ARTICLE  ") is NotifyGranularity.PER_ARTICLE

    def test_empty_raises(self) -> None:
        with pytest.raises(RuntimeError, match="NOTIFY_GRANULARITY' is empty"):
            _parse_notify_granularity("   ")

    def test_unknown_raises_with_listing(self) -> None:
        with pytest.raises(RuntimeError, match="unknown NOTIFY_GRANULARITY value 'bulk'"):
            _parse_notify_granularity("bulk")


class TestConfigNotifyGranularity:
    def test_default_is_digest_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("MAIL_FROM", "from@example.com")
        monkeypatch.setenv("MAIL_TO", "to@example.com")
        monkeypatch.delenv("NOTIFY_GRANULARITY", raising=False)

        cfg = Config.from_env()
        assert cfg.notify_granularity is NotifyGranularity.DIGEST

    def test_per_article_when_env_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("MAIL_FROM", "from@example.com")
        monkeypatch.setenv("MAIL_TO", "to@example.com")
        monkeypatch.setenv("NOTIFY_GRANULARITY", "per_article")

        cfg = Config.from_env()
        assert cfg.notify_granularity is NotifyGranularity.PER_ARTICLE


class TestConfigFromEnvCombined:
    def test_mail_and_slack_requires_both_sets_of_envs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("NOTIFY_CHANNELS", "mail,slack")
        monkeypatch.setenv("MAIL_FROM", "from@example.com")
        monkeypatch.setenv("MAIL_TO", "to@example.com")
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/y")

        cfg = Config.from_env()
        assert cfg.notification_channels == frozenset(
            {NotificationChannel.MAIL, NotificationChannel.SLACK}
        )
        assert cfg.mail_from == "from@example.com"
        assert cfg.mail_to == ["to@example.com"]
        assert cfg.slack_webhook_url == "https://hooks.slack.com/services/y"

    def test_combined_missing_slack_webhook_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_base_env(monkeypatch)
        monkeypatch.setenv("NOTIFY_CHANNELS", "mail,slack")
        monkeypatch.setenv("MAIL_FROM", "from@example.com")
        monkeypatch.setenv("MAIL_TO", "to@example.com")
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

        with pytest.raises(RuntimeError, match="'SLACK_WEBHOOK_URL' is not set"):
            Config.from_env()
