class AppError(Exception):
    """Base exception for read-later-digest."""


class LLMError(AppError):
    """Raised when LLM summarization fails after retries."""
