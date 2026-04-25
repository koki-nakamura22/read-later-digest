from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any

import boto3  # type: ignore[import-untyped]
import httpx
from anthropic import AsyncAnthropic
from notion_client import Client as NotionClient

from read_later_digest.adapters.article_fetcher import ArticleFetcher
from read_later_digest.adapters.llm.claude import ClaudeLLMClient
from read_later_digest.adapters.mailer.ses import SesMailer
from read_later_digest.adapters.notion_repository import NotionClientLike, NotionRepository
from read_later_digest.config import Config
from read_later_digest.domain.digest_builder import DigestBuilder
from read_later_digest.domain.models import RunResult
from read_later_digest.logging_setup import logger
from read_later_digest.orchestrator import Orchestrator


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint invoked by EventBridge Scheduler.

    Builds the orchestrator from environment-derived `Config`, runs the daily batch,
    logs a structured summary, and returns the RunResult fields. Re-raises on hard
    failures so EventBridge / CloudWatch surface the error.
    """
    logger.info("batch invoked", extra={"event_keys": list(event.keys()) if event else []})
    config = Config.from_env()
    result = asyncio.run(_run(config))
    summary = asdict(result)
    logger.info("batch completed", extra=summary)
    return summary


async def _run(config: Config) -> RunResult:
    notion_client: NotionClientLike = NotionClient(auth=config.notion_token)  # type: ignore[assignment]
    notion_repo = NotionRepository(
        client=notion_client,
        db_id=config.notion_db_id,
        status_property=config.notion_status_property,
        status_unread=config.notion_status_unread,
        status_processed=config.notion_status_processed,
        type_property=config.notion_type_property,
        priority_property=config.notion_priority_property,
    )
    http_client = httpx.AsyncClient(timeout=config.fetch_timeout_sec)
    fetcher = ArticleFetcher(
        client=http_client,
        timeout_sec=config.fetch_timeout_sec,
        body_max_chars=config.llm_body_max_chars,
    )
    llm = ClaudeLLMClient(
        client=AsyncAnthropic(api_key=config.anthropic_api_key),  # type: ignore[arg-type]
        model=config.llm_model,
        body_max_chars=config.llm_body_max_chars,
        max_rate_limit_retries=config.llm_max_rate_limit_retries,
        initial_backoff_sec=config.llm_initial_backoff_sec,
    )
    mailer = SesMailer(
        client=boto3.client("ses", region_name=config.aws_region),
        source=config.mail_from,
    )
    orchestrator = Orchestrator(
        notion=notion_repo,
        fetcher=fetcher,
        llm=llm,
        mailer=mailer,
        digest_builder=DigestBuilder(),
        mail_to=config.mail_to,
        llm_concurrency=config.llm_concurrency,
    )
    try:
        return await orchestrator.run()
    finally:
        await http_client.aclose()
