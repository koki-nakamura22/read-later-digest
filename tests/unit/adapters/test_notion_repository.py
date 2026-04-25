from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

import httpx
import pytest
from notion_client.errors import APIErrorCode, APIResponseError

from read_later_digest.adapters.notion_repository import NotionRepository
from read_later_digest.exceptions import NotionError


def _rate_limited_error() -> APIResponseError:
    return APIResponseError(
        code=APIErrorCode.RateLimited,
        status=429,
        message="rate limited",
        headers=httpx.Headers({}),
        raw_body_text="",
    )


def _validation_error() -> APIResponseError:
    return APIResponseError(
        code=APIErrorCode.ValidationError,
        status=400,
        message="bad request",
        headers=httpx.Headers({}),
        raw_body_text="",
    )


def _build_repo(client: Any) -> NotionRepository:
    return NotionRepository(
        client=client,
        db_id="db-1",
        status_property="Status",
        status_unread="未読",
        max_retries=3,
        initial_backoff_sec=0,
    )


class TestListUnreadSinglePage:
    async def test_returns_articles_sorted_by_added_at_then_page_id(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        page1 = load_fixture("unread_page1.json")
        page1_single = copy.deepcopy(page1)
        page1_single["has_more"] = False
        page1_single["next_cursor"] = None

        client = make_fake_client([page1_single])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert [a.page_id for a in articles] == ["page-id-B", "page-id-A", "page-id-C"]
        assert articles[0].title == "Article B"
        assert articles[0].url == "https://example.com/b"
        assert articles[0].added_at == datetime.fromisoformat("2026-04-20T09:00:00+09:00")
        assert articles[0].age_days == 5
        assert len(client.databases.calls) == 1
        first_call = client.databases.calls[0]
        assert first_call["database_id"] == "db-1"
        assert first_call["filter"] == {
            "property": "Status",
            "select": {"equals": "未読"},
        }
        assert "start_cursor" not in first_call


class TestListUnreadPagination:
    async def test_traverses_multiple_pages_and_merges_results(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        page1 = load_fixture("unread_page1.json")
        page2 = load_fixture("unread_page2.json")
        client = make_fake_client([page1, page2])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert [a.page_id for a in articles] == [
            "page-id-B",
            "page-id-D",
            "page-id-A",
            "page-id-C",
        ]
        assert len(client.databases.calls) == 2
        assert "start_cursor" not in client.databases.calls[0]
        assert client.databases.calls[1]["start_cursor"] == "cursor-2"


class TestListUnreadEmpty:
    async def test_returns_empty_list(self, load_fixture: Any, make_fake_client: Any) -> None:
        client = make_fake_client([load_fixture("empty.json")])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles == []


class TestListUnreadRetry:
    async def test_retries_on_429_then_succeeds(
        self, load_fixture: Any, make_fake_client: Any, no_sleep: None
    ) -> None:
        page = copy.deepcopy(load_fixture("empty.json"))
        client = make_fake_client([_rate_limited_error(), page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles == []
        assert len(client.databases.calls) == 2

    async def test_raises_notion_error_when_retries_exhausted(
        self, make_fake_client: Any, no_sleep: None
    ) -> None:
        client = make_fake_client(
            [
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
            ]
        )
        repo = _build_repo(client)

        with pytest.raises(NotionError):
            await repo.list_unread()
        assert len(client.databases.calls) == 4

    async def test_non_rate_limit_error_is_wrapped_as_notion_error(
        self, make_fake_client: Any
    ) -> None:
        client = make_fake_client([_validation_error()])
        repo = _build_repo(client)

        with pytest.raises(NotionError):
            await repo.list_unread()
        assert len(client.databases.calls) == 1


class TestListUnreadMissingProperties:
    async def test_url_missing_record_is_skipped(self, make_fake_client: Any) -> None:
        page = {
            "results": [
                {
                    "id": "no-url",
                    "properties": {
                        "Name": {"title": [{"plain_text": "T"}]},
                        "URL": {"url": None},
                        "AddedAt": {"date": {"start": "2026-04-20T00:00:00+09:00"}},
                        "Age": {"formula": {"number": 1}},
                    },
                },
                {
                    "id": "ok",
                    "properties": {
                        "Name": {"title": [{"plain_text": "T2"}]},
                        "URL": {"url": "https://example.com/ok"},
                        "AddedAt": {"date": {"start": "2026-04-21T00:00:00+09:00"}},
                        "Age": {"formula": {"number": 1}},
                    },
                },
            ],
            "has_more": False,
            "next_cursor": None,
        }
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()
        assert [a.page_id for a in articles] == ["ok"]

    async def test_empty_title_is_kept(self, make_fake_client: Any) -> None:
        page = {
            "results": [
                {
                    "id": "p1",
                    "properties": {
                        "Name": {"title": []},
                        "URL": {"url": "https://example.com/x"},
                        "AddedAt": {"date": {"start": "2026-04-20T00:00:00+09:00"}},
                        "Age": {"formula": {"number": 0}},
                    },
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()
        assert articles[0].title == ""
        assert articles[0].url == "https://example.com/x"

    async def test_added_at_missing_record_is_skipped(self, make_fake_client: Any) -> None:
        page = {
            "results": [
                {
                    "id": "no-date",
                    "properties": {
                        "Name": {"title": [{"plain_text": "T"}]},
                        "URL": {"url": "https://example.com/x"},
                        "AddedAt": {"date": None},
                        "Age": {"formula": {"number": 0}},
                    },
                }
            ],
            "has_more": False,
            "next_cursor": None,
        }
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()
        assert articles == []


class TestListUnreadClientSideSort:
    async def test_sorts_by_added_at_even_if_api_returns_unsorted(
        self, make_fake_client: Any
    ) -> None:
        page = {
            "results": [
                {
                    "id": "z",
                    "properties": {
                        "Name": {"title": [{"plain_text": "Z"}]},
                        "URL": {"url": "https://example.com/z"},
                        "AddedAt": {"date": {"start": "2026-04-25T00:00:00+09:00"}},
                        "Age": {"formula": {"number": 0}},
                    },
                },
                {
                    "id": "a",
                    "properties": {
                        "Name": {"title": [{"plain_text": "A"}]},
                        "URL": {"url": "https://example.com/a"},
                        "AddedAt": {"date": {"start": "2026-04-20T00:00:00+09:00"}},
                        "Age": {"formula": {"number": 5}},
                    },
                },
                {
                    "id": "b",
                    "properties": {
                        "Name": {"title": [{"plain_text": "B"}]},
                        "URL": {"url": "https://example.com/b"},
                        "AddedAt": {"date": {"start": "2026-04-20T00:00:00+09:00"}},
                        "Age": {"formula": {"number": 5}},
                    },
                },
            ],
            "has_more": False,
            "next_cursor": None,
        }
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()
        assert [a.page_id for a in articles] == ["a", "b", "z"]
