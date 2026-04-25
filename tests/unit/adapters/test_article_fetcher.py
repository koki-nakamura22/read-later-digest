from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from read_later_digest.adapters.article_fetcher import (
    DEFAULT_BODY_MAX_CHARS,
    DEFAULT_TIMEOUT_SEC,
    DEFAULT_USER_AGENT,
    ArticleFetcher,
)
from read_later_digest.domain.models import FetchFailureReason

SAMPLE_HTML = """
<html>
  <head><title>Sample Article</title></head>
  <body>
    <article>
      <h1>Sample Article</h1>
      <p>This is the first paragraph of the article body. It contains real content.</p>
      <p>Here is a second paragraph with more substantive text for the extractor.</p>
    </article>
  </body>
</html>
"""

EXTERNAL_HOST = "example.com"
EXTERNAL_URL = f"https://{EXTERNAL_HOST}/article"


def _public_ip_resolver(_host: str) -> list[str]:
    """Resolver stub: pretend every hostname resolves to a public IP so the SSRF guard passes."""
    return ["93.184.216.34"]


def _build_fetcher(
    client: httpx.AsyncClient,
    *,
    user_agent: str | None = None,
    timeout_sec: float | None = None,
    body_max_chars: int | None = None,
    host_resolver: Callable[[str], list[str]] | None = None,
) -> ArticleFetcher:
    """Construct a fetcher with production defaults unless overridden.

    Tests should pick the production default by passing None (or omitting the kwarg)
    to avoid silently exercising a different code path than the deployed config.
    """
    kwargs: dict[str, Any] = {
        "client": client,
        "host_resolver": host_resolver or _public_ip_resolver,
    }
    if user_agent is not None:
        kwargs["user_agent"] = user_agent
    if timeout_sec is not None:
        kwargs["timeout_sec"] = timeout_sec
    if body_max_chars is not None:
        kwargs["body_max_chars"] = body_max_chars
    return ArticleFetcher(**kwargs)


