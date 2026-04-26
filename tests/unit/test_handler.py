from __future__ import annotations

from typing import Any

import pytest

from read_later_digest.domain.models import RunResult


@pytest.fixture(autouse=True)
def _set_required_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NOTION_DB_ID", "db-1")
    monkeypatch.setenv("NOTION_TOKEN", "tok")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    monkeypatch.setenv("MAIL_FROM", "from@example.com")
    monkeypatch.setenv("MAIL_TO", "to@example.com")


def test_lambda_handler_runs_orchestrator_and_returns_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from read_later_digest import handler as handler_mod

    fake_result = RunResult(
        total_articles=2,
        succeeded=1,
        failed=1,
        notification_sent=True,
        status_updated=1,
        duration_ms=42,
    )

    async def _fake_run(_config: Any) -> RunResult:
        return fake_result

    monkeypatch.setattr(handler_mod, "_run", _fake_run)

    out = handler_mod.lambda_handler({"source": "aws.scheduler"}, object())

    assert out == {
        "total_articles": 2,
        "succeeded": 1,
        "failed": 1,
        "notification_sent": True,
        "status_updated": 1,
        "duration_ms": 42,
    }


def test_lambda_handler_propagates_orchestrator_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from read_later_digest import handler as handler_mod

    async def _boom(_config: Any) -> RunResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(handler_mod, "_run", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        handler_mod.lambda_handler({}, object())
