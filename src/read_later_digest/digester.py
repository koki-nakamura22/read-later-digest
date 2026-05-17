from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta, timezone
from functools import partial
from typing import Any

from digestkit import FailureInfo
from digestkit.digester import Digester
from digestkit.protocols import AckSource
from digestkit.types import Digest, Item

from read_later_digest.domain.models import ReadLaterRunResult
from read_later_digest.logging_setup import logger
from read_later_digest.notion_retry import with_retry

JST = timezone(timedelta(hours=9))


def _paragraph_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _default_clock() -> datetime:
    return datetime.now(tz=UTC).astimezone(JST)


class _CachedSource:
    """enrich 済 items を super().run() の Source として使うラッパ.

    Digester は run() 中で source.fetch() を呼ぶので、enrich 済 items で差し替える.
    ack_success / ack_failure は AckSource プロトコルを満たすために明示メソッドで
    元 source へ委譲する (Issue #25). ``__getattr__`` 経由の委譲では runtime_checkable
    Protocol の ``isinstance`` 判定がクラス属性検査で False になり、digestkit 側で
    ack が呼ばれなくなる.
    """

    def __init__(self, items: list[Item], *, original: AckSource) -> None:
        self._items = items
        self._original = original

    def fetch(self) -> Iterable[Item]:
        yield from self._items

    def ack_success(self, item: Item, digest: Digest) -> None:
        self._original.ack_success(item, digest)

    def ack_failure(self, failure: FailureInfo) -> None:
        self._original.ack_failure(failure)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)


class ReadLaterDigester(Digester):
    """vanilla Digester に薄いフックを足したラッパ."""

    def __init__(
        self,
        *,
        slack_notifier: Any,
        notion_client: Any,
        max_items_per_run: int = 30,
        clock_fn: Callable[[], datetime] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._slack = slack_notifier
        self._notion_client = notion_client
        self._max_items_per_run = max_items_per_run
        self._clock_fn = clock_fn or _default_clock

    def run(self, **kwargs: Any) -> ReadLaterRunResult:  # type: ignore[override]
        started = time.monotonic()
        target_date = self._clock_fn().date().isoformat()

        raw_items = list(self.source.fetch())
        items = self._sort_by_added_at(raw_items)

        if len(items) > self._max_items_per_run:
            logger.warning(
                "item count exceeds MAX_ITEMS_PER_RUN; Lambda timeout risk",
                extra={"count": len(items), "max": self._max_items_per_run},
            )

        if not items:
            self._slack.send_heartbeat(target_date=target_date)
            return ReadLaterRunResult(
                total_articles=0,
                succeeded=0,
                failed=0,
                notification_sent=True,
                status_updated=0,
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        total = len(items)
        enriched: list[Item] = []
        for i, it in enumerate(items, start=1):
            meta = dict(it.metadata or {})
            page = meta.get("page", {}) or {}
            title = meta.get("title") or self._extract_title(page)
            url = (
                str(it.payload)
                if isinstance(it.payload, str)
                else (meta.get("url") or page.get("url", ""))
            )
            meta.update({"index": i, "total": total, "title": title, "url": url})
            enriched.append(Item(id=it.id, payload=it.payload, metadata=meta))

        original_source = self.source
        assert isinstance(original_source, AckSource), (
            "ReadLaterDigester requires an AckSource (e.g. NotionDatabaseSource) "
            "so that Status write-back is preserved across the _CachedSource swap."
        )
        self.source = _CachedSource(enriched, original=original_source)
        try:
            result = super().run(**kwargs)
        finally:
            self.source = original_source

        notification_sent = result.success > 0
        if result.failures:
            for f in result.failures:
                raw_reason = getattr(f.error, "reason", None)
                if raw_reason is not None:
                    reason_str = str(getattr(raw_reason, "value", raw_reason))
                else:
                    reason_str = str(f.error)
                with_retry(
                    partial(
                        self._notion_client.blocks.children.append,
                        block_id=f.item.id,
                        children=[_paragraph_block(f"[処理失敗] {reason_str}")],
                    ),
                    max_retries=3,
                    initial_backoff_sec=1.0,
                )
            self._slack.send_failure_summary(result.failures, target_date=target_date)
            notification_sent = True

        return ReadLaterRunResult(
            total_articles=total,
            succeeded=result.success,
            failed=len(result.failures),
            notification_sent=notification_sent,
            status_updated=result.success,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    def _sort_by_added_at(self, items: list[Item]) -> list[Item]:
        def key(it: Item) -> tuple[Any, str]:
            page = (it.metadata or {}).get("page", {}) or {}
            added_at = self._extract_added_at(page) or datetime.min.replace(tzinfo=UTC)
            return (added_at, it.id)

        return sorted(items, key=key)

    @staticmethod
    def _extract_title(page: dict[str, Any]) -> str:
        props = page.get("properties", {})
        name_prop = props.get("Name", {})
        title_items = name_prop.get("title", []) or []
        return "".join(t.get("plain_text", "") for t in title_items)

    @staticmethod
    def _extract_added_at(page: dict[str, Any]) -> datetime | None:
        props = page.get("properties", {})
        added = props.get("AddedAt", {})
        if not isinstance(added, dict):
            return None
        raw = added.get("created_time")
        if not isinstance(raw, str):
            date_obj = added.get("date")
            if isinstance(date_obj, dict):
                raw = date_obj.get("start")
        if not isinstance(raw, str):
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
