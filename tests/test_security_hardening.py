"""Security hardening regression tests."""

from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import aiohttp
import pytest
from pydantic import ValidationError

from docpull.http.client import AsyncHttpClient, _ValidatedResolver
from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.save import SaveStep
from docpull.security.robots import RobotsChecker, _RobotsResponse
from docpull.security.url_validator import UrlValidationResult, UrlValidator


class _NullAsyncContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        return None


class _DummyRateLimiter:
    def limit(self, url: str) -> _NullAsyncContext:
        return _NullAsyncContext()


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.iterated = False

    async def iter_chunked(self, size: int):
        self.iterated = True
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

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
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


class _FailingSession:
    def __init__(self, error: BaseException) -> None:
        self._error = error

    def get(self, url: str, **kwargs: object) -> _FakeRequestContext:
        raise self._error


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

    def test_resolve_allowed_addresses_resolves_exactly_once(self) -> None:
        """DNS-rebinding TOCTOU regression.

        resolve_allowed_addresses() must resolve a hostname once and return the
        addresses it screened. A second resolution would let a hostile resolver
        serve a public IP to the check and an internal IP to the connect path.
        """
        answers = [["1.2.3.4"], ["169.254.169.254"]]
        calls = {"n": 0}

        def rebinding_resolver(hostname: str) -> list[str]:
            index = calls["n"]
            calls["n"] += 1
            return answers[min(index, len(answers) - 1)]

        validator = UrlValidator(resolver=rebinding_resolver)

        addresses = validator.resolve_allowed_addresses("rebind.example.com")

        assert calls["n"] == 1
        assert addresses == ["1.2.3.4"]

    def test_resolve_allowed_addresses_rejects_blocked_resolution(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: ["169.254.169.254"])

        with pytest.raises(ValueError, match="blocked address '169.254.169.254'"):
            validator.resolve_allowed_addresses("rebind.example.com")

    def test_resolve_allowed_addresses_rejects_empty_resolution(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: [])

        with pytest.raises(ValueError, match="did not resolve"):
            validator.resolve_allowed_addresses("void.example.com")

    def test_blocks_cgnat_shared_address_space(self) -> None:
        validator = UrlValidator()

        for host in ("100.64.0.1", "100.127.255.254", "::ffff:100.64.0.1"):
            result = validator.validate_hostname(host)
            assert result.is_valid is False, host
            assert "Carrier-grade NAT" in (result.rejection_reason or "")

    def test_blocks_ipv4_mapped_loopback(self) -> None:
        validator = UrlValidator()

        result = validator.validate_hostname("::ffff:127.0.0.1")

        assert result.is_valid is False

    def test_trailing_dot_does_not_bypass_localhost(self) -> None:
        validator = UrlValidator()

        assert validator.validate("https://localhost./admin").is_valid is False
        assert validator.validate_hostname("service.internal.").is_valid is False


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
    async def test_redirect_to_ipv4_mapped_private_ipv6_blocked(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            url_validator=UrlValidator(),
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    302,
                    headers={"Location": "https://[::ffff:127.0.0.1]/admin"},
                    url="https://public.example/start",
                )
            ]
        )

        with pytest.raises(ValueError, match="URL validation failed"):
            await client.get("https://public.example/start")

    @pytest.mark.asyncio
    async def test_http_client_strips_auth_headers_for_off_scope_requests(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            auth_headers={
                "Authorization": "Bearer top-secret",
                "Cookie": "session=abc123",
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

        await client.get("https://evil.example/collect", headers={"X-Trace": "keep-me"})

        assert client._session is not None
        _, kwargs = client._session.calls[0]
        assert kwargs["headers"] == {"X-Trace": "keep-me"}

    @pytest.mark.asyncio
    async def test_http_client_strips_custom_auth_headers_for_off_scope_requests(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            auth_headers={"X-Api-Key": "top-secret"},
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
        assert kwargs["headers"] == {}

    @pytest.mark.asyncio
    async def test_http_client_strips_custom_auth_headers_on_cross_origin_redirect(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            auth_headers={"X-Api-Key": "top-secret"},
            auth_scope_hosts={"docs.example.com"},
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    302,
                    headers={"Location": "https://evil.example/collect"},
                    url="https://docs.example.com/start",
                ),
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    chunks=[b"ok"],
                    url="https://evil.example/collect",
                ),
            ]
        )

        await client.get("https://docs.example.com/start")

        assert client._session is not None
        assert client._session.calls[0][1]["headers"] == {"X-Api-Key": "top-secret"}
        assert client._session.calls[1][1]["headers"] == {}

    def test_http_client_rejects_insecure_tls_override(self) -> None:
        with pytest.raises(ValueError, match="Insecure TLS is not supported"):
            AsyncHttpClient(
                rate_limiter=_DummyRateLimiter(),
                allow_insecure_tls=True,
            )

    def test_robots_checker_rejects_insecure_tls_override(self) -> None:
        with pytest.raises(ValueError, match="Insecure TLS is not supported"):
            RobotsChecker(allow_insecure_tls=True)

    @pytest.mark.asyncio
    async def test_retry_status_logging_redacts_url_credentials(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("docpull.http.client.asyncio.sleep", no_sleep)
        client = AsyncHttpClient(rate_limiter=_DummyRateLimiter(), max_retries=1)
        client._session = _FakeSession(
            [
                _FakeResponse(500, url="https://user:password@example.com/path?token=secret"),
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/plain"},
                    chunks=[b"ok"],
                    url="https://user:password@example.com/path?token=secret",
                ),
            ]
        )

        with caplog.at_level(logging.WARNING, logger="docpull.http.client"):
            await client.get("https://user:password@example.com/path?token=secret")

        assert "[redacted]@example.com" in caplog.text
        assert "token=%5Bredacted%5D" in caplog.text
        assert "password" not in caplog.text
        assert "secret" not in caplog.text

    @pytest.mark.asyncio
    async def test_retry_exception_logging_does_not_log_exception_secrets(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def no_sleep(_delay: float) -> None:
            return None

        monkeypatch.setattr("docpull.http.client.asyncio.sleep", no_sleep)
        client = AsyncHttpClient(rate_limiter=_DummyRateLimiter(), max_retries=1)
        client._session = _FailingSession(
            aiohttp.ClientError("could not fetch https://user:password@example.com/path?token=secret")
        )

        with (
            caplog.at_level(logging.WARNING, logger="docpull.http.client"),
            pytest.raises(aiohttp.ClientError),
        ):
            await client.get("https://user:password@example.com/path?token=secret")

        assert "[redacted]@example.com" in caplog.text
        assert "ClientError" in caplog.text
        assert "password" not in caplog.text
        assert "secret" not in caplog.text

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

    def test_robots_checker_caps_response_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A hostile site cannot stream an unbounded robots.txt into memory."""
        import docpull.security.robots as robots_mod

        class _HugeResponse:
            status = 200

            def read(self, amt: int | None = None) -> bytes:
                payload = b"x" * (RobotsChecker.MAX_ROBOTS_SIZE + 1024)
                return payload[:amt] if amt is not None else payload

            def getheaders(self) -> list[tuple[str, str]]:
                return []

            def getheader(self, name: str, default: str | None = None) -> str | None:
                return default

        class _FakeConn:
            def __init__(self, *args: object, **kwargs: object) -> None:
                pass

            def request(self, *args: object, **kwargs: object) -> None:
                pass

            def getresponse(self) -> _HugeResponse:
                return _HugeResponse()

            def close(self) -> None:
                pass

        monkeypatch.setattr(robots_mod, "_PinnedHTTPSConnection", _FakeConn)
        checker = RobotsChecker()
        monkeypatch.setattr(checker, "_resolve_addresses", lambda hostname: ["1.2.3.4"])

        with pytest.raises(ValueError, match="exceeds maximum size"):
            checker._fetch_url("https://evil.example.com/robots.txt")

    @pytest.mark.asyncio
    async def test_http_client_caps_streamed_body_size(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_content_size=4,
            max_retries=0,
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    chunks=[b"abc", b"def"],
                    url="https://public.example/page",
                )
            ]
        )

        with pytest.raises(ValueError, match="Content size limit exceeded"):
            await client.get("https://public.example/page")

    @pytest.mark.asyncio
    async def test_http_client_rejects_dangerous_extension_before_request(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        session = _FakeSession([])
        client._session = session

        with pytest.raises(ValueError, match="Disallowed download URL extension '.exe'"):
            await client.get("https://public.example/downloads/update.exe")

        assert session.calls == []

    @pytest.mark.asyncio
    async def test_http_client_rejects_nested_encoded_dangerous_extension(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        session = _FakeSession([])
        client._session = session

        with pytest.raises(ValueError, match="Disallowed download URL extension '.exe'"):
            await client.get("https://public.example/downloads/update%252eexe")

        assert session.calls == []

    @pytest.mark.asyncio
    async def test_http_client_rejects_dangerous_filename_star_before_body(self) -> None:
        response = _FakeResponse(
            200,
            headers={
                "Content-Type": "text/plain",
                "Content-Disposition": "inline; filename*=UTF-8''safe%252eexe",
            },
            chunks=[b"plain text"],
            url="https://public.example/notes",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="disallowed extension '.exe'"):
            await client.get("https://public.example/notes")

        assert response.content.iterated is False

    @pytest.mark.asyncio
    async def test_http_client_rejects_disallowed_content_type_before_body(self) -> None:
        response = _FakeResponse(
            200,
            headers={"Content-Type": "application/x-msdownload"},
            chunks=[b"MZevil"],
            url="https://public.example/download",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="Disallowed content type 'application/x-msdownload'"):
            await client.get("https://public.example/download")

        assert response.content.iterated is False

    @pytest.mark.asyncio
    async def test_http_client_rejects_attachment_before_body(self) -> None:
        response = _FakeResponse(
            200,
            headers={
                "Content-Type": "text/plain",
                "Content-Disposition": "attachment; filename=notes.txt",
            },
            chunks=[b"plain text"],
            url="https://public.example/notes",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="Refusing attachment response"):
            await client.get("https://public.example/notes")

        assert response.content.iterated is False

    @pytest.mark.asyncio
    async def test_http_client_rejects_spoofed_executable_magic(self) -> None:
        response = _FakeResponse(
            200,
            headers={"Content-Type": "text/plain"},
            chunks=[b"M", b"Znot actually text"],
            url="https://public.example/readme",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="Disallowed Windows executable body"):
            await client.get("https://public.example/readme")

        assert response.content.iterated is True

    @pytest.mark.asyncio
    async def test_http_client_rejects_spoofed_image_magic(self) -> None:
        response = _FakeResponse(
            200,
            headers={"Content-Type": "text/plain"},
            chunks=[b"\x89PNG\r\n\x1a\nnot docs"],
            url="https://public.example/readme",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="Disallowed PNG image body"):
            await client.get("https://public.example/readme")

    @pytest.mark.asyncio
    async def test_http_client_rejects_spoofed_svg_body(self) -> None:
        response = _FakeResponse(
            200,
            headers={"Content-Type": "application/xml"},
            chunks=[b"<?xml version='1.0'?><svg><script>alert(1)</script></svg>"],
            url="https://public.example/readme",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="Disallowed SVG document body"):
            await client.get("https://public.example/readme")

    @pytest.mark.asyncio
    async def test_http_client_rejects_binary_looking_text_body(self) -> None:
        response = _FakeResponse(
            200,
            headers={"Content-Type": "text/plain"},
            chunks=[b"docs\x00\x01\x02" * 20],
            url="https://public.example/readme",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        with pytest.raises(ValueError, match="Disallowed binary-looking body"):
            await client.get("https://public.example/readme")

    @pytest.mark.asyncio
    async def test_http_client_allows_safe_markdown_body(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/markdown; charset=utf-8"},
                    chunks=[b"# Safe docs\n\nBody"],
                    url="https://public.example/readme",
                )
            ]
        )

        response = await client.get("https://public.example/readme")

        assert response.content == b"# Safe docs\n\nBody"
        assert response.content_type == "text/markdown; charset=utf-8"

    @pytest.mark.asyncio
    async def test_http_client_does_not_buffer_4xx_body(self) -> None:
        response = _FakeResponse(
            404,
            headers={"Content-Type": "application/x-msdownload"},
            chunks=[b"MZevil"],
            url="https://public.example/missing",
        )
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([response])

        result = await client.get("https://public.example/missing")

        assert result.status_code == 404
        assert result.content == b""
        assert response.content.iterated is False

    @pytest.mark.asyncio
    async def test_http_client_rejects_request_header_injection(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([])

        with pytest.raises(ValueError, match="header injection"):
            await client.get("https://public.example/readme", headers={"X-Test": "ok\nbad"})

    @pytest.mark.asyncio
    async def test_http_client_rejects_compressed_accept_encoding_override(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            max_retries=0,
        )
        client._session = _FakeSession([])

        with pytest.raises(ValueError, match="Accept-Encoding: identity"):
            await client.get("https://public.example/readme", headers={"Accept-Encoding": "gzip"})

    @pytest.mark.asyncio
    async def test_http_client_session_does_not_store_cookies(self) -> None:
        client = AsyncHttpClient(rate_limiter=_DummyRateLimiter())

        async with client:
            assert client._session is not None
            assert isinstance(client._session.cookie_jar, aiohttp.DummyCookieJar)
            assert client._session.headers["Accept-Encoding"] == "identity"

    @pytest.mark.asyncio
    async def test_save_step_rejects_symlink_escape(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        output = tmp_path / "out"
        output.mkdir()
        link = output / "linked"
        link.symlink_to(outside, target_is_directory=True)

        step = SaveStep(base_output_dir=output)
        ctx = PageContext(
            url="https://example.com/page",
            output_path=link / "page.md",
            markdown="# Page\n\nBody",
        )

        with pytest.raises(ValueError, match="outside base directory"):
            await step.execute(ctx)


class TestProxyHandling:
    def test_socks_proxy_without_extra_has_actionable_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "aiohttp_socks", None)
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            proxy="socks5://127.0.0.1:1080",
        )

        with pytest.raises(ImportError, match=r"docpull\[proxy\]"):
            client._build_connector(None)

    def test_socks_proxy_uses_optional_connector(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sentinel = object()
        created: dict[str, object] = {}

        class FakeProxyConnector:
            @classmethod
            def from_url(cls, url: str, **kwargs: object) -> object:
                created["url"] = url
                created["kwargs"] = kwargs
                return sentinel

        fake_module = ModuleType("aiohttp_socks")
        fake_module.ProxyConnector = FakeProxyConnector  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "aiohttp_socks", fake_module)

        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            proxy="socks5://user:pass@127.0.0.1:1080",
        )

        connector = client._build_connector(None)

        assert connector is sentinel
        assert client._request_proxy is None
        assert created["url"] == "socks5://user:pass@127.0.0.1:1080"
        assert created["kwargs"] == {"limit": 100, "limit_per_host": 10, "ttl_dns_cache": 300}

    @pytest.mark.asyncio
    async def test_native_http_proxy_is_passed_per_request(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            proxy="http://corp.proxy:8080",
            max_retries=0,
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    chunks=[b"ok"],
                    url="https://public.example/page",
                )
            ]
        )

        await client.get("https://public.example/page")

        assert client._session is not None
        _, kwargs = client._session.calls[0]
        assert kwargs["proxy"] == "http://corp.proxy:8080"

    @pytest.mark.asyncio
    async def test_socks_proxy_is_not_passed_per_request(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            proxy="socks5://127.0.0.1:1080",
            max_retries=0,
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    chunks=[b"ok"],
                    url="https://public.example/page",
                )
            ]
        )

        await client.get("https://public.example/page")

        assert client._session is not None
        _, kwargs = client._session.calls[0]
        assert kwargs["proxy"] is None

    def test_proxy_url_requires_supported_scheme(self) -> None:
        with pytest.raises(ValueError, match="Unsupported proxy URL scheme 'ftp'"):
            AsyncHttpClient(
                rate_limiter=_DummyRateLimiter(),
                proxy="ftp://corp.proxy:21",
            )


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
