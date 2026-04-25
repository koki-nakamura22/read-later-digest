class AppError(Exception):
    """Base exception for read-later-digest."""


class NotionError(AppError):
    """Raised when Notion API interaction fails."""


class LLMError(AppError):
    """Raised when LLM summarization fails after retries."""


class MailerError(AppError):
    """Raised when sending mail fails."""
