from __future__ import annotations

import asyncio
from typing import Any, Protocol

from botocore.exceptions import ClientError

from read_later_digest.exceptions import MailerError
from read_later_digest.logging_setup import logger


class SesClientLike(Protocol):
    def send_email(self, **kwargs: Any) -> Any: ...


class SesMailer:
    """Amazon SES implementation of the Mailer port."""

    def __init__(self, *, client: SesClientLike, source: str) -> None:
        self._client = client
        self._source = source

    async def send(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        text: str,
    ) -> None:
        if not to:
            raise MailerError("recipient list is empty")
        if not subject:
            raise MailerError("subject is empty")

        try:
            await asyncio.to_thread(
                self._client.send_email,
                Source=self._source,
                Destination={"ToAddresses": list(to)},
                Message={
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": html, "Charset": "UTF-8"},
                        "Text": {"Data": text, "Charset": "UTF-8"},
                    },
                },
            )
        except ClientError as e:
            raise MailerError(f"SES send_email failed: {e}") from e

        logger.info(
            "mail sent",
            extra={
                "to_count": len(to),
                "subject_len": len(subject),
                "html_len": len(html),
                "text_len": len(text),
            },
        )
