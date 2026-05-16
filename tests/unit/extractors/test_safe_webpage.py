from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from digestkit.extractors import ExtractionError
from digestkit.types import Item

from read_later_digest.domain.models import FetchFailureReason
from read_later_digest.extractors.safe_webpage import (
    DEFAULT_BODY_MAX_CHARS,
    DEFAULT_TIMEOUT_SEC,
    DEFAULT_USER_AGENT,
    FetchFailure,
    SafeWebPageExtractor,
)

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
    """Resolver stub: pretend every hostname resolves to a public IP so SSRF guard passes."""
    return ["93.184.216.34"]


def _build_extractor(
    client: httpx.Client,
    *,
    user_agent: str | None = None,
    timeout_sec: float | None = None,
    body_max_chars: int | None = None,
    host_resolver: Callable[[str], list[str]] | None = None,
) -> SafeWebPageExtractor:
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
    return SafeWebPageExtractor(**kwargs)


def _item(url: str = EXTERNAL_URL) -> Item:
    return Item(id="i1", payload=url)


@pytest.fixture
def captured_extractor_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, dict[str, Any]]]:
    from read_later_digest.extractors import safe_webpage as extractor_module

    captured: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        captured.append((msg, kwargs.get("extra", {})))

    monkeypatch.setattr(extractor_module.logger, "warning", _capture)
    return captured


class TestExtractSuccess:
    def test_returns_str_for_200_response(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            extractor = _build_extractor(client)
            # Act
            result = extractor.extract(_item())
        # Assert
        assert isinstance(result, str)

    def test_extracts_article_body_text(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            extractor = _build_extractor(client)
            # Act
            result = extractor.extract(_item())
        # Assert
        assert "first paragraph" in result


class TestExtractHttpErrorBoundaries:
    @pytest.mark.parametrize("status_code", [400, 404, 499])
    def test_4xx_raises_http_4xx(self, status_code: int) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(status_code, html=""))
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item())
        assert exc.value.reason is FetchFailureReason.HTTP_4XX
        assert exc.value.status_code == status_code

    @pytest.mark.parametrize("status_code", [500, 503, 599])
    def test_5xx_raises_http_5xx(self, status_code: int) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(status_code, html=""))
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item())
        assert exc.value.reason is FetchFailureReason.HTTP_5XX
        assert exc.value.status_code == status_code

    def test_http_error_emits_warning_log(
        self, captured_extractor_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(404, html=""))
            extractor = _build_extractor(client)
            # Act
            with pytest.raises(FetchFailure):
                extractor.extract(_item())
        # Assert
        assert any(extra.get("status") == 404 for _, extra in captured_extractor_warnings)


class TestExtractTimeoutAndNetwork:
    def test_timeout_raises_fetch_failure(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item())
        assert exc.value.reason is FetchFailureReason.TIMEOUT
        assert exc.value.status_code is None

    def test_connect_error_raises_network_failure(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ConnectError("refused"))
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item())
        assert exc.value.reason is FetchFailureReason.NETWORK
        assert exc.value.status_code is None

    def test_timeout_emits_warning_log(
        self, captured_extractor_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=httpx.ReadTimeout("read timeout"))
            extractor = _build_extractor(client)
            # Act
            with pytest.raises(FetchFailure):
                extractor.extract(_item())
        # Assert
        assert any(extra.get("url") == EXTERNAL_URL for _, extra in captured_extractor_warnings)


class TestExtractExtractionFailure:
    def test_unextractable_html_raises_extraction_empty(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(
                return_value=httpx.Response(200, html="<html><body></body></html>")
            )
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item())
        assert exc.value.reason is FetchFailureReason.EXTRACTION_EMPTY

    def test_whitespace_only_extraction_is_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            monkeypatch.setattr(
                "read_later_digest.extractors.safe_webpage.trafilatura.extract",
                lambda *args, **kwargs: "   \n\t  ",
            )
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item())
        assert exc.value.reason is FetchFailureReason.EXTRACTION_EMPTY


class TestExtractTruncationBoundaries:
    @pytest.mark.parametrize(
        "extracted_len, max_chars, expected_len",
        [
            (999, 1000, 999),
            (1000, 1000, 1000),
            (1001, 1000, 1000),
            (50_000, 1000, 1000),
        ],
    )
    def test_truncation_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        extracted_len: int,
        max_chars: int,
        expected_len: int,
    ) -> None:
        # Arrange
        long_text = "x" * extracted_len
        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(return_value=httpx.Response(200, html=SAMPLE_HTML))
            monkeypatch.setattr(
                "read_later_digest.extractors.safe_webpage.trafilatura.extract",
                lambda *args, **kwargs: long_text,
            )
            extractor = _build_extractor(client, body_max_chars=max_chars)
            # Act
            result = extractor.extract(_item())
        # Assert
        assert len(result) == expected_len


class TestExtractSchemeGuard:
    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x"])
    def test_disallowed_scheme_raises_without_http_call(self, url: str) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item(url))
        assert exc.value.reason is FetchFailureReason.INVALID_SCHEME
        assert respx.calls.call_count == 0


