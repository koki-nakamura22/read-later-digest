from __future__ import annotations

import contextlib
import ipaddress
import os
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import trafilatura
from digestkit.extractors import ExtractionError
from digestkit.types import Item

from read_later_digest import __version__
from read_later_digest.domain.models import FetchFailureReason
from read_later_digest.logging_setup import logger

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_USER_AGENT = os.getenv(
    "FETCH_USER_AGENT",
    f"read-later-digest/{__version__} (+https://github.com/koki-nakamura22/read-later-digest)",
)
DEFAULT_TIMEOUT_SEC = 15.0
DEFAULT_BODY_MAX_CHARS = 30_000

HostResolver = Callable[[str], list[str]]


class FetchFailure(ExtractionError):  # noqa: N818
    def __init__(self, reason: FetchFailureReason, *, status_code: int | None = None) -> None:
        self.reason = reason
        self.status_code = status_code
        msg = f"fetch failed: {reason.value}"
        if status_code is not None:
            msg += f" (HTTP {status_code})"
        super().__init__(msg)


def _resolve_addresses(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, None)
    addresses: list[str] = []
    for info in infos:
        addr = info[4][0]
        if isinstance(addr, str):
            addresses.append(addr)
    return addresses


def _is_blocked_host(host: str, resolver: HostResolver) -> bool:
    if host.lower() in {"localhost", "localhost."}:
        return True
    try:
        addresses = resolver(host)
    except socket.gaierror:
        return True
    if not addresses:
        return True
    for addr in addresses:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
            return True
    return False


class SafeWebPageExtractor:
    """Sync web page extractor implementing digestkit Extractor Protocol.

    Sync rewrite of ArticleFetcher preserving SSRF/scheme/status classification,
    trafilatura options, and truncation via httpx.Client.
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        body_max_chars: int = DEFAULT_BODY_MAX_CHARS,
        host_resolver: HostResolver | None = None,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_sec, follow_redirects=True)
        self._user_agent = user_agent
        self._timeout_sec = timeout_sec
        self._body_max_chars = body_max_chars
        self._host_resolver = host_resolver or _resolve_addresses

    def extract(self, item: Item) -> str:
        url = str(item.payload)
        self._validate_scheme(url)
        self._validate_host(url)
        return self._fetch_and_extract(url)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __del__(self) -> None:
        with contextlib.suppress(Exception):
            self.close()

    def _validate_scheme(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            logger.warning(
                "fetch rejected: invalid scheme",
                extra={"url": url, "scheme": parts.scheme},
            )
            raise FetchFailure(FetchFailureReason.INVALID_SCHEME)

    def _validate_host(self, url: str) -> None:
        host = urlsplit(url).hostname or ""
        if _is_blocked_host(host, self._host_resolver):
            logger.warning("fetch rejected: blocked host", extra={"url": url, "host": host})
            raise FetchFailure(FetchFailureReason.BLOCKED_HOST)

    def _fetch_and_extract(self, url: str) -> str:
        try:
            response = self._client.get(url, headers={"User-Agent": self._user_agent})
        except httpx.TimeoutException:
            logger.warning("fetch failed: timeout", extra={"url": url})
            raise FetchFailure(FetchFailureReason.TIMEOUT) from None
        except httpx.HTTPError as e:
            logger.warning("fetch failed: network error", extra={"url": url, "error": str(e)})
            raise FetchFailure(FetchFailureReason.NETWORK) from None

        status = response.status_code
        if 400 <= status < 500:
            logger.warning("fetch failed: http 4xx", extra={"url": url, "status": status})
            raise FetchFailure(FetchFailureReason.HTTP_4XX, status_code=status)
        if 500 <= status < 600:
            logger.warning("fetch failed: http 5xx", extra={"url": url, "status": status})
            raise FetchFailure(FetchFailureReason.HTTP_5XX, status_code=status)

        text = self._extract_body(response.text)
        if text is None or text.strip() == "":
            logger.warning("fetch failed: empty extraction", extra={"url": url, "status": status})
            raise FetchFailure(FetchFailureReason.EXTRACTION_EMPTY)

        return text[: self._body_max_chars]

    @staticmethod
    def _extract_body(html: str) -> str | None:
        result: Any = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            output_format="txt",
        )
        if isinstance(result, str):
            return result
        return None
