from typing import Protocol


class Mailer(Protocol):
    """Abstract mailer port (see ADR-0005)."""

    async def send(
        self,
        *,
        to: list[str],
        subject: str,
        html: str,
        text: str,
    ) -> None:
        """Send an HTML + plain-text email.

        Raises:
            MailerError: when input is invalid or the underlying send fails.
        """
        ...
