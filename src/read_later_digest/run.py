from __future__ import annotations

import argparse
from dataclasses import asdict

from read_later_digest.config import Config
from read_later_digest.logging_setup import logger
from read_later_digest.wiring import build_digester


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="read-later-digest",
        description="Run the read-later-digest daily batch locally.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the digest but skip notifications and Notion writeback.",
    )
    return parser.parse_args()


def main() -> None:
    _parse_args()
    config = Config.from_env()
    digester = build_digester(config)
    try:
        result = digester.run()
    finally:
        close = getattr(digester.extractor, "close", None)
        if close is not None:
            close()
    logger.info("local run completed", extra=asdict(result))
    print(asdict(result))


if __name__ == "__main__":
    main()
