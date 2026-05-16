"""AWS Lambda entrypoint invoked by EventBridge Scheduler.

Operational constraints:
- Items per invocation: ≤ MAX_ITEMS_PER_RUN (default 30) due to serial
  LLM execution. Excess items time out and remain "未読" for retry.
- Parallel execution support is tracked at koki-nakamura22/inboxkit#40.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any

from aws_lambda_powertools import Logger

from read_later_digest.config import Config
from read_later_digest.domain.models import ReadLaterRunResult
from read_later_digest.logging_setup import logger
from read_later_digest.wiring import build_digester


def _attach_powertools_handler_to_digestkit(powertools_logger: Logger) -> None:
    """digestkit の全 logger を Powertools handler に流す.

    Lambda コンテナ再利用で複数回呼ばれても重複 attach しないよう冪等ガード.
    子 logger (`digestkit.summarizers.*` 等) は独自 handler をクリア + propagate=True.
    """
    digestkit_root = logging.getLogger("digestkit")
    if digestkit_root.handlers:
        return  # 冪等

    for name in list(logging.root.manager.loggerDict):
        if name.startswith("digestkit."):
            sublogger = logging.getLogger(name)
            sublogger.handlers.clear()
            sublogger.propagate = True

    for h in powertools_logger.handlers:
        digestkit_root.addHandler(h)
    digestkit_root.setLevel(logging.INFO)
    digestkit_root.propagate = False


@logger.inject_lambda_context
def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """AWS Lambda entrypoint invoked by EventBridge Scheduler."""
    logger.info("batch invoked", extra={"event_keys": list(event.keys()) if event else []})
    config = Config.from_env()
    result = _run(config)
    summary = asdict(result)
    logger.info("batch completed", extra=summary)
    return summary


def _run(config: Config) -> ReadLaterRunResult:
    _attach_powertools_handler_to_digestkit(logger)
    digester = build_digester(config)
    try:
        return digester.run()
    finally:
        close = getattr(digester.extractor, "close", None)
        if close is not None:
            close()
