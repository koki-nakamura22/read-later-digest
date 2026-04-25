from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from read_later_digest.adapters.article_fetcher import ArticleFetcher
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


def _resolver_to_external() -> Any:
    """Pretend every hostname resolves to a public IP so SSRF guard does not block tests."""
    return lambda _host: ["93.184.216.34"]


def _build_fetcher(
    client: httpx.AsyncClient,
    *,
    user_agent: str = "test-agent/1.0",
    timeout_sec: float = 5.0,
    body_max_chars: int = 30_000,
    host_resolver: Any | None = None,
) -> ArticleFetcher:
    return ArticleFetcher(
        client=client,
        user_agent=user_agent,
        timeout_sec=timeout_sec,
        body_max_chars=body_max_chars,
        host_resolver=host_resolver or _resolver_to_external(),
    )


class TestFetchSuccess:
    async def test_returns_extracted_text_for_200_response(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is True
        assert result.text is not None
        assert "first paragraph" in result.text
        assert result.status_code == 200
        assert result.reason is None


class TestFetchHttpErrors:
    async def test_4xx_is_recorded_as_failure(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(404, html=""))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.HTTP_4XX
        assert result.status_code == 404

    async def test_5xx_is_recorded_as_failure(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(503, html=""))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.HTTP_5XX
        assert result.status_code == 503


class TestFetchTimeout:
    async def test_timeout_returns_failure_with_reason(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
            fetcher = _build_fetcher(client)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is False
        assert result.reason is FetchFailureReason.TIMEOUT


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


class TestFetchTruncation:
    async def test_body_is_truncated_when_longer_than_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        long_text = "x" * 50_000
        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            monkeypatch.setattr(
                "read_later_digest.adapters.article_fetcher.trafilatura.extract",
                lambda *args, **kwargs: long_text,
            )
            fetcher = _build_fetcher(client, body_max_chars=1_000)

            result = await fetcher.fetch(EXTERNAL_URL)

        assert result.ok is True
        assert result.text is not None
        assert len(result.text) == 1_000


class TestFetchSchemeAndHostGuards:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
    async def test_disallowed_scheme_is_rejected_without_http_call(self, url: str) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client)
            result = await fetcher.fetch(url)

        assert result.ok is False
        assert result.reason is FetchFailureReason.INVALID_SCHEME
        assert respx.calls.call_count == 0

    @pytest.mark.parametrize(
        "url, resolved_ip",
        [
            ("https://internal.example/x", "127.0.0.1"),
            ("https://internal.example/x", "10.0.0.5"),
            ("https://internal.example/x", "192.168.1.10"),
            ("https://internal.example/x", "172.16.0.1"),
            ("https://internal.example/x", "169.254.1.1"),
        ],
    )
    async def test_private_addresses_are_blocked(self, url: str, resolved_ip: str) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client, host_resolver=lambda _host: [resolved_ip])
            result = await fetcher.fetch(url)

        assert result.ok is False
        assert result.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    async def test_localhost_literal_is_blocked(self) -> None:
        async with httpx.AsyncClient() as client, respx.mock:
            fetcher = _build_fetcher(client, host_resolver=lambda _host: ["93.184.216.34"])
            result = await fetcher.fetch("https://localhost/x")

        assert result.ok is False
        assert result.reason is FetchFailureReason.BLOCKED_HOST


class TestFetchRequestSettings:
    async def test_user_agent_header_is_sent(self) -> None:
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent", "")
            return httpx.Response(200, html=SAMPLE_HTML)

        async with httpx.AsyncClient() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=_capture)
            fetcher = _build_fetcher(client, user_agent="custom-agent/2.0")

            await fetcher.fetch(EXTERNAL_URL)

        assert captured["ua"] == "custom-agent/2.0"

    async def test_timeout_setting_triggers_timeout_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_timeout: dict[str, Any] = {}

        async def _fake_get(self: Any, url: str, **kwargs: Any) -> httpx.Response:
            captured_timeout["value"] = kwargs.get("timeout")
            raise httpx.ReadTimeout("simulated")

        monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)

        async with httpx.AsyncClient() as client:
            fetcher = _build_fetcher(client, timeout_sec=2.5)
            result = await fetcher.fetch(EXTERNAL_URL)

        assert captured_timeout["value"] == 2.5
        assert result.reason is FetchFailureReason.TIMEOUT


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
