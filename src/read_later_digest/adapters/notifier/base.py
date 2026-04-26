from typing import Protocol


class Notifier(Protocol):
    """Abstract chat-notification port (see PRD F10)."""

    async def send(self, *, subject: str, text: str) -> None:
        """Send a notification message.

        Raises:
            NotifierError: when input is invalid or the underlying send fails.
        """
        ...