class TestExtractSsrfGuard:
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
    def test_internal_addresses_are_blocked_without_http_call(
        self, resolved_ip: str, label: str
    ) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=lambda _host: [resolved_ip])
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item("https://internal.example/x"))
        assert exc.value.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    def test_unresolvable_host_is_blocked(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=lambda _host: [])
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item("https://nope.example/x"))
        assert exc.value.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    def test_localhost_literal_is_blocked(self) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=_public_ip_resolver)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item("https://localhost/x"))
        assert exc.value.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    def test_localhost_with_trailing_dot_is_blocked(self) -> None:
        # Arrange — "localhost." is a valid FQDN alias; implementation short-circuits it
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=_public_ip_resolver)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item("https://localhost./x"))
        assert exc.value.reason is FetchFailureReason.BLOCKED_HOST

    def test_reserved_ip_is_blocked(self) -> None:
        # Arrange — 240.0.0.0/4 is reserved per ipaddress.ip_address.is_reserved
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=lambda _host: ["240.0.0.1"])
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item("https://reserved.example/x"))
        assert exc.value.reason is FetchFailureReason.BLOCKED_HOST
        assert respx.calls.call_count == 0

    def test_resolver_raising_gaierror_blocks_host(self) -> None:
        # Arrange — resolver raises gaierror (DNS failure) → blocked, different path from []
        def _gaierror_resolver(_host: str) -> list[str]:
            raise socket.gaierror("name or service not known")

        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=_gaierror_resolver)
            # Act / Assert
            with pytest.raises(FetchFailure) as exc:
                extractor.extract(_item("https://unresolvable.example/x"))
        assert exc.value.reason is FetchFailureReason.BLOCKED_HOST

    def test_resolver_returning_invalid_ip_string_does_not_block(self) -> None:
        # Arrange — resolver returns a non-IP string; ValueError → continue in _is_blocked_host
        # → no valid address blocks → host passes through (not blocked)
        with httpx.Client() as client, respx.mock:
            respx.get("https://weird.example/x").mock(
                return_value=httpx.Response(200, html=SAMPLE_HTML)
            )
            extractor = _build_extractor(client, host_resolver=lambda _host: ["not-an-ip"])
            # Act
            result = extractor.extract(_item("https://weird.example/x"))
        # Assert
        assert isinstance(result, str)

    def test_blocked_host_emits_warning_log(
        self, captured_extractor_warnings: list[tuple[str, dict[str, Any]]]
    ) -> None:
        # Arrange
        with httpx.Client() as client, respx.mock:
            extractor = _build_extractor(client, host_resolver=lambda _host: ["127.0.0.1"])
            # Act
            with pytest.raises(FetchFailure):
                extractor.extract(_item("https://internal.example/x"))
        # Assert
        assert any(
            extra.get("host") == "internal.example" for _, extra in captured_extractor_warnings
        )


class TestExtractUserAgent:
    def test_custom_user_agent_header_is_sent(self) -> None:
        # Arrange
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent", "")
            return httpx.Response(200, html=SAMPLE_HTML)

        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=_capture)
            extractor = _build_extractor(client, user_agent="custom-agent/2.0")
            # Act
            extractor.extract(_item())
        # Assert
        assert captured["ua"] == "custom-agent/2.0"

    def test_default_user_agent_contains_package_name(self) -> None:
        # Arrange
        captured: dict[str, str] = {}

        def _capture(request: httpx.Request) -> httpx.Response:
            captured["ua"] = request.headers.get("User-Agent", "")
            return httpx.Response(200, html=SAMPLE_HTML)

        with httpx.Client() as client, respx.mock:
            respx.get(EXTERNAL_URL).mock(side_effect=_capture)
            extractor = _build_extractor(client)
            # Act
            extractor.extract(_item())
        # Assert
        assert "read-later-digest/" in captured["ua"]
        assert "github.com/koki-nakamura22/read-later-digest" in captured["ua"]

    def test_default_user_agent_constant_matches_expected_format(self) -> None:
        assert "read-later-digest/" in DEFAULT_USER_AGENT
        assert "github.com/koki-nakamura22/read-later-digest" in DEFAULT_USER_AGENT


class TestExtractModuleDefaults:
    def test_default_body_max_chars_matches_design(self) -> None:
        assert DEFAULT_BODY_MAX_CHARS == 30_000

    def test_default_timeout_matches_design(self) -> None:
        assert DEFAULT_TIMEOUT_SEC == 15.0


class TestFetchFailureException:
    def test_is_subclass_of_extraction_error(self) -> None:
        # AC4: FetchFailure must satisfy digestkit ExtractionError hierarchy
        exc = FetchFailure(FetchFailureReason.TIMEOUT)
        assert isinstance(exc, ExtractionError)

    def test_message_includes_reason_value(self) -> None:
        exc = FetchFailure(FetchFailureReason.INVALID_SCHEME)
        assert "invalid_scheme" in str(exc)

    def test_message_includes_http_status_when_set(self) -> None:
        exc = FetchFailure(FetchFailureReason.HTTP_4XX, status_code=404)
        assert "404" in str(exc)

    def test_message_omits_status_when_none(self) -> None:
        exc = FetchFailure(FetchFailureReason.TIMEOUT)
        assert "HTTP" not in str(exc)

    def test_status_code_stored_as_attribute(self) -> None:
        exc = FetchFailure(FetchFailureReason.HTTP_5XX, status_code=503)
        assert exc.status_code == 503

    def test_reason_stored_as_attribute(self) -> None:
        exc = FetchFailure(FetchFailureReason.BLOCKED_HOST)
        assert exc.reason is FetchFailureReason.BLOCKED_HOST


class TestExtractorClientLifecycle:
    def test_close_with_owned_client_closes_internal_client(self) -> None:
        # Arrange — no external client → extractor owns the httpx.Client
        extractor = SafeWebPageExtractor(host_resolver=_public_ip_resolver)
        internal_client = extractor._client
        # Act
        extractor.close()
        # Assert
        assert internal_client.is_closed

    def test_close_with_external_client_does_not_close_it(self) -> None:
        # Arrange — external client is passed → extractor must not close it
        client = httpx.Client()
        extractor = SafeWebPageExtractor(client=client, host_resolver=_public_ip_resolver)
        # Act
        extractor.close()
        # Assert: caller still owns and can close the client
        assert not client.is_closed
        client.close()
