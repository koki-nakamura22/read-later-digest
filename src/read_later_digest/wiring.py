from __future__ import annotations

from typing import Any

from digestkit.sinks.notion_page import NotionPageSink
from digestkit.sources.notion_database import NotionDatabaseSource
from digestkit.summarizers import LLMSummarizer
from digestkit.types import Digest, Item
from notion_client import Client as NotionClient

from read_later_digest.config import Config
from read_later_digest.digester import ReadLaterDigester
from read_later_digest.extractors.safe_webpage import SafeWebPageExtractor
from read_later_digest.sinks.ordered import OrderedSink
from read_later_digest.sinks.slack import SlackBlockKitSink
from read_later_digest.summarizers.prompts import _SYSTEM_PROMPT
from read_later_digest.summarizers.validating_llm import ValidatingLLMSummarizer, parse_summary


def _paragraph_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _heading_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _bullet_block(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def _make_success_properties(item: Item, digest: Digest) -> dict[str, Any]:
    """NotionDatabaseSource.properties_on_success callback. Type/Priority を select 形式で返す."""
    summary = parse_summary(digest)
    out: dict[str, Any] = {}
    if summary.type_ is not None:
        out["Type"] = {"select": {"name": summary.type_.value}}
    if summary.priority is not None:
        out["Priority"] = {"select": {"name": summary.priority.value}}
    return out


def _build_summary_blocks(digest: Digest, item: Item) -> list[dict[str, Any]]:
    """NotionPageSink.blocks_builder. 旧 notion_repository._build_summary_blocks を移植."""
    summary = parse_summary(digest)
    blocks: list[dict[str, Any]] = [_heading_block("要約")]
    blocks.extend(_paragraph_block(line) for line in summary.summary_lines)
    if summary.key_points:
        blocks.append(_heading_block("重要ポイント"))
        blocks.extend(_bullet_block(point) for point in summary.key_points)
    return blocks


def build_digester(config: Config) -> ReadLaterDigester:
    """handler から呼ばれる factory. 全部品を組み立てて ReadLaterDigester を返す."""
    notion_client = NotionClient(auth=config.notion_token)
    source = NotionDatabaseSource(
        config.notion_db_id,
        config.notion_token,
        status_property="Status",
        status_value_success="処理済み",
        url_property="URL",
        query_filter={"property": "Status", "select": {"equals": "未読"}},
        properties_on_success=_make_success_properties,
        max_retries=3,
        initial_backoff_sec=1.0,
    )
    extractor = SafeWebPageExtractor(
        user_agent=config.fetch_user_agent,
        timeout_sec=config.fetch_timeout_sec,
        body_max_chars=config.llm_body_max_chars,
    )
    inner_llm = LLMSummarizer(
        provider="anthropic",
        model=config.llm_model,
        system_prompt=_SYSTEM_PROMPT,
        system_prompt_cache=True,
        num_retries=config.llm_max_rate_limit_retries,
    )
    summarizer = ValidatingLLMSummarizer(inner_llm, max_schema_retries=1)
    slack_sink = SlackBlockKitSink(
        webhook_url=config.slack_webhook_url or "",
        timeout_sec=config.slack_timeout_sec,
    )
    notion_page_sink = NotionPageSink(
        config.notion_token,
        blocks_builder=_build_summary_blocks,
        max_retries=3,
        initial_backoff_sec=1.0,
    )
    sink = OrderedSink([slack_sink, notion_page_sink])
    return ReadLaterDigester(
        source=source,
        extractor=extractor,
        summarizer=summarizer,
        sink=sink,
        ack_mode="after_run",
        seen_store=None,
        slack_notifier=slack_sink,
        notion_client=notion_client,
        max_items_per_run=config.max_items_per_run,
    )
