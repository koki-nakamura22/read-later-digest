class AppError(Exception):
    """Base exception for read-later-digest."""


class NotionError(AppError):
    """Raised when Notion API interaction fails."""