class TestFetchSuccess:
    async def test_returns_ok_true_for_200_response(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is True

    async def test_extracts_article_body_text(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.text is not None
        assert "first paragraph" in result.text

    async def test_records_status_code_and_clears_reason(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.status_code == 200
        assert result.reason is None


class TestFetchHttpErrorBoundaries:
    @pytest.mark.parametrize("status_code", [400, 404, 499])
    async def test_4xx_boundaries_are_recorded_as_http_4xx(self, status_code: int) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(status_code, html=""))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.HTTP_4XX
        assert result.status_code == status_code

    @pytest.mark.parametrize("status_code", [500, 503, 599])
    async def test_5xx_boundaries_are_recorded_as_http_5xx(self, status_code: int) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(status_code, html=""))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.HTTP_5XX
        assert result.status_code == status_code

    async def test_http_error_emits_warning_log(
        self, captured_fetcher_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(404, html=""))
            fetcher = _build_fetcher(client)

            await fetcher.fetch(EXTERNAL_URL)

        assert any(extra.get("status") == 404 for _, extra in captured_fetcher_warnings)


class TestFetchTimeoutAndNetwork:
    async def test_timeout_returns_failure_with_reason(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.TIMEOUT

    async def test_connect_error_is_recorded_as_network_failure(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ConnectError("refused"))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.NETWORK
        assert result.status_code is None

    async def test_timeout_emits_warning_log(
        self, captured_fetcher_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
            fetcher = _build_fetcher(client)

            await fetcher.fetch(EXTERNAL_URL)

        assert any(extra.get("url") == EXTERNAL_URL for _, extra in captured_fetcher_warnings)


class TestFetchExtractionFailure:
    async def test_unextractable_html_returns_extraction_empty(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(
                return_value=httpx.Response(200, html="<html><body></body></html>")
            )
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.EXTRACTION_EMPTY
        assert result.status_code == 200

    async def test_whitespace_only_extraction_is_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            monkeypatch.setattr(
                "read_later_digest.adapters.article_fetcher.trafilatura.extract",
                lambda *args, **kwargs: "   \n\t  ",
            )
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.EXTRACTION_EMPTY


class TestFetchTruncationBoundaries:
    @pytest.mark.parametrize(
        "extracted_len, max_chars, expected_len",
        [
            (999, 1000, 999),  # below limit: preserved
            (1000, 1000, 1000),  # equal to limit: preserved
            (1001, 1000, 1000),  # one over: truncated by 1
            (50_000, 1000, 1000),  # well over: truncated to limit
        ],
    )
    async def test_truncation_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        extracted_len: int,
        max_chars: int,
        expected_len: int,
    ) -> None:
        long_text = "x" * extracted_len
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            monkeypatch.setattr(
                "read_later_digest.adapters.article_fetcher.trafilatura.extract",
                lambda *args, **kwargs: long_text,
            )
            fetcher = _build_fetcher(client, body_max_chars=max_chars)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is True
        assert result.text is not None
        assert len(result.text) == expected_len


class TestFetchSchemeGuard:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
    async def test_disallowed_scheme_is_rejected_without_http_call(self, url: str) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client)
            result = await fetcher.fetch(url)

        assert result.ok is False
        assert result.reason is FetchFailureReason.INVALID_SCHEME
        assert respx.calls.call_count == 0


class TestFetchSsrfGuard:
    @pytest.mark.parametrize(
        "resolved_ip, label",
        [
            ("127.0.0.1", "loopback"),
            ("10.0.0.5", "private (10/8)"),
            ("172.16.0.1", "private (172.16/12)"),
            ("192.168.1.10", "private (192.168/16)"),
            ("169.254.1.1", "link-local"),
        ],
    )
    async def test_internal_addresses_are_blocked_without_http_call(
        self, resolved_ip: str, label: str
    ) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client, host_resolver=lambda _host: [resolved_ip])
            result = await fetcher.fetch("https://internal.example/x")

        assert result.ok is False
        assert result.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    async def test_unresolvable_host_is_blocked_without_http_call(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client, host_resolver=lambda _host: [])
            result = await fetcher.fetch("https://nope.example/x")

        assert result.ok is False
        assert result.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    async def test_localhost_literal_is_blocked_without_http_call(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client, host_resolver=_public_ip_resolver)
            result = await fetcher.fetch("https://localhost/x")

        assert result.ok is False
        assert result.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    async def test_blocked_host_emits_warning_log(
        self, captured_fetcher_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client, host_resolver=lambda _host: ["127.0.0.1"])
            await fetcher.fetch("https://internal.example/x")

        assert any(
            extra.get("host") == "internal.example" for _, extra in captured_fetcher_warnings
        )


class TestFetchRequestSettings:
    async def test_custom_user_agent_header_is_sent(self) -> None:
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent", "")
            return httpx.Response(200, html=SAMPLE_HTML)

        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=_capture)
            fetcher = _build_fetcher(client, user_agent="custom-agent/2.0")

            await fetcher.fetch(EXTERNAL_URL)

        assert captured["ua"] == "custom-agent/2.0"

    async def test_default_user_agent_is_sent_when_not_overridden(self) -> None:
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent", "")
            return httpx.Response(200, html=SAMPLE_HTML)

        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=_capture)
            fetcher = _build_fetcher(client)

            await fetcher.fetch(EXTERNAL_URL)

        assert captured["ua"] == DEFAULT_USER_AGENT

    async def test_timeout_setting_is_propagated_to_httpx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_timeout: dict[str, Any] = {}

        async def _fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
            captured_timeout["value"] = kwargs.get("timeout")
            raise httpx.ReadTimeout("simulated")

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        async with httpx.AsyncClient() as client:
            fetcher = _build_fetcher(client, timeout_sec=2.5)
            await fetcher.fetch(EXTERNAL_URL)

        assert captured_timeout["value"] == 2.5

    async def test_default_timeout_is_propagated_when_not_overridden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_timeout: dict[str, Any] = {}

        async def _fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
            captured_timeout["value"] = kwargs.get("timeout")
            raise httpx.ReadTimeout("simulated")

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        async with httpx.AsyncClient() as client:
            fetcher = _build_fetcher(client)
            await fetcher.fetch(EXTERNAL_URL)

        assert captured_timeout["value"] == DEFAULT_TIMEOUT_SEC


class TestFetchRedirect:
    async def test_follows_redirects_to_final_response(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(
                return_value=httpx.Response(301, headers={"Location": "https://example.com/final"})
            )
            respx.get("https://example.com/final").mock(
                return_value=httpx.Response(200, html=SAMPLE_HTML)
            )
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is True
        assert result.status_code == 200
        assert result.text is not None and "first paragraph" in result.text


class TestFetchModuleDefaults:
    """Sanity check: production defaults match what the design document claims."""

    def test_default_body_max_chars_matches_design(self) -> None:
        assert DEFAULT_BODY_MAX_CHARS == 30_000

    def test_default_timeout_matches_design(self) -> None:
        assert DEFAULT_TIMEOUT_SEC == 15.0
