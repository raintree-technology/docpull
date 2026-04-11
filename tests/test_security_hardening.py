"""Security hardening regression tests."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

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
