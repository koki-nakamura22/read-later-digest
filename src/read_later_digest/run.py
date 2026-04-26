from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict

import boto3  # type: ignore[import-untyped]
import httpx
from anthropic import AsyncAnthropic
from notion_client import Client as NotionClient

from read_later_digest.adapters.article_fetcher import ArticleFetcher
from read_later_digest.adapters.llm.claude import ClaudeLLMClient
from read_later_digest.adapters.mailer.ses import SesMailer
from read_later_digest.adapters.notion_repository import NotionClientLike, NotionRepository
from read_later_digest.config import Config, NotificationChannel
from read_later_digest.domain.digest_builder import DigestBuilder
from read_later_digest.domain.models import RunResult
from read_later_digest.logging_setup import logger
from read_later_digest.orchestrator import Orchestrator


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="read-later-digest",
        description="Run the read-later-digest daily batch locally.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the digest but skip mail send and Notion writeback.",
    )
    return parser.parse_args()


async def _run(config: Config, *, dry_run: bool) -> RunResult:
    # See handler._run: same orchestrator constraint applies.
    if config.notification_channels != frozenset({NotificationChannel.MAIL}):
        raise NotImplementedError(
            "multi-channel notification routing is not yet wired in the orchestrator; "
            f"only NOTIFY_CHANNELS=mail is supported at runtime "
            f"(got: {sorted(c.value for c in config.notification_channels)})"
        )
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
    try:
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
        return await orchestrator.run(dry_run=dry_run)
    finally:
        await http_client.aclose()


def main() -> None:
    args = _parse_args()
    config = Config.from_env()
    result = asyncio.run(_run(config, dry_run=args.dry_run))
    logger.info("local run completed", extra=asdict(result))
    print(asdict(result))


if __name__ == "__main__":
    main()
