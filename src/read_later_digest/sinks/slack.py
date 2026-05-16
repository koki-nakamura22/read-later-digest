from __future__ import annotations

from typing import Any

import httpx
from digestkit.digester import FailureInfo
from digestkit.types import Digest, Item

from read_later_digest.domain.models import ArticleSummary
from read_later_digest.summarizers.validating_llm import parse_summary

_UNCATEGORIZED_TYPE = "未分類"
_UNSET_PRIORITY = "未設定"


class SlackBlockKitSink:
    """Slack Incoming Webhook へ Block Kit ペイロードを POST する Sink (per_article モード).

    T005 では write() のみ実装. send_failure_summary / send_heartbeat は T006 で追加.
    """

    def __init__(
        self,
        *,
        webhook_url: str,
        client: httpx.Client | None = None,
        timeout_sec: float = 10.0,
        subject_prefix: str = "[read-later-digest]",
    ) -> None:
        self._webhook_url = webhook_url
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_sec)
        self._subject_prefix = subject_prefix

    def write(self, digest: Digest, item: Item) -> None:
        """1 記事 = 1 Slack 通知."""
        summary = parse_summary(digest)
        meta = item.metadata or {}
        index: int | None = meta.get("index")
        total: int | None = meta.get("total")
        title: str = meta.get("title") or str(item.payload)
        url: str = meta.get("url") or str(item.payload)
        blocks = self._build_per_article_blocks(
            summary, title=title, url=url, index=index, total=total
        )
        self._post({"blocks": blocks})

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _post(self, payload: dict[str, Any]) -> None:
        response = self._client.post(self._webhook_url, json=payload)
        response.raise_for_status()

    def send_failure_summary(self, failures: list[FailureInfo], *, target_date: str) -> None:
        """失敗集約 1 通を Slack へ送る (ReadLaterDigester からの別経路呼び出し)."""
        if not failures:
            return
        blocks = self._build_failure_blocks(failures, target_date=target_date)
        self._post({"blocks": blocks})

    def send_heartbeat(self, *, target_date: str) -> None:
        """0 件ハートビート 1 通を Slack へ送る."""
        blocks = self._build_heartbeat_blocks(target_date=target_date)
        self._post({"blocks": blocks})

    def _build_failure_blocks(
        self, failures: list[FailureInfo], *, target_date: str
    ) -> list[dict[str, Any]]:
        header = (
            f"{self._subject_prefix} {target_date} のダイジェスト — 処理失敗 {len(failures)} 件"
        )
        lines: list[str] = []
        for f in failures:
            meta = f.item.metadata or {}
            title = meta.get("title")
            url = meta.get("url") or str(f.item.payload)
            reason = getattr(f.error, "reason", None)
            if reason is not None and hasattr(reason, "value"):
                reason_str = reason.value
            elif reason is not None:
                reason_str = str(reason)
            else:
                reason_str = str(f.error)
            label = title or url
            lines.append(f"• {label} (<{url}|link>) — {reason_str}")
        return [
            {"type": "header", "text": {"type": "plain_text", "text": header}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}},
        ]

    def _build_heartbeat_blocks(self, *, target_date: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"{self._subject_prefix} {target_date} のダイジェスト — 本日の未読 0 件"
                    ),
                },
            },
        ]

    def _build_per_article_blocks(
        self,
        summary: ArticleSummary,
        *,
        title: str,
        url: str,
        index: int | None,
        total: int | None,
    ) -> list[dict[str, Any]]:
        header_prefix = f"[{index}/{total}] " if index is not None and total is not None else ""
        type_label = summary.type_.value if summary.type_ is not None else _UNCATEGORIZED_TYPE
        priority_label = summary.priority.value if summary.priority is not None else _UNSET_PRIORITY
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{header_prefix}{title}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{url}|記事を開く>"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*タグ:* {type_label} / *優先度:* {priority_label}",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*3 行要約*\n" + "\n".join(f"• {s}" for s in summary.summary_lines),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*重要ポイント*\n" + "\n".join(f"• {k}" for k in summary.key_points),
                },
            },
        ]
