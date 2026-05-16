from __future__ import annotations

import logging
from collections.abc import Generator
from unittest.mock import MagicMock

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


@pytest.fixture()
def _clean_digestkit_logger() -> Generator[None]:
    """digestkit グローバル logger 状態をテスト前後でリセットし、順序依存を防ぐ."""
    root = logging.getLogger("digestkit")
    saved_handlers = list(root.handlers)
    saved_propagate = root.propagate
    root.handlers.clear()
    yield
    root.handlers.clear()
    root.handlers.extend(saved_handlers)
    root.propagate = saved_propagate


class _FakeLambdaContext:
    function_name = "test-fn"
    memory_limit_in_mb = 128
    invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-fn"
    aws_request_id = "req-id-1"


# ---------------------------------------------------------------------------
# lambda_handler
# ---------------------------------------------------------------------------


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


def test_lambda_handler_propagates_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from read_later_digest import handler as handler_mod

    def _boom(c: object) -> ReadLaterRunResult:
        raise RuntimeError("boom")

    monkeypatch.setattr(handler_mod, "_run", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        handler_mod.lambda_handler({}, _FakeLambdaContext())


# ---------------------------------------------------------------------------
# _run — extractor.close() の finally 保証
# ---------------------------------------------------------------------------


def test_run_calls_extractor_close_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from read_later_digest import handler as handler_mod

    fake_result = ReadLaterRunResult(0, 0, 0, False, 0, 1)
    mock_extractor = MagicMock()
    mock_digester = MagicMock()
    mock_digester.run.return_value = fake_result
    mock_digester.extractor = mock_extractor

    monkeypatch.setattr(handler_mod, "build_digester", lambda c: mock_digester)

    handler_mod._run(MagicMock())

    mock_extractor.close.assert_called_once()


def test_run_calls_extractor_close_even_when_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from read_later_digest import handler as handler_mod

    mock_extractor = MagicMock()
    mock_digester = MagicMock()
    mock_digester.run.side_effect = RuntimeError("run failed")
    mock_digester.extractor = mock_extractor

    monkeypatch.setattr(handler_mod, "build_digester", lambda c: mock_digester)

    with pytest.raises(RuntimeError, match="run failed"):
        handler_mod._run(MagicMock())

    mock_extractor.close.assert_called_once()


def test_run_skips_close_if_extractor_has_no_close_attr(monkeypatch: pytest.MonkeyPatch) -> None:
    from read_later_digest import handler as handler_mod

    fake_result = ReadLaterRunResult(0, 0, 0, False, 0, 1)
    mock_digester = MagicMock()
    mock_digester.run.return_value = fake_result
    mock_digester.extractor = object()  # close 属性なし

    monkeypatch.setattr(handler_mod, "build_digester", lambda c: mock_digester)

    result = handler_mod._run(MagicMock())

    assert result == fake_result


# ---------------------------------------------------------------------------
# _attach_powertools_handler_to_digestkit
# ---------------------------------------------------------------------------


def test_attach_powertools_handler_to_digestkit_is_idempotent(
    _clean_digestkit_logger: None,
) -> None:
    from read_later_digest.handler import _attach_powertools_handler_to_digestkit
    from read_later_digest.logging_setup import logger as powertools_logger

    _attach_powertools_handler_to_digestkit(powertools_logger)
    first_count = len(logging.getLogger("digestkit").handlers)

    _attach_powertools_handler_to_digestkit(powertools_logger)
    second_count = len(logging.getLogger("digestkit").handlers)

    assert first_count == second_count > 0


def test_attach_sets_digestkit_root_propagate_false(
    _clean_digestkit_logger: None,
) -> None:
    from read_later_digest.handler import _attach_powertools_handler_to_digestkit
    from read_later_digest.logging_setup import logger as powertools_logger

    digestkit_root = logging.getLogger("digestkit")
    digestkit_root.propagate = True

    _attach_powertools_handler_to_digestkit(powertools_logger)

    assert digestkit_root.propagate is False


def test_attach_clears_child_logger_handlers_and_sets_propagate_true(
    _clean_digestkit_logger: None,
) -> None:
    from read_later_digest.handler import _attach_powertools_handler_to_digestkit
    from read_later_digest.logging_setup import logger as powertools_logger

    child = logging.getLogger("digestkit._test_child_unique")
    child.addHandler(logging.NullHandler())
    child.propagate = False

    _attach_powertools_handler_to_digestkit(powertools_logger)

    assert child.handlers == []
    assert child.propagate is True
