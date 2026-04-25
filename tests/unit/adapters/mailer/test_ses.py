from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from read_later_digest.adapters.mailer.ses import SesMailer
from read_later_digest.exceptions import MailerError


class FakeSesClient:
    def __init__(self, *, raise_error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raise_error = raise_error

    def send_email(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self._raise_error is not None:
            raise self._raise_error
        return {"MessageId": "fake-msg-id"}


def _make_mailer(client: FakeSesClient | None = None) -> tuple[SesMailer, FakeSesClient]:
    fake = client or FakeSesClient()
    return SesMailer(client=fake, source="from@example.com"), fake


class TestSesMailerSend:
    async def test_single_recipient_invokes_ses_with_expected_payload(self) -> None:
        mailer, fake = _make_mailer()
        await mailer.send(
            to=["alice@example.com"],
            subject="Daily digest",
            html="<p>hi</p>",
            text="hi",
        )

        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["Source"] == "from@example.com"
        assert call["Destination"] == {"ToAddresses": ["alice@example.com"]}
        assert call["Message"]["Subject"] == {"Data": "Daily digest", "Charset": "UTF-8"}
        assert call["Message"]["Body"]["Html"] == {"Data": "<p>hi</p>", "Charset": "UTF-8"}
        assert call["Message"]["Body"]["Text"] == {"Data": "hi", "Charset": "UTF-8"}

    async def test_multiple_recipients_pass_all_addresses(self) -> None:
        mailer, fake = _make_mailer()
        await mailer.send(
            to=["a@example.com", "b@example.com"],
            subject="s",
            html="<p>x</p>",
            text="x",
        )

        assert fake.calls[0]["Destination"]["ToAddresses"] == [
            "a@example.com",
            "b@example.com",
        ]

    async def test_client_error_is_wrapped_in_mailer_error(self) -> None:
        client_error = ClientError(
            error_response={"Error": {"Code": "MessageRejected", "Message": "rejected"}},
            operation_name="SendEmail",
        )
        mailer, _ = _make_mailer(FakeSesClient(raise_error=client_error))

        with pytest.raises(MailerError) as exc_info:
            await mailer.send(to=["a@example.com"], subject="s", html="<p>x</p>", text="x")
        assert exc_info.value.__cause__ is client_error

    async def test_empty_recipient_list_raises_and_does_not_call_ses(self) -> None:
        mailer, fake = _make_mailer()

        with pytest.raises(MailerError, match="recipient list is empty"):
            await mailer.send(to=[], subject="s", html="<p>x</p>", text="x")
        assert fake.calls == []

    async def test_empty_subject_raises_and_does_not_call_ses(self) -> None:
        mailer, fake = _make_mailer()

        with pytest.raises(MailerError, match="subject is empty"):
            await mailer.send(to=["a@example.com"], subject="", html="<p>x</p>", text="x")
        assert fake.calls == []

    async def test_success_log_contains_metrics_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from read_later_digest.adapters.mailer import ses as ses_module

        captured: list[tuple[str, dict[str, Any]]] = []

        def _capture(msg: str, **kwargs: Any) -> None:
            captured.append((msg, kwargs.get("extra", {})))

        monkeypatch.setattr(ses_module.logger, "info", _capture)
        mailer, _ = _make_mailer()
        await mailer.send(
            to=["a@example.com", "b@example.com"],
            subject="hello",
            html="<p>html-body</p>",
            text="text-body",
        )

        assert len(captured) == 1
        msg, extra = captured[0]
        assert msg == "mail sent"
        assert extra == {
            "to_count": 2,
            "subject_len": len("hello"),
            "html_len": len("<p>html-body</p>"),
            "text_len": len("text-body"),
        }
        # Addresses must not appear anywhere in log output.
        flat = repr((msg, extra))
        assert "a@example.com" not in flat
        assert "b@example.com" not in flat
