"""Security hardening regression tests."""

from __future__ import annotations

import asyncio
import socket
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from docpull.http.client import AsyncHttpClient, _ValidatedResolver
from docpull.http.rate_limiter import AdaptiveRateLimiter, PerHostRateLimiter
from docpull.security.robots import RobotsChecker, _RobotsResponse
from docpull.security.url_validator import UrlValidationResult, UrlValidator


class _NullAsyncContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _DummyRateLimiter:
    default_concurrent = 3

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


class _RecordingAdaptiveRateLimiter(AdaptiveRateLimiter):
    def __init__(self) -> None:
        super().__init__(default_delay=0.0, default_concurrent=3)
        self.rate_limit_calls: list[tuple[str, int | None]] = []
        self.success_calls: list[str] = []

    async def record_rate_limit(self, url: str, retry_after: int | None = None) -> None:
        self.rate_limit_calls.append((url, retry_after))

    async def record_success(self, url: str) -> None:
        self.success_calls.append(url)


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

    def test_validate_and_connect_path_share_screened_resolution(self) -> None:
        calls = {"n": 0}

        def resolver(hostname: str) -> list[str]:
            calls["n"] += 1
            return ["93.184.216.34"]

        validator = UrlValidator(resolver=resolver)

        assert validator.validate("https://docs.example.com").is_valid is True
        assert validator.resolve_allowed_addresses("docs.example.com") == ["93.184.216.34"]
        assert calls["n"] == 1

    def test_resolution_cache_expires_and_reresolves(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = {"n": 0}
        now = {"value": 100.0}

        def resolver(hostname: str) -> list[str]:
            calls["n"] += 1
            return [f"93.184.216.{calls['n']}"]

        validator = UrlValidator(resolver=resolver)
        monkeypatch.setattr("docpull.security.url_validator.time.monotonic", lambda: now["value"])

        assert validator.resolve_allowed_addresses("docs.example.com") == ["93.184.216.1"]
        now["value"] += UrlValidator._RESOLUTION_CACHE_TTL_SECONDS + 0.01
        assert validator.resolve_allowed_addresses("docs.example.com") == ["93.184.216.2"]
        assert calls["n"] == 2

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

    def test_blocks_dns_rebinding_suffixes_without_resolution(self) -> None:
        validator = UrlValidator(resolver=lambda hostname: ["93.184.216.34"])

        for host in ("docs.nip.io", "api.sslip.io", "box.xip.io", "router.lan"):
            result = validator.validate(f"https://{host}/")
            assert result.is_valid is False, host
            assert result.rejection_reason is not None
            assert "not allowed" in result.rejection_reason

    def test_allowed_domains_are_normalized(self) -> None:
        validator = UrlValidator(
            allowed_domains={"Docs.Example.com."},
            resolver=lambda hostname: ["93.184.216.34"],
        )

        assert validator.validate("https://docs.example.com/page").is_valid is True


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

    @pytest.mark.asyncio
    async def test_http_client_head_accepts_request_headers(self) -> None:
        client = AsyncHttpClient(rate_limiter=_DummyRateLimiter())
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    url="https://docs.example.com/page",
                )
            ]
        )

        await client.head("https://docs.example.com/page", headers={"X-Test": "1"})

        assert client._session is not None
        _, kwargs = client._session.calls[0]
        assert kwargs["headers"]["X-Test"] == "1"

    @pytest.mark.asyncio
    async def test_http_client_uses_separate_connect_and_read_timeouts(self) -> None:
        client = AsyncHttpClient(
            rate_limiter=_DummyRateLimiter(),
            default_timeout=7.0,
            connect_timeout=2.0,
        )
        client._session = _FakeSession(
            [
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    chunks=[b"ok"],
                    url="https://docs.example.com/page",
                )
            ]
        )

        await client.get("https://docs.example.com/page")

        assert client._session is not None
        _, kwargs = client._session.calls[0]
        timeout = kwargs["timeout"]
        assert timeout.total == 9.0
        assert timeout.connect == 2.0
        assert timeout.sock_read == 7.0

    @pytest.mark.asyncio
    async def test_http_client_rejects_crlf_in_request_headers(self) -> None:
        client = AsyncHttpClient(rate_limiter=_DummyRateLimiter())
        client._session = _FakeSession([])

        with pytest.raises(ValueError, match="header injection"):
            await client.get("https://docs.example.com/page", headers={"X-Test": "ok\r\nbad: yes"})

    @pytest.mark.asyncio
    async def test_http_head_retries_and_tracks_adaptive_backoff(self) -> None:
        limiter = _RecordingAdaptiveRateLimiter()
        client = AsyncHttpClient(rate_limiter=limiter, max_retries=1, retry_base_delay=0.0)
        client._session = _FakeSession(
            [
                _FakeResponse(
                    429,
                    headers={"Retry-After": "120", "Content-Type": "text/html"},
                    url="https://docs.example.com/page",
                ),
                _FakeResponse(
                    200,
                    headers={"Content-Type": "text/html"},
                    url="https://docs.example.com/page",
                ),
            ]
        )

        response = await client.head("https://docs.example.com/page")

        assert response.status_code == 200
        assert client._session is not None
        assert len(client._session.calls) == 2
        assert limiter.rate_limit_calls == [("https://docs.example.com/page", 120)]
        assert limiter.success_calls == ["https://docs.example.com/page"]

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

    def test_robots_checker_uses_validator_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = RobotsChecker()
        monkeypatch.setattr(
            checker._url_validator,
            "validate",
            lambda url: UrlValidationResult.invalid("blocked for test"),
        )

        assert checker.is_allowed("https://public.example/docs") is False

    def test_robots_checker_allows_when_robots_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = RobotsChecker()

        def fake_fetch(url: str) -> _RobotsResponse:
            return _RobotsResponse(status_code=404, headers={}, text="")

        monkeypatch.setattr(checker, "_validate_url", lambda url: True)
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

    def test_robots_cache_key_is_canonicalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        checker = RobotsChecker()
        calls: list[tuple[str, str]] = []

        def fake_fetch(domain: str, robots_url: str):
            calls.append((domain, robots_url))
            return SimpleNamespace(parser=None, status="missing")

        monkeypatch.setattr(checker, "_fetch_robots", fake_fetch)

        assert checker.is_allowed("HTTPS://Example.com/docs") is True
        assert checker.is_allowed("https://example.com:443/other") is True

        assert calls == [("example.com", "https://example.com/robots.txt")]

    def test_robots_url_preserves_ipv6_brackets(self) -> None:
        checker = RobotsChecker()

        assert (
            checker._get_robots_url("https://[2606:2800:220:1:248:1893:25c8:1946]:8443/docs")
            == "https://[2606:2800:220:1:248:1893:25c8:1946]:8443/robots.txt"
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

    def test_auth_token_env_expansion_rejects_crlf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from docpull.models.config import AuthConfig, AuthType

        monkeypatch.setenv("DOCPULL_TEST_TOKEN", "tok\r\nX-Evil: true")

        with pytest.raises(ValueError, match="token must not contain CR, LF, or null"):
            AuthConfig(type=AuthType.BEARER, token="$DOCPULL_TEST_TOKEN")

    def test_auth_cookie_env_expansion_rejects_crlf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from docpull.models.config import AuthConfig, AuthType

        monkeypatch.setenv("DOCPULL_TEST_COOKIE", "session=abc\r\nX-Evil: true")

        with pytest.raises(ValueError, match="cookie must not contain CR, LF, or null"):
            AuthConfig(type=AuthType.COOKIE, cookie="$DOCPULL_TEST_COOKIE")

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


class TestRateLimiterIsolation:
    @pytest.mark.asyncio
    async def test_waiting_host_does_not_block_other_hosts(self) -> None:
        limiter = PerHostRateLimiter(default_delay=0.2, default_concurrent=1)
        limiter._last_request["slow.example"] = time.monotonic()

        entered_fast = asyncio.Event()

        async def slow_request() -> None:
            async with limiter.limit("https://slow.example/page"):
                await asyncio.sleep(0.01)

        async def fast_request() -> None:
            async with limiter.limit("https://fast.example/page"):
                entered_fast.set()

        slow_task = asyncio.create_task(slow_request())
        await asyncio.sleep(0)
        fast_task = asyncio.create_task(fast_request())

        await asyncio.wait_for(entered_fast.wait(), timeout=0.05)
        await asyncio.gather(slow_task, fast_task)

    def test_default_ports_and_case_share_one_host_key(self) -> None:
        limiter = PerHostRateLimiter(default_delay=0.1, default_concurrent=1)

        assert limiter._get_host("HTTPS://Example.com:443/path") == "example.com"
        assert limiter._get_host("https://example.com./path") == "example.com"

    def test_invalid_rate_limiter_config_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="delay must be >= 0"):
            PerHostRateLimiter(default_delay=-0.1)

        with pytest.raises(ValueError, match="concurrency must be >= 1"):
            PerHostRateLimiter(default_concurrent=0)
