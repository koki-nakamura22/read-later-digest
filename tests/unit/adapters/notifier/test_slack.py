from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from read_later_digest.adapters.notifier.slack import SlackNotifier
from read_later_digest.exceptions import NotifierError

WEBHOOK_URL = "https://hooks.slack.com/services/T000/B000/XXXXSECRETXXXX"


class TestSlackNotifierInit:
    async def test_empty_webhook_url_raises_value_error(self) -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="webhook_url is empty"):
                SlackNotifier(client=client, webhook_url="")


class TestSlackNotifierSend:
    @respx.mock
    async def test_success_posts_payload_with_subject_and_text(self) -> None:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, text="ok"))

        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            await notifier.send(subject="Daily digest", text="3 articles")

        assert route.call_count == 1
        sent = route.calls.last.request
        import json

        body = json.loads(sent.content)
        assert body == {"text": "*Daily digest*\n3 articles"}

    @respx.mock
    async def test_4xx_response_raises_notifier_error(self) -> None:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(400, text="invalid_payload"))

        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            with pytest.raises(NotifierError, match="non-2xx status: 400"):
                await notifier.send(subject="s", text="t")

    @respx.mock
    async def test_5xx_response_raises_notifier_error(self) -> None:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(503, text="unavailable"))

        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            with pytest.raises(NotifierError, match="non-2xx status: 503"):
                await notifier.send(subject="s", text="t")

    @respx.mock
    async def test_network_error_is_wrapped_in_notifier_error(self) -> None:
        respx.post(WEBHOOK_URL).mock(side_effect=httpx.ConnectError("boom"))

        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            with pytest.raises(NotifierError) as exc_info:
                await notifier.send(subject="s", text="t")
            # Underlying httpx.HTTPError preserved as __cause__.
            assert isinstance(exc_info.value.__cause__, httpx.HTTPError)
            # Webhook URL must not leak in the surfaced message.
            assert WEBHOOK_URL not in str(exc_info.value)

    @respx.mock
    async def test_timeout_is_wrapped_in_notifier_error(self) -> None:
        respx.post(WEBHOOK_URL).mock(side_effect=httpx.TimeoutException("slow"))

        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            with pytest.raises(NotifierError):
                await notifier.send(subject="s", text="t")

    async def test_empty_subject_raises_and_does_not_call_http(self) -> None:
        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            with respx.mock(assert_all_called=False) as mock:
                route = mock.post(WEBHOOK_URL)
                with pytest.raises(NotifierError, match="subject is empty"):
                    await notifier.send(subject="", text="t")
                assert route.call_count == 0

    async def test_empty_text_raises_and_does_not_call_http(self) -> None:
        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            with respx.mock(assert_all_called=False) as mock:
                route = mock.post(WEBHOOK_URL)
                with pytest.raises(NotifierError, match="text is empty"):
                    await notifier.send(subject="s", text="")
                assert route.call_count == 0

    @respx.mock
    async def test_success_log_omits_webhook_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from read_later_digest.adapters.notifier import slack as slack_module

        captured: list[tuple[str, dict[str, Any]]] = []

        def _capture(msg: str, **kwargs: Any) -> None:
            captured.append((msg, kwargs.get("extra", {})))

        monkeypatch.setattr(slack_module.logger, "info", _capture)
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, text="ok"))

        async with httpx.AsyncClient() as client:
            notifier = SlackNotifier(client=client, webhook_url=WEBHOOK_URL)
            await notifier.send(subject="hello", text="world!")

        assert len(captured) == 1
        msg, extra = captured[0]
        assert msg == "slack notification sent"
        assert extra == {
            "subject_len": len("hello"),
            "text_len": len("world!"),
            "status_code": 200,
        }
        flat = repr((msg, extra))
        assert WEBHOOK_URL not in flat
        assert "XXXXSECRETXXXX" not in flat
