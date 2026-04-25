from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol

from notion_client.errors import APIResponseError

from read_later_digest.domain.models import NotionArticle
from read_later_digest.exceptions import NotionError
from read_later_digest.logging_setup import logger


class NotionDatabasesAPI(Protocol):
    """Subset of `notion_client.Client.databases` used by `NotionRepository`."""

    def query(self, **kwargs: Any) -> dict[str, Any]: ...


class NotionPagesAPI(Protocol):
    """Subset of `notion_client.Client.pages` used by `NotionRepository`."""

    def update(self, **kwargs: Any) -> dict[str, Any]: ...


class NotionClientLike(Protocol):
    """Structural type matching `notion_client.Client` for the parts we use."""

    @property
    def databases(self) -> NotionDatabasesAPI: ...

    @property
    def pages(self) -> NotionPagesAPI: ...


class NotionRepository:
    """Read-side adapter for the Notion DB that holds saved articles."""

    def __init__(
        self,
        *,
        client: NotionClientLike,
        db_id: str,
        status_property: str = "Status",
        status_unread: str = "未読",
        status_processed: str = "処理済み",
        max_retries: int = 3,
        initial_backoff_sec: float = 1.0,
    ) -> None:
        self._client = client
        self._db_id = db_id
        self._status_property = status_property
        self._status_unread = status_unread
        self._status_processed = status_processed
        self._max_retries = max_retries
        self._initial_backoff_sec = initial_backoff_sec

    async def list_unread(self) -> list[NotionArticle]:
        """Return all pages whose Status equals the configured unread value.

        Pages without a usable URL or AddedAt are skipped with a warning.
        Results are sorted by added_at ascending, then by page_id ascending.
        """
        articles: list[NotionArticle] = []
        cursor: str | None = None
        while True:
            response = await self._query_with_retry(start_cursor=cursor)
            articles.extend(self._parse_page(response))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
            if cursor is None:
                break
        return self._sort(articles)

    async def mark_processed(self, page_id: str) -> None:
        """Update the page's Status property to the configured processed value."""
        await self._update_with_retry(
            page_id=page_id,
            properties={
                self._status_property: {"select": {"name": self._status_processed}},
            },
        )

    async def _update_with_retry(
        self, *, page_id: str, properties: dict[str, Any]
    ) -> dict[str, Any]:
        delay = self._initial_backoff_sec
        last_error: APIResponseError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await asyncio.to_thread(
                    self._client.pages.update, page_id=page_id, properties=properties
                )
            except APIResponseError as e:
                last_error = e
                if e.status == 429 and attempt < self._max_retries:
                    logger.warning(
                        "notion api rate limited; retrying",
                        extra={"attempt": attempt + 1, "delay_sec": delay},
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise NotionError(f"notion api error (status={e.status}): {e}") from e
        raise NotionError(f"notion api retries exhausted: {last_error}") from last_error

    async def _query_with_retry(self, *, start_cursor: str | None) -> dict[str, Any]:
        delay = self._initial_backoff_sec
        last_error: APIResponseError | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await asyncio.to_thread(
                    self._client.databases.query, **self._build_query(start_cursor)
                )
            except APIResponseError as e:
                last_error = e
                if e.status == 429 and attempt < self._max_retries:
                    logger.warning(
                        "notion api rate limited; retrying",
                        extra={"attempt": attempt + 1, "delay_sec": delay},
                    )
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise NotionError(f"notion api error (status={e.status}): {e}") from e
        raise NotionError(f"notion api retries exhausted: {last_error}") from last_error

    def _build_query(self, start_cursor: str | None) -> dict[str, Any]:
        query: dict[str, Any] = {
            "database_id": self._db_id,
            "filter": {
                "property": self._status_property,
                "select": {"equals": self._status_unread},
            },
            "sorts": [{"property": "AddedAt", "direction": "ascending"}],
            "page_size": 100,
        }
        if start_cursor is not None:
            query["start_cursor"] = start_cursor
        return query

    def _parse_page(self, response: dict[str, Any]) -> list[NotionArticle]:
        out: list[NotionArticle] = []
        for result in response.get("results", []):
            article = self._parse_result(result)
            if article is not None:
                out.append(article)
        return out

    def _parse_result(self, result: dict[str, Any]) -> NotionArticle | None:
        page_id = result.get("id", "")
        properties = result.get("properties", {})

        url = self._extract_url(properties)
        if url is None:
            logger.warning("skipping notion page: url is missing", extra={"page_id": page_id})
            return None

        added_at = self._extract_added_at(properties)
        if added_at is None:
            logger.warning("skipping notion page: added_at is missing", extra={"page_id": page_id})
            return None

        title = self._extract_title(properties)
        if title == "":
            logger.warning("notion page has empty title", extra={"page_id": page_id})

        age_days = self._extract_age_days(properties)

        return NotionArticle(
            page_id=page_id,
            title=title,
            url=url,
            added_at=added_at,
            age_days=age_days,
        )

    @staticmethod
    def _extract_title(properties: dict[str, Any]) -> str:
        name_prop = properties.get("Name", {})
        title_items = name_prop.get("title", []) or []
        return "".join(item.get("plain_text", "") for item in title_items)

    @staticmethod
    def _extract_url(properties: dict[str, Any]) -> str | None:
        url_prop = properties.get("URL", {})
        url = url_prop.get("url")
        return url if isinstance(url, str) and url else None

    @staticmethod
    def _extract_added_at(properties: dict[str, Any]) -> datetime | None:
        added_prop = properties.get("AddedAt", {})
        date_obj = added_prop.get("date")
        if not isinstance(date_obj, dict):
            return None
        start = date_obj.get("start")
        if not isinstance(start, str):
            return None
        try:
            return datetime.fromisoformat(start)
        except ValueError:
            return None

    @staticmethod
    def _extract_age_days(properties: dict[str, Any]) -> int | None:
        age_prop = properties.get("Age", {})
        formula = age_prop.get("formula")
        if not isinstance(formula, dict):
            return None
        number = formula.get("number")
        if isinstance(number, int):
            return number
        if isinstance(number, float):
            return int(number)
        return None

    @staticmethod
    def _sort(articles: list[NotionArticle]) -> list[NotionArticle]:
        return sorted(articles, key=lambda a: (a.added_at, a.page_id))
