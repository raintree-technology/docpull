"""Security hardening regression tests."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from docpull.http.client import AsyncHttpClient, _ValidatedResolver
from docpull.security.robots import RobotsChecker, _RobotsResponse
from docpull.security.url_validator import UrlValidationResult, UrlValidator


class _NullAsyncContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _DummyRateLimiter:
    def limit(self, url: str) -> _NullAsyncContext:
        return _NullAsyncContext()


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        url: str = "https://example.com",
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(chunks or [])
        self.url = url

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _FakeRequestContext:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return self._response

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _FakeRequestContext:
        self.calls.append((url, kwargs))
        return _FakeRequestContext(self._responses.pop(0))

    def head(self, url: str, **kwargs: object) -> _FakeRequestContext:
        self.calls.append((url, kwargs))
        return _FakeRequestContext(self._responses.pop(0))


class TestUrlValidatorResolution:
    def test_rejects_public_hostname_that_resolves_to_loopback(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: ["127.0.0.1"])

        result = validator.validate("https://docs.example.com")

        assert result.is_valid is False
        assert result.rejection_reason is not None
        assert "blocked address '127.0.0.1'" in result.rejection_reason

    def test_allows_public_hostname_with_public_dns_answers(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: ["93.184.216.34"])

        result = validator.validate("https://docs.example.com")

        assert result.is_valid is True

    def test_resolve_allowed_addresses_returns_public_ips(self) -> None:
        validator = UrlValidator(
            resolver=lambda hostname: [
                "93.184.216.34",
                "2606:2800:220:1:248:1893:25c8:1946",
            ]
        )

        addresses = validator.resolve_allowed_addresses("docs.example.com")

        assert addresses == ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"]


class TestValidatedResolver:
    @pytest.mark.asyncio
    async def test_transport_resolver_returns_numeric_addresses(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: ["93.184.216.34"])
        resolver = _ValidatedResolver(validator)

        resolved = await resolver.resolve("docs.example.com", 443)

        assert resolved == [
            {
                "hostname": "docs.example.com",
                "host": "93.184.216.34",
                "port": 443,
                "family": socket.AF_INET,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
        ]

    @pytest.mark.asyncio
    async def test_transport_resolver_rejects_rebound_private_address(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: ["169.254.169.254"])
        resolver = _ValidatedResolver(validator)

        with pytest.raises(OSError, match="blocked address"):
            await resolver.resolve("docs.example.com", 443)


class TestRedirectValidation:
    @pytest.mark.asyncio
    async def test_http_client_rejects_unsafe_redirect_targets(self) -> None:
        validator = MagicMock()
        validator.validate.side_effect = [
            UrlValidationResult.valid(),
            UrlValidationResult.invalid("Private IP address '169.254.169.254' not allowed"),
        ]

        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            url_validator=validator,
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    302,
                    headers={"Location": "https://169.254.169.254/latest/meta-data"},
                    url="https://public.example/start",
                )
            ]
        )

        with pytest.raises(ValueError, match="URL validation failed"):
            await client.get("https://public.example/start")

        assert client._session is not None
        assert len(client._session.calls) == 1
        assert validator.validate.call_count == 2

    @pytest.mark.asyncio
    async def test_http_client_strips_auth_headers_for_off_scope_requests(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            auth_headers={
                "Authorization": "Bearer top-secret",
                "Cookie": "session=abc123",
                "X-Trace": "keep-me",
            },
            auth_scope_hosts={"docs.example.com"},
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    chunks=[b"ok"],
                    url="https://evil.example/collect",
                )
            ]
        )

        await client.get("https://evil.example/collect")

        assert client._session is not None
        _, kwargs = client._session.calls[0]
        assert kwargs["headers"] == {"X-Trace": "keep-me"}

    def test_http_client_rejects_insecure_tls_override(self) -> None:
        with pytest.raises(ValueError, match="Insecure TLS is not supported"):
            AsyncHttpClient(
                rate_limiter=_DummyRateLimiter(),
                allow_insecure_tls=True,
            )

    def test_robots_checker_rejects_insecure_tls_override(self) -> None:
        with pytest.raises(ValueError, match="Insecure TLS is not supported"):
            RobotsChecker(allow_insecure_tls=True)

    def test_robots_checker_stops_on_unsafe_redirect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        validator = MagicMock()
        validator.validate.side_effect = [
            UrlValidationResult.valid(),
            UrlValidationResult.invalid("Private IP address '169.254.169.254' not allowed"),
        ]

        checker = RobotsChecker(url_validator=validator)
        calls: list[str] = []

        def fake_fetch(url: str) -> _RobotsResponse:
            calls.append(url)
            return _RobotsResponse(
                status_code=302,
                headers={"Location": "https://169.254.169.254/robots.txt"},
                text="",
            )

        monkeypatch.setattr(checker, "_fetch_url", fake_fetch)
        entry = checker._fetch_robots("public.example", "https://public.example/robots.txt")

        assert entry.parser is None
        assert entry.status == "error"
        assert calls == ["https://public.example/robots.txt"]
        assert validator.validate.call_count == 2

    def test_robots_checker_blocks_on_fetch_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = RobotsChecker()

        def fake_fetch(url: str) -> _RobotsResponse:
            raise OSError("network down")

        monkeypatch.setattr(checker, "_fetch_url", fake_fetch)

        assert checker.is_allowed("https://public.example/docs") is False

    def test_robots_checker_allows_when_robots_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = RobotsChecker()

        def fake_fetch(url: str) -> _RobotsResponse:
            return _RobotsResponse(status_code=404, headers={}, text="")

        monkeypatch.setattr(checker, "_fetch_url", fake_fetch)

        assert checker.is_allowed("https://public.example/docs") is True

    def test_robots_checker_blocks_on_parser_runtime_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = RobotsChecker()
        parser = MagicMock()
        parser.can_fetch.side_effect = RuntimeError("parser failed")

        monkeypatch.setattr(
            checker,
            "_fetch_robots",
            lambda domain, robots_url: SimpleNamespace(parser=parser, status="present"),
        )

        assert checker.is_allowed("https://public.example/docs") is False


# ---------------------------------------------------------------------------
# CRLF header injection prevention
# ---------------------------------------------------------------------------


class TestCrlfHeaderInjection:
    """Verify that CR, LF, and null bytes are rejected in HTTP header values."""

    def test_user_agent_rejects_newline(self) -> None:
        from docpull.models.config import NetworkConfig

        with pytest.raises(ValidationError, match="must not contain CR, LF"):
            NetworkConfig(user_agent="Mozilla/5.0\nX-Injected: true")

    def test_user_agent_rejects_carriage_return(self) -> None:
        from docpull.models.config import NetworkConfig

        with pytest.raises(ValidationError, match="must not contain CR, LF"):
            NetworkConfig(user_agent="Mozilla/5.0\rX-Injected: true")

    def test_user_agent_rejects_null_byte(self) -> None:
        from docpull.models.config import NetworkConfig

        with pytest.raises(ValidationError, match="must not contain CR, LF"):
            NetworkConfig(user_agent="Mozilla/5.0\x00X-Injected: true")

    def test_user_agent_accepts_clean_value(self) -> None:
        from docpull.models.config import NetworkConfig

        config = NetworkConfig(user_agent="docpull/2.0 (custom)")
        assert config.user_agent == "docpull/2.0 (custom)"

    def test_auth_header_name_rejects_crlf(self) -> None:
        from docpull.models.config import AuthConfig, AuthType

        with pytest.raises(ValidationError, match="must not contain CR, LF"):
            AuthConfig(type=AuthType.HEADER, header_name="X-Auth\r\n", header_value="token")

    def test_auth_header_value_rejects_crlf(self) -> None:
        from docpull.models.config import AuthConfig, AuthType

        with pytest.raises(ValidationError, match="must not contain CR, LF"):
            AuthConfig(type=AuthType.HEADER, header_name="X-Auth", header_value="token\r\nX-Evil: true")

    def test_auth_header_accepts_clean_values(self) -> None:
        from docpull.models.config import AuthConfig, AuthType

        config = AuthConfig(type=AuthType.HEADER, header_name="X-Api-Key", header_value="abc123")
        assert config.header_name == "X-Api-Key"
        assert config.header_value == "abc123"

    def test_client_init_rejects_crlf_user_agent(self) -> None:
        with pytest.raises(ValueError, match="header injection"):
            AsyncHttpClient(
                rate_limiter=_DummyRateLimiter(),
                user_agent="agent\r\nX-Injected: yes",
            )

    def test_client_init_rejects_crlf_auth_header(self) -> None:
        with pytest.raises(ValueError, match="header injection"):
            AsyncHttpClient(
                rate_limiter=_DummyRateLimiter(),
                auth_headers={"Authorization": "Bearer tok\nen"},
            )

    def test_require_pinned_dns_with_proxy_is_rejected(self) -> None:
        """`--require-pinned-dns` must error out when a proxy is also set.

        The proxy disables docpull's connector-level DNS pinning. Marketing
        promises DNS-pinning at connect time; this flag lets agent-driven
        callers refuse the weakened proxy posture instead of silently
        accepting it.
        """
        with pytest.raises(ValueError, match="require_pinned_dns"):
            AsyncHttpClient(
                rate_limiter=_DummyRateLimiter(),
                proxy="http://corp.proxy:8080",
                require_pinned_dns=True,
            )

    def test_require_pinned_dns_without_proxy_is_accepted(self) -> None:
        """No proxy, --require-pinned-dns is a no-op (the connector-level
        pin is already in effect for direct connections)."""
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            require_pinned_dns=True,
        )
        assert client is not None  # construction succeeded

    def test_default_user_agent_is_honest_docpull_identity(self) -> None:
        """Default UA must identify as docpull, not camouflage as a browser.

        Polite-crawling claim requires that operators can scope robots.txt
        rules at User-Agent: docpull and have the actual requests match.
        """
        from docpull import __version__

        client = AsyncHttpClient(rate_limiter=_DummyRateLimiter())
        assert client.user_agent.startswith(f"docpull/{__version__}")
        assert "Mozilla" not in client.user_agent


# ---------------------------------------------------------------------------
# Dead code removal verification
# ---------------------------------------------------------------------------


class TestDeadCodeRemoved:
    """Verify that dangerous dead code has been removed."""

    def test_integration_config_removed_from_docpull_config(self) -> None:
        from docpull.models.config import DocpullConfig

        assert "integration" not in DocpullConfig.model_fields

    def test_archive_created_event_removed(self) -> None:
        from docpull.models.events import EventType

        assert not hasattr(EventType, "ARCHIVE_CREATED")

    def test_git_committed_event_removed(self) -> None:
        from docpull.models.events import EventType

        assert not hasattr(EventType, "GIT_COMMITTED")
