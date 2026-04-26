from __future__ import annotations

import copy
from datetime import datetime
from typing import Any

import httpx
import pytest
from notion_client.errors import APIErrorCode, APIResponseError

from read_later_digest.adapters.notion_repository import NotionRepository
from read_later_digest.exceptions import NotionError
from tests.conftest import make_notion_page, make_query_response


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


def _build_repo(client: Any, **overrides: Any) -> NotionRepository:
    defaults: dict[str, Any] = {
        "client": client,
        "db_id": "db-1",
        "status_property": "Status",
        "status_unread": "未読",
        "max_retries": 3,
        "initial_backoff_sec": 0,
    }
    defaults.update(overrides)
    return NotionRepository(**defaults)


class TestListUnreadSinglePage:
    """Single-page happy path. Fixture `unread_page1.json` holds 3 records B(2026-04-20),
    A(2026-04-22), C(2026-04-23) so the expected order after client-side sort is B, A, C.
    """

    async def test_returns_records_in_added_at_ascending_order(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        page = copy.deepcopy(load_fixture("unread_page1.json"))
        page["has_more"] = False
        page["next_cursor"] = None
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert [a.page_id for a in articles] == ["page-id-B", "page-id-A", "page-id-C"]

    async def test_maps_all_notion_properties_onto_article(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        page = copy.deepcopy(load_fixture("unread_page1.json"))
        page["has_more"] = False
        page["next_cursor"] = None
        client = make_fake_client([page])
        repo = _build_repo(client)

        first = (await repo.list_unread())[0]

        assert first.page_id == "page-id-B"
        assert first.title == "Article B"
        assert first.url == "https://example.com/b"
        assert first.added_at == datetime.fromisoformat("2026-04-20T09:00:00+09:00")
        assert first.age_days == 5

    async def test_sends_status_filter_and_no_cursor_on_first_call(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        page = copy.deepcopy(load_fixture("unread_page1.json"))
        page["has_more"] = False
        page["next_cursor"] = None
        client = make_fake_client([page])
        repo = _build_repo(client)

        await repo.list_unread()

        assert len(client.data_sources.calls) == 1
        first_call = client.data_sources.calls[0]
        assert first_call["data_source_id"] == "ds-1"
        assert first_call["filter"] == {
            "property": "Status",
            "select": {"equals": "未読"},
        }
        assert "start_cursor" not in first_call


class TestDataSourceIdResolution:
    """Notion 3.x: query takes data_source_id, resolved once via databases.retrieve."""

    async def test_resolves_data_source_id_via_databases_retrieve(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        client = make_fake_client([load_fixture("empty.json")])
        repo = _build_repo(client)

        await repo.list_unread()

        # Exactly one retrieve call, with the configured database_id.
        assert len(client.databases.retrieve_calls) == 1
        assert client.databases.retrieve_calls[0]["database_id"] == "db-1"

    async def test_caches_data_source_id_across_repeated_list_calls(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        # Two consecutive list_unread() invocations must hit databases.retrieve once;
        # the resolved data_source_id is cached on the repo for its lifetime.
        client = make_fake_client([load_fixture("empty.json"), load_fixture("empty.json")])
        repo = _build_repo(client)

        await repo.list_unread()
        await repo.list_unread()

        assert len(client.databases.retrieve_calls) == 1
        assert len(client.data_sources.calls) == 2

    async def test_raises_when_database_has_no_data_sources(self) -> None:
        from tests.conftest import (
            FakeDataSourcesAPI,
            FakeNotionClient,
        )

        class _EmptyDatabasesAPI:
            def __init__(self) -> None:
                self.retrieve_calls: list[dict[str, Any]] = []

            def retrieve(self, **kwargs: Any) -> dict[str, Any]:
                self.retrieve_calls.append(kwargs)
                return {"data_sources": []}

        client = FakeNotionClient(FakeDataSourcesAPI([]), databases=_EmptyDatabasesAPI())  # type: ignore[arg-type]
        repo = _build_repo(client)

        with pytest.raises(NotionError, match="data_sources"):
            await repo.list_unread()


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

    async def test_passes_next_cursor_on_second_call(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        page1 = load_fixture("unread_page1.json")
        page2 = load_fixture("unread_page2.json")
        client = make_fake_client([page1, page2])
        repo = _build_repo(client)

        await repo.list_unread()

        assert len(client.data_sources.calls) == 2
        assert "start_cursor" not in client.data_sources.calls[0]
        assert client.data_sources.calls[1]["start_cursor"] == "cursor-2"


class TestListUnreadEmpty:
    async def test_returns_empty_list_with_one_api_call(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        client = make_fake_client([load_fixture("empty.json")])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles == []
        assert len(client.data_sources.calls) == 1


class TestListUnreadConfigurableStatusValue:
    async def test_passes_custom_status_unread_value_to_filter(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        client = make_fake_client([load_fixture("empty.json")])
        repo = _build_repo(client, status_unread="読了待ち")

        await repo.list_unread()

        assert client.data_sources.calls[0]["filter"] == {
            "property": "Status",
            "select": {"equals": "読了待ち"},
        }

    async def test_passes_custom_status_property_name_to_filter(
        self, load_fixture: Any, make_fake_client: Any
    ) -> None:
        client = make_fake_client([load_fixture("empty.json")])
        repo = _build_repo(client, status_property="読書状態")

        await repo.list_unread()

        assert client.data_sources.calls[0]["filter"] == {
            "property": "読書状態",
            "select": {"equals": "未読"},
        }


class TestListUnreadRetry:
    async def test_retries_on_429_then_succeeds(
        self, load_fixture: Any, make_fake_client: Any, no_sleep: None
    ) -> None:
        page = copy.deepcopy(load_fixture("empty.json"))
        client = make_fake_client([_rate_limited_error(), page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles == []
        assert len(client.data_sources.calls) == 2

    async def test_succeeds_when_last_allowed_retry_finally_returns(
        self, load_fixture: Any, make_fake_client: Any, no_sleep: None
    ) -> None:
        # Boundary: max_retries=3 means 3 retries are allowed after the first attempt,
        # so the 4th total call (3rd retry) is the last one that may succeed.
        page = copy.deepcopy(load_fixture("empty.json"))
        client = make_fake_client(
            [
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
                page,
            ]
        )
        repo = _build_repo(client, max_retries=3)

        articles = await repo.list_unread()

        assert articles == []
        assert len(client.data_sources.calls) == 4

    async def test_raises_notion_error_when_retries_exhausted(
        self, make_fake_client: Any, no_sleep: None
    ) -> None:
        # Boundary: one more 429 than max_retries triggers exhaustion.
        client = make_fake_client(
            [
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
            ]
        )
        repo = _build_repo(client, max_retries=3)

        with pytest.raises(NotionError):
            await repo.list_unread()
        assert len(client.data_sources.calls) == 4

    async def test_non_rate_limit_error_is_wrapped_as_notion_error_without_retry(
        self, make_fake_client: Any
    ) -> None:
        client = make_fake_client([_validation_error()])
        repo = _build_repo(client)

        with pytest.raises(NotionError):
            await repo.list_unread()
        assert len(client.data_sources.calls) == 1


class TestListUnreadMissingProperties:
    async def test_url_missing_record_is_skipped(self, make_fake_client: Any) -> None:
        page = make_query_response(
            [
                make_notion_page(page_id="no-url", url=None),
                make_notion_page(page_id="ok", url="https://example.com/ok"),
            ]
        )
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert [a.page_id for a in articles] == ["ok"]

    async def test_url_missing_record_emits_warning_log_with_page_id(
        self, make_fake_client: Any, captured_notion_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        page = make_query_response([make_notion_page(page_id="no-url", url=None)])
        client = make_fake_client([page])
        repo = _build_repo(client)

        await repo.list_unread()

        assert any(
            "url" in msg.lower() and extra.get("page_id") == "no-url"
            for msg, extra in captured_notion_warnings
        )

    async def test_empty_title_is_kept_with_warning(
        self, make_fake_client: Any, captured_notion_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        page = make_query_response([make_notion_page(page_id="p1", title="")])
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles[0].title == ""
        assert any(extra.get("page_id") == "p1" for _, extra in captured_notion_warnings)

    async def test_added_at_missing_record_is_skipped(self, make_fake_client: Any) -> None:
        page = make_query_response([make_notion_page(page_id="no-date", added_at=None)])
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles == []

    async def test_added_at_missing_record_emits_warning_log(
        self, make_fake_client: Any, captured_notion_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        page = make_query_response([make_notion_page(page_id="no-date", added_at=None)])
        client = make_fake_client([page])
        repo = _build_repo(client)

        await repo.list_unread()

        assert any(
            "added_at" in msg.lower() and extra.get("page_id") == "no-date"
            for msg, extra in captured_notion_warnings
        )

    async def test_age_missing_returns_none_age_days(self, make_fake_client: Any) -> None:
        page = make_query_response([make_notion_page(page_id="p1", age_days=None)])
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert articles[0].age_days is None

    async def test_added_at_in_notion_created_time_property_form_is_parsed(
        self, make_fake_client: Any
    ) -> None:
        # docs/functional-design.md specifies AddedAt as a Notion `created_time`
        # property. Its API shape is {"created_time": "<iso>"} rather than the
        # `date` shape {"date": {"start": "<iso>"}}. The adapter must accept
        # both so users can keep their existing schema.
        result = make_notion_page(page_id="ct-1")
        result["properties"]["AddedAt"] = {
            "type": "created_time",
            "created_time": "2026-04-22T10:00:00.000Z",
        }
        page = make_query_response([result])
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert len(articles) == 1
        assert articles[0].added_at == datetime.fromisoformat("2026-04-22T10:00:00+00:00")


class TestListUnreadClientSideSort:
    async def test_sorts_by_added_at_even_if_api_returns_unsorted(
        self, make_fake_client: Any
    ) -> None:
        page = make_query_response(
            [
                make_notion_page(page_id="z", added_at="2026-04-25T00:00:00+09:00"),
                make_notion_page(page_id="a", added_at="2026-04-20T00:00:00+09:00"),
                make_notion_page(page_id="b", added_at="2026-04-20T00:00:00+09:00"),
            ]
        )
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert [a.page_id for a in articles] == ["a", "b", "z"]

    async def test_tie_break_uses_page_id_ascending(self, make_fake_client: Any) -> None:
        # Same added_at across all three records; only page_id determines order.
        same_time = "2026-04-20T00:00:00+09:00"
        page = make_query_response(
            [
                make_notion_page(page_id="banana", added_at=same_time),
                make_notion_page(page_id="apple", added_at=same_time),
                make_notion_page(page_id="cherry", added_at=same_time),
            ]
        )
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert [a.page_id for a in articles] == ["apple", "banana", "cherry"]

    async def test_single_record_returns_single_article(self, make_fake_client: Any) -> None:
        # Boundary: one item.
        page = make_query_response([make_notion_page(page_id="only")])
        client = make_fake_client([page])
        repo = _build_repo(client)

        articles = await repo.list_unread()

        assert len(articles) == 1
        assert articles[0].page_id == "only"


class TestMarkProcessed:
    """Behavior of `NotionRepository.mark_processed` (F6)."""

    async def test_sends_select_payload_with_default_values(
        self, make_fake_pages_client: Any
    ) -> None:
        client = make_fake_pages_client([])
        repo = _build_repo(client)

        await repo.mark_processed("page-id-1")

        assert client.pages.calls == [
            {
                "page_id": "page-id-1",
                "properties": {"Status": {"select": {"name": "処理済み"}}},
            }
        ]

    async def test_uses_configured_status_property_and_value(
        self, make_fake_pages_client: Any
    ) -> None:
        client = make_fake_pages_client([])
        repo = _build_repo(
            client,
            status_property="ステータス",
            status_processed="Done",
        )

        await repo.mark_processed("page-id-2")

        assert client.pages.calls[0]["properties"] == {"ステータス": {"select": {"name": "Done"}}}

    async def test_retries_on_429_then_succeeds(
        self, make_fake_pages_client: Any, no_sleep: None
    ) -> None:
        client = make_fake_pages_client([_rate_limited_error(), {}])
        repo = _build_repo(client)

        await repo.mark_processed("page-id-4")

        assert len(client.pages.calls) == 2

    async def test_succeeds_when_last_allowed_retry_finally_returns(
        self, make_fake_pages_client: Any, no_sleep: None
    ) -> None:
        # Boundary: max_retries=3 means 3 retries after the first attempt; the 4th
        # total call may still succeed.
        client = make_fake_pages_client(
            [
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
                {},
            ]
        )
        repo = _build_repo(client, max_retries=3)

        await repo.mark_processed("page-id-edge")

        assert len(client.pages.calls) == 4

    async def test_raises_notion_error_when_retries_exhausted(
        self, make_fake_pages_client: Any, no_sleep: None
    ) -> None:
        # Boundary: max_retries=3 → one more 429 (4 total) triggers exhaustion.
        client = make_fake_pages_client(
            [
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
                _rate_limited_error(),
            ]
        )
        repo = _build_repo(client, max_retries=3)

        with pytest.raises(NotionError):
            await repo.mark_processed("page-id-5")
        assert len(client.pages.calls) == 4

    async def test_non_rate_limit_error_is_wrapped_without_retry(
        self, make_fake_pages_client: Any
    ) -> None:
        client = make_fake_pages_client([_validation_error()])
        repo = _build_repo(client)

        with pytest.raises(NotionError):
            await repo.mark_processed("page-id-6")
        assert len(client.pages.calls) == 1


class TestWriteSummary:
    @staticmethod
    def _build_summary(
        *,
        type_: Any = None,
        priority: Any = None,
    ) -> Any:
        from read_later_digest.domain.models import ArticleSummary

        return ArticleSummary(
            summary_lines=["s1", "s2", "s3"],
            key_points=["k1", "k2"],
            type_=type_,
            priority=priority,
        )

    async def test_appends_summary_blocks_and_updates_type_priority(self) -> None:
        from read_later_digest.domain.models import ArticleType, Priority
        from tests.conftest import (
            FakeBlocksAPI,
            FakeBlocksChildrenAPI,
            FakeDataSourcesAPI,
            FakeNotionClient,
            FakePagesAPI,
        )

        children = FakeBlocksChildrenAPI([{}])
        client = FakeNotionClient(
            FakeDataSourcesAPI([]),
            pages=FakePagesAPI([{}]),
            blocks=FakeBlocksAPI(children),
        )
        repo = _build_repo(client)

        await repo.write_summary(
            "page-id-X",
            self._build_summary(type_=ArticleType.TECH, priority=Priority.HIGH),
        )

        assert len(children.calls) == 1
        appended = children.calls[0]
        assert appended["block_id"] == "page-id-X"
        # 1 heading + 3 summary lines + 1 heading + 2 bullet points = 7 blocks
        assert len(appended["children"]) == 7

        assert len(client.pages.calls) == 1
        props = client.pages.calls[0]["properties"]
        assert props["Type"] == {"select": {"name": "技術"}}
        assert props["Priority"] == {"select": {"name": "高"}}

    async def test_skips_property_update_when_type_and_priority_are_none(self) -> None:
        from tests.conftest import (
            FakeBlocksAPI,
            FakeBlocksChildrenAPI,
            FakeDataSourcesAPI,
            FakeNotionClient,
            FakePagesAPI,
        )

        children = FakeBlocksChildrenAPI([{}])
        client = FakeNotionClient(
            FakeDataSourcesAPI([]),
            pages=FakePagesAPI(),
            blocks=FakeBlocksAPI(children),
        )
        repo = _build_repo(client)

        await repo.write_summary("page-id-Y", self._build_summary())

        assert len(children.calls) == 1
        assert client.pages.calls == [], "no property update when type/priority absent"


class TestWriteFailure:
    async def test_appends_failure_paragraph_and_does_not_touch_properties(self) -> None:
        from tests.conftest import (
            FakeBlocksAPI,
            FakeBlocksChildrenAPI,
            FakeDataSourcesAPI,
            FakeNotionClient,
            FakePagesAPI,
        )

        children = FakeBlocksChildrenAPI([{}])
        client = FakeNotionClient(
            FakeDataSourcesAPI([]),
            pages=FakePagesAPI(),
            blocks=FakeBlocksAPI(children),
        )
        repo = _build_repo(client)

        await repo.write_failure("page-id-Z", "fetch_failed: timeout")

        assert len(children.calls) == 1
        appended = children.calls[0]
        assert appended["block_id"] == "page-id-Z"
        assert len(appended["children"]) == 1
        text = appended["children"][0]["paragraph"]["rich_text"][0]["text"]["content"]
        assert text == "[処理失敗] fetch_failed: timeout"
        assert client.pages.calls == []
