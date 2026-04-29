#!/usr/bin/env python3
"""One-shot setup script: create the Notion DB used by read-later-digest.

Independent from `src/read_later_digest/` — schema is hard-coded here so the
Lambda runtime stays decoupled from this CLI's dependency on `notion-client`.

Usage:
    uv run python scripts/create-notion-db.py --parent-page-id <PAGE_ID>
    uv run python scripts/create-notion-db.py --parent-page-id <PAGE_ID> --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

# Schema (mirrors docs/functional-design.md). Hard-coded by design — do NOT
# share with src/ adapters.
STATUS_OPTIONS: tuple[str, ...] = ("未読", "処理済み", "不要")
TYPE_OPTIONS: tuple[str, ...] = ("記事", "技術", "ネタ", "仕事")
PRIORITY_OPTIONS: tuple[str, ...] = ("高", "中", "低")

# Notion formula: days elapsed since AddedAt.
AGE_FORMULA_EXPRESSION = 'dateBetween(now(), prop("AddedAt"), "days")'

DEFAULT_TITLE = "Read Later"


def _select(options: tuple[str, ...]) -> dict[str, Any]:
    return {"select": {"options": [{"name": name} for name in options]}}


def build_properties_payload() -> dict[str, Any]:
    """Return the `properties` dict for the Notion `databases.create` payload.

    Key order here is for human readability only — Notion renders columns
    title-first then alphabetical regardless of payload order, so the UI
    column order has to be set by manual drag in Notion after creation.
    """
    return {
        "Name": {"title": {}},
        "URL": {"url": {}},
        "Status": _select(STATUS_OPTIONS),
        "Type": _select(TYPE_OPTIONS),
        "Priority": _select(PRIORITY_OPTIONS),
        "AddedAt": {"created_time": {}},
        "Age": {"formula": {"expression": AGE_FORMULA_EXPRESSION}},
    }


def build_create_payload(parent_page_id: str, title: str) -> dict[str, Any]:
    """Return the full request body for `databases.create`.

    Notion API 2025-09-03+ (notion-client 3.x) requires properties to live
    under `initial_data_source.properties`. Top-level `properties` is silently
    ignored, which would create a DB with only the default `Name` title
    property — confirmed empirically when this script first shipped.
    """
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "initial_data_source": {"properties": build_properties_payload()},
    }


def _resolve_token(cli_token: str | None) -> str | None:
    if cli_token:
        return cli_token
    env = os.environ.get("NOTION_TOKEN")
    return env or None


def _extract_data_source_id(retrieved: dict[str, Any]) -> str | None:
    sources = retrieved.get("data_sources")
    if isinstance(sources, list) and sources:
        first = sources[0]
        if isinstance(first, dict):
            value = first.get("id")
            if isinstance(value, str):
                return value
    return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create the Notion database used by read-later-digest."
    )
    parser.add_argument(
        "--parent-page-id",
        required=True,
        help="Notion page ID under which to create the DB",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="Notion integration token (falls back to $NOTION_TOKEN)",
    )
    parser.add_argument(
        "--title",
        default=DEFAULT_TITLE,
        help=f"DB title (default: {DEFAULT_TITLE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print payload to stdout without calling Notion",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = build_create_payload(args.parent_page_id, args.title)

    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    token = _resolve_token(args.token)
    if not token:
        print(
            "error: Notion token not provided. Pass --token or set NOTION_TOKEN.",
            file=sys.stderr,
        )
        return 1

    # Imported lazily so --dry-run and unit tests don't require notion-client
    # to be installed in the environment importing this module.
    from notion_client import Client
    from notion_client.errors import APIResponseError

    client = Client(auth=token)
    try:
        created = client.databases.create(**payload)
        database_id = created["id"] if isinstance(created, dict) else None
        if not isinstance(database_id, str):
            print("error: Notion did not return a database id.", file=sys.stderr)
            return 1
        retrieved = client.databases.retrieve(database_id=database_id)
        data_source_id = _extract_data_source_id(retrieved) if isinstance(retrieved, dict) else None
    except APIResponseError as exc:
        print(f"error: Notion API error: {exc}", file=sys.stderr)
        return 1

    print(f"database_id={database_id}")
    if data_source_id:
        print(f"data_source_id={data_source_id}")
    else:
        print(
            "warning: data_source_id not present in retrieve response; "
            "your notion-client / API version may not expose it.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
