from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

import httpx
import trafilatura

from read_later_digest.domain.models import FetchFailureReason, FetchResult
from read_later_digest.logging_setup import logger

ALLOWED_SCHEMES = ("http", "https")
DEFAULT_USER_AGENT = "read-later-digest/0.1"
DEFAULT_TIMEOUT_SEC = 15.0
DEFAULT_BODY_MAX_CHARS = 30_000

HostResolver = Callable[[str], list[str]]


def _resolve_addresses(host: str) -> list[str]:
    """Default host resolver returning IP strings via socket.getaddrinfo."""
    infos = socket.getaddrinfo(host, None)
    addresses: list[str] = []
    for info in infos:
        addr = info[4][0]
        if isinstance(addr, str):
            addresses.append(addr)
    return addresses


def _is_blocked_host(host: str, resolver: HostResolver) -> bool:
    """Return True for hostnames that resolve to loopback/private/link-local addresses.

    Blocks SSRF-style requests to internal infra. Also rejects the literal
    `localhost` short-circuit even when DNS resolution would map it elsewhere.
    """
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


class ArticleFetcher:
    """Fetch a URL and extract the readable article body as plain text.

    Errors are surfaced via `FetchResult` rather than raised, so a per-article
    failure does not cascade to the whole batch in the orchestrator.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        body_max_chars: int = DEFAULT_BODY_MAX_CHARS,
        host_resolver: HostResolver = _resolve_addresses,
    ) -> None:
        self._client = client
        self._user_agent = user_agent
        self._timeout_sec = timeout_sec
        self._body_max_chars = body_max_chars
        self._host_resolver = host_resolver

    async def fetch(self, url: str) -> FetchResult:
        """Return the article body for `url`, or a failure result with a reason."""
        scheme_failure = self._validate_scheme(url)
        if scheme_failure is not None:
            return scheme_failure

        host_failure = self._validate_host(url)
        if host_failure is not None:
            return host_failure

        return await self._fetch_and_extract(url)

    def _validate_scheme(self, url: str) -> FetchResult | None:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            logger.warning(
                "fetch rejected: invalid scheme",
                extra={"url": url, "scheme": parts.scheme},
            )
            return self._failure(url, FetchFailureReason.INVALID_SCHEME)
        return None

    def _validate_host(self, url: str) -> FetchResult | None:
        host = urlsplit(url).hostname or ""
        if _is_blocked_host(host, self._host_resolver):
            logger.warning("fetch rejected: blocked host", extra={"url": url, "host": host})
            return self._failure(url, FetchFailureReason.BLOCKED_HOST)
        return None

    async def _fetch_and_extract(self, url: str) -> FetchResult:
        try:
            response = await self._client.get(
                url,
                headers={"User-Agent": self._user_agent},
                timeout=self._timeout_sec,
                follow_redirects=True,
            )
        except httpx.TimeoutException:
            logger.warning("fetch failed: timeout", extra={"url": url})
            return self._failure(url, FetchFailureReason.TIMEOUT)
        except httpx.HTTPError as e:
            logger.warning("fetch failed: network error", extra={"url": url, "error": str(e)})
            return self._failure(url, FetchFailureReason.NETWORK)

        status = response.status_code
        if 400 <= status < 500:
            logger.warning("fetch failed: http 4xx", extra={"url": url, "status": status})
            return self._failure(url, FetchFailureReason.HTTP_4XX, status_code=status)
        if 500 <= status < 600:
            logger.warning("fetch failed: http 5xx", extra={"url": url, "status": status})
            return self._failure(url, FetchFailureReason.HTTP_5XX, status_code=status)

        text = await asyncio.to_thread(self._extract_body, response.text)
        if text is None or text.strip() == "":
            logger.warning("fetch failed: empty extraction", extra={"url": url, "status": status})
            return self._failure(url, FetchFailureReason.EXTRACTION_EMPTY, status_code=status)

        truncated = text[: self._body_max_chars]
        return FetchResult(
            url=url,
            ok=True,
            text=truncated,
            reason=None,
            status_code=status,
        )

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

    @staticmethod
    def _failure(
        url: str,
        reason: FetchFailureReason,
        *,
        status_code: int | None = None,
    ) -> FetchResult:
        return FetchResult(
            url=url,
            ok=False,
            text=None,
            reason=reason,
            status_code=status_code,
        )
