from __future__ import annotations

import logging

import pytest

from read_later_digest.domain.models import ReadLaterRunResult


@pytest.fixture(autouse=True)
def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_DB_ID", "db-1")
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    monkeypatch.setenv("NOTIFY_CHANNELS", "slack")
    # MAIL_* は設定しない (項目 04)


class _FakeLambdaContext:
    function_name = "test-fn"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-fn"
    aws_request_id = "req-id-1"


def test_lambda_handler_returns_six_field_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    from read_later_digest import handler as handler_mod

    fake = ReadLaterRunResult(2, 1, 1, True, 1, 42)
    monkeypatch.setattr(handler_mod, "_run", lambda c: fake)

    out = handler_mod.lambda_handler({"source": "aws.scheduler"}, _FakeLambdaContext())
    assert out == {
        "total_articles": 2,
        "succeeded": 1,
        "failed": 1,
        "notification_sent": True,
        "status_updated": 1,
        "duration_ms": 42,
    }


def test_attach_powertools_handler_to_digestkit_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from read_later_digest.handler import _attach_powertools_handler_to_digestkit
    from read_later_digest.logging_setup import logger as powertools_logger

    logging.getLogger("digestkit").handlers.clear()

    _attach_powertools_handler_to_digestkit(powertools_logger)
    first_count = len(logging.getLogger("digestkit").handlers)

    _attach_powertools_handler_to_digestkit(powertools_logger)
    second_count = len(logging.getLogger("digestkit").handlers)

    assert first_count == second_count > 0
