from __future__ import annotations

import httpx

from read_later_digest.exceptions import NotifierError
from read_later_digest.logging_setup import logger


class SlackNotifier:
    """Slack Incoming Webhook implementation of the Notifier port."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        webhook_url: str,
        timeout_sec: float = 10.0,
    ) -> None:
        if not webhook_url:
            raise ValueError("webhook_url is empty")
        self._client = client
        self._webhook_url = webhook_url
        self._timeout_sec = timeout_sec

    async def send(self, *, subject: str, text: str) -> None:
        if not subject:
            raise NotifierError("subject is empty")
        if not text:
            raise NotifierError("text is empty")

        payload = {"text": f"*{subject}*\n{text}"}

        try:
            response = await self._client.post(
                self._webhook_url,
                json=payload,
                timeout=self._timeout_sec,
            )
        except httpx.HTTPError as e:
            # Avoid leaking the webhook URL via str(e).
            raise NotifierError(f"slack webhook request failed: {type(e).__name__}") from e

        if not 200 <= response.status_code < 300:
            raise NotifierError(f"slack webhook returned non-2xx status: {response.status_code}")

        logger.info(
            "slack notification sent",
            extra={
                "subject_len": len(subject),
                "text_len": len(text),
                "status_code": response.status_code,
            },
        )
