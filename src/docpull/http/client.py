"""Async HTTP client with retry logic and rate limiting."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
import socket
from types import TracebackType
from typing import cast
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

from ..security.download_policy import SafeDownloadPolicy
from ..security.url_validator import UrlValidator
from .protocols import HttpResponse
from .rate_limiter import AdaptiveRateLimiter, PerHostRateLimiter

# Better encoding detection (charset-normalizer is an aiohttp dependency)
try:
    from charset_normalizer import from_bytes as detect_encoding

    CHARSET_NORMALIZER_AVAILABLE = True
except ImportError:
    CHARSET_NORMALIZER_AVAILABLE = False

logger = logging.getLogger(__name__)

_NATIVE_PROXY_SCHEMES = frozenset({"http", "https"})
_SOCKS_PROXY_SCHEMES = frozenset({"socks4", "socks4a", "socks5", "socks5h"})


class _ValidatedResolver(AbstractResolver):
    """
    Resolver that pins connections to addresses approved by UrlValidator.

    Validation must happen at connect time, not only before the request is
    dispatched, otherwise DNS rebinding can swap in internal targets after the
    preflight check has passed.
    """

    def __init__(self, url_validator: UrlValidator):
        self._url_validator = url_validator

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[ResolveResult]:
        try:
            addresses = self._url_validator.resolve_allowed_addresses(host)
        except ValueError as err:
            raise OSError(str(err)) from err

        results: list[ResolveResult] = []
        for address in addresses:
            ip = ipaddress.ip_address(address)
            entry_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            if family not in {socket.AF_UNSPEC, entry_family}:
                continue

            results.append(
                ResolveResult(
                    hostname=host,
                    host=address,
                    port=port,
                    family=entry_family,
                    proto=socket.IPPROTO_TCP,
                    flags=socket.AI_NUMERICHOST,
                )
            )

        if not results:
            raise OSError(f"No allowed addresses available for {host}")

        return results

    async def close(self) -> None:
        """The resolver does not hold external resources."""
        return None


class AsyncHttpClient:
    """
    Async HTTP client with retry logic and rate limiting.

    Features:
    - Exponential backoff retry for transient failures
    - Per-host rate limiting via PerHostRateLimiter
    - Content size limits to prevent memory exhaustion
    - Intelligent encoding detection
    - Timeout controls

    Example:
        rate_limiter = PerHostRateLimiter(default_delay=0.5)
        client = AsyncHttpClient(rate_limiter=rate_limiter)

        async with client:
            response = await client.get("https://example.com")
            print(response.content.decode())
    """

    _CRLF_RE = re.compile(r"[\r\n\x00]")

    MAX_CONTENT_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_DOWNLOAD_TIME = 300  # 5 minutes
    MAX_REDIRECTS = 10

    # Status codes that warrant a retry
    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
    REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})
    SENSITIVE_HEADERS = frozenset({"authorization", "cookie", "proxy-authorization"})

    # Exceptions that warrant a retry
    RETRYABLE_EXCEPTIONS = (
        aiohttp.ClientError,
        asyncio.TimeoutError,
        ConnectionError,
    )

    def __init__(
        self,
        rate_limiter: PerHostRateLimiter,
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
        max_content_size: int = 50 * 1024 * 1024,
        user_agent: str | None = None,
        proxy: str | None = None,
        default_timeout: float = 30.0,
        auth_headers: dict[str, str] | None = None,
        url_validator: UrlValidator | None = None,
        allow_insecure_tls: bool = False,
        auth_scope_hosts: set[str] | None = None,
        require_pinned_dns: bool = False,
        download_policy: SafeDownloadPolicy | None = None,
    ) -> None:
        """
        Initialize the HTTP client.

        Args:
            rate_limiter: Per-host rate limiter for polite crawling
            max_retries: Maximum retry attempts for failed requests
            retry_base_delay: Base delay for exponential backoff (seconds)
            max_content_size: Maximum response size in bytes
            user_agent: Custom User-Agent string
            proxy: Proxy URL (http:// or socks5://)
            default_timeout: Default request timeout in seconds
            auth_headers: Authentication headers to include in all requests
            download_policy: Policy that rejects file-like responses before
                they can be buffered, converted, or saved
        """
        self._rate_limiter = rate_limiter
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._max_content_size = max_content_size
        self._proxy = self._validate_proxy_url(proxy)
        self._socks_proxy_url = self._proxy if self._is_socks_proxy_url(self._proxy) else None
        self._request_proxy = None if self._socks_proxy_url is not None else self._proxy
        self._default_timeout = default_timeout
        self._auth_headers = auth_headers or {}
        self._sensitive_headers = self.SENSITIVE_HEADERS | {name.lower() for name in self._auth_headers}
        self._url_validator = url_validator
        self._auth_scope_hosts = {host.lower() for host in auth_scope_hosts} if auth_scope_hosts else None
        self._download_policy = download_policy or SafeDownloadPolicy()

        if allow_insecure_tls:
            raise ValueError("Insecure TLS is not supported; certificate verification is always enforced")

        if require_pinned_dns and proxy is not None:
            raise ValueError(
                "require_pinned_dns is set but a proxy was configured. "
                "DNS pinning is delegated to the proxy in proxy mode, which "
                "weakens the SSRF posture below docpull's defaults. Either "
                "remove --proxy or drop --require-pinned-dns."
            )

        if user_agent is None:
            from .. import __version__

            user_agent = f"docpull/{__version__} (+https://github.com/raintree-technology/docpull)"
        self._user_agent = user_agent

        # Defense-in-depth: reject CRLF in headers at transport layer
        self._validate_header_value("User-Agent", self._user_agent)
        for name, value in self._auth_headers.items():
            self._validate_header_value(name, value)

        self._session: aiohttp.ClientSession | None = None

    @property
    def user_agent(self) -> str:
        """The User-Agent string this client sends on every request."""
        return self._user_agent

    @staticmethod
    def _validate_proxy_url(proxy: str | None) -> str | None:
        """Validate proxy schemes before handing the URL to aiohttp."""
        if proxy is None:
            return None

        scheme = urlparse(proxy).scheme.lower()
        if not scheme:
            raise ValueError("Proxy URL must include a scheme such as http://, https://, or socks5://")
        if scheme not in _NATIVE_PROXY_SCHEMES | _SOCKS_PROXY_SCHEMES:
            supported = ", ".join(sorted(_NATIVE_PROXY_SCHEMES | _SOCKS_PROXY_SCHEMES))
            raise ValueError(f"Unsupported proxy URL scheme '{scheme}'. Supported schemes: {supported}")
        return proxy

    @staticmethod
    def _is_socks_proxy_url(proxy: str | None) -> bool:
        """Return True when the proxy requires the optional aiohttp-socks connector."""
        return proxy is not None and urlparse(proxy).scheme.lower() in _SOCKS_PROXY_SCHEMES

    def _validate_url(self, url: str) -> None:
        """Re-validate each request URL, including redirect targets."""
        if self._url_validator is None:
            return

        result = self._url_validator.validate(url)
        if not result.is_valid:
            raise ValueError(f"URL validation failed for {url}: {result.rejection_reason}")

    @staticmethod
    def _validate_header_value(name: str, value: str) -> None:
        """Reject HTTP headers containing CR, LF, or null bytes."""
        if AsyncHttpClient._CRLF_RE.search(name) or AsyncHttpClient._CRLF_RE.search(value):
            raise ValueError(f"HTTP header injection blocked: header '{name}' contains CR, LF, or null")

    def _validate_request_headers(self, headers: dict[str, str]) -> None:
        """Validate caller-supplied headers before sending a request."""
        for name, value in headers.items():
            self._validate_header_value(name, value)
            if name.lower() == "accept-encoding" and value.lower().strip() != "identity":
                raise ValueError(
                    "Compressed response encodings are not requested; use Accept-Encoding: identity"
                )

    def _resolve_redirect_url(self, current_url: str, location: str) -> str:
        """Resolve a redirect target relative to the current URL."""
        redirect_url = urljoin(current_url, location)
        self._validate_url(redirect_url)
        return redirect_url

    def _headers_for_redirect(
        self,
        headers: dict[str, str],
        current_url: str,
        redirect_url: str,
    ) -> dict[str, str]:
        """
        Drop sensitive auth state when a redirect changes origin.

        Callers often attach bearer tokens or cookies scoped to a single docs
        host. Keeping them on cross-origin redirects can leak credentials.
        """
        if urlparse(current_url).netloc.lower() == urlparse(redirect_url).netloc.lower():
            return headers

        return self._without_sensitive_headers(headers)

    def _headers_for_url(self, headers: dict[str, str], target_url: str) -> dict[str, str]:
        """Strip scoped auth state before off-origin requests."""
        if self._auth_scope_hosts is None:
            return headers

        hostname = urlparse(target_url).hostname
        if hostname and hostname.lower() in self._auth_scope_hosts:
            return headers

        return self._without_sensitive_headers(headers)

    def _without_sensitive_headers(self, headers: dict[str, str]) -> dict[str, str]:
        return {key: value for key, value in headers.items() if key.lower() not in self._sensitive_headers}

    def _next_redirect(
        self,
        response: aiohttp.ClientResponse,
        current_url: str,
        current_headers: dict[str, str],
        redirect_count: int,
        original_url: str,
    ) -> tuple[str, dict[str, str], int] | None:
        """Re-validate and follow one redirect hop, shared by GET and HEAD.

        Returns the updated ``(url, headers, redirect_count)`` when ``response``
        is a redirect, or ``None`` when it is not. Raises ``ValueError`` once
        ``MAX_REDIRECTS`` is exceeded. Centralising this keeps GET and HEAD on
        identical redirect/SSRF re-validation.
        """
        location = response.headers.get("Location")
        if response.status in self.REDIRECT_STATUS_CODES and location:
            if redirect_count >= self.MAX_REDIRECTS:
                raise ValueError(f"Too many redirects while fetching {original_url}")

            redirect_url = self._resolve_redirect_url(current_url, location)
            new_headers = self._headers_for_url(
                self._headers_for_redirect(
                    current_headers,
                    current_url,
                    redirect_url,
                ),
                redirect_url,
            )
            return redirect_url, new_headers, redirect_count + 1
        return None

    def _build_connector(self, resolver: AbstractResolver | None) -> aiohttp.BaseConnector:
        """Build the right connector for direct, native-proxy, or SOCKS proxy mode."""
        if self._socks_proxy_url is not None:
            try:
                from aiohttp_socks import ProxyConnector  # type: ignore[import-not-found]
            except ImportError as err:
                raise ImportError(
                    "SOCKS proxy support requires the optional 'aiohttp-socks' package. "
                    "Install it with: pip install docpull[proxy]"
                ) from err

            return cast(
                aiohttp.BaseConnector,
                ProxyConnector.from_url(
                    self._socks_proxy_url,
                    limit=100,
                    limit_per_host=10,
                    ttl_dns_cache=300,
                ),
            )

        return aiohttp.TCPConnector(
            limit=100,
            limit_per_host=10,
            ttl_dns_cache=300,
            resolver=resolver,
        )

    async def __aenter__(self) -> AsyncHttpClient:
        """Enter async context and create session."""
        resolver: AbstractResolver | None = None
        if self._url_validator is not None and self._proxy is None:
            resolver = _ValidatedResolver(self._url_validator)
        elif self._proxy is not None and self._url_validator is not None:
            logger.warning(
                "Proxy mode: DNS-pinning resolver is not active. "
                "URL validation still runs pre-flight, but the proxy resolves DNS independently."
            )

        connector = self._build_connector(resolver)
        self._session = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.DummyCookieJar(),
            headers={
                "User-Agent": self._user_agent,
                "Accept-Encoding": "identity",
            },
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context and close session."""
        if self._session:
            await self._session.close()
            self._session = None

    def _calculate_retry_delay(self, attempt: int) -> float:
        """
        Calculate delay for exponential backoff with jitter.

        Args:
            attempt: Current attempt number (0-indexed)

        Returns:
            Delay in seconds
        """
        # Exponential backoff: base * (2 ^ attempt) + random jitter
        delay: float = self._retry_base_delay * (2**attempt)
        jitter: float = secrets.randbits(24) / float(1 << 24)
        return delay + jitter

    def _decode_content(self, content: bytes, content_type: str) -> str:
        """
        Decode content with intelligent encoding detection.

        Fallback chain:
        1. Content-Type header charset
        2. charset-normalizer detection
        3. UTF-8 with replacement

        Args:
            content: Raw bytes content
            content_type: Content-Type header value

        Returns:
            Decoded string
        """
        # First, try to get encoding from Content-Type header
        encoding = None
        if content_type:
            for part in content_type.split(";"):
                part = part.strip()
                if part.lower().startswith("charset="):
                    encoding = part.split("=", 1)[1].strip().strip("\"'")
                    break

        # Try declared encoding first
        if encoding:
            try:
                return content.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                logger.debug(f"Failed to decode with declared encoding: {encoding}")

        # Use charset-normalizer for better detection
        if CHARSET_NORMALIZER_AVAILABLE:
            try:
                result = detect_encoding(content)
                if result:
                    best_match = result.best()
                    if best_match:
                        logger.debug(f"Detected encoding: {best_match.encoding}")
                        return str(best_match)
            except Exception as e:
                logger.debug(f"Encoding detection failed: {e}")

        # Final fallback: UTF-8 with replacement
        return content.decode("utf-8", errors="replace")

    async def get(
        self,
        url: str,
        *,
        timeout: float | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """
        Perform an HTTP GET request with retry logic.

        Args:
            url: The URL to fetch
            timeout: Request timeout in seconds (uses default if None)
            headers: Optional additional headers

        Returns:
            HttpResponse with status, content, and headers

        Raises:
            aiohttp.ClientError: On network errors after retries exhausted
            ValueError: On content size exceeded
        """
        if self._session is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        timeout_val = timeout or self._default_timeout
        # Merge auth headers with request-specific headers
        request_headers = dict(self._auth_headers)
        if headers:
            request_headers.update(headers)
        self._validate_request_headers(request_headers)

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                current_url = url
                current_headers = self._headers_for_url(dict(request_headers), current_url)
                redirect_count = 0

                while True:
                    self._download_policy.validate_request_url(current_url)
                    self._validate_url(current_url)

                    async with (
                        self._rate_limiter.limit(current_url),
                        self._session.get(
                            current_url,
                            timeout=aiohttp.ClientTimeout(total=timeout_val),
                            headers=current_headers,
                            proxy=self._request_proxy,
                            allow_redirects=False,
                        ) as response,
                    ):
                        redirect = self._next_redirect(
                            response, current_url, current_headers, redirect_count, url
                        )
                        if redirect is not None:
                            current_url, current_headers, redirect_count = redirect
                            continue

                        content_type = response.headers.get("Content-Type", "")
                        response_headers = dict(response.headers)

                        if response.status in self.RETRYABLE_STATUS_CODES:
                            if response.status == 429 and isinstance(self._rate_limiter, AdaptiveRateLimiter):
                                retry_after = response.headers.get("Retry-After")
                                retry_seconds = (
                                    int(retry_after) if retry_after and retry_after.isdigit() else None
                                )
                                await self._rate_limiter.record_rate_limit(current_url, retry_seconds)

                            if attempt < self._max_retries:
                                delay = self._calculate_retry_delay(attempt)
                                logger.warning(
                                    f"Got {response.status} for {current_url}, retrying in {delay:.1f}s "
                                    f"(attempt {attempt + 1}/{self._max_retries + 1})"
                                )
                                await asyncio.sleep(delay)
                                break
                            response.raise_for_status()

                        if response.status == 304 or 400 <= response.status < 500:
                            return HttpResponse(
                                status_code=response.status,
                                content=b"",
                                content_type=content_type,
                                headers=response_headers,
                                url=str(response.url),
                            )

                        self._download_policy.validate_response_headers(
                            current_url,
                            status_code=response.status,
                            headers=response_headers,
                            content_type=content_type,
                        )

                        content_length = response.headers.get("Content-Length")
                        if content_length:
                            try:
                                parsed_content_length = int(content_length)
                            except ValueError as err:
                                raise ValueError(f"Invalid Content-Length header: {content_length}") from err
                            if parsed_content_length > self._max_content_size:
                                raise ValueError(f"Content too large: {content_length} bytes")

                        content_parts: list[bytes] = []
                        bytes_downloaded = 0
                        body_prefix = bytearray()
                        async for chunk in response.content.iter_chunked(8192):
                            if not chunk:
                                continue

                            if len(body_prefix) < self._download_policy.max_sniff_bytes:
                                remaining = self._download_policy.max_sniff_bytes - len(body_prefix)
                                body_prefix.extend(chunk[:remaining])
                                self._download_policy.validate_body_prefix(
                                    current_url,
                                    bytes(body_prefix),
                                )

                            bytes_downloaded += len(chunk)
                            if bytes_downloaded > self._max_content_size:
                                raise ValueError(
                                    f"Content size limit exceeded: >{self._max_content_size} bytes"
                                )
                            content_parts.append(chunk)

                        content = b"".join(content_parts)

                        if isinstance(self._rate_limiter, AdaptiveRateLimiter):
                            await self._rate_limiter.record_success(current_url)

                        return HttpResponse(
                            status_code=response.status,
                            content=content,
                            content_type=content_type,
                            headers=response_headers,
                            url=str(response.url),
                        )

            except self.RETRYABLE_EXCEPTIONS as e:
                last_error = e
                if attempt < self._max_retries:
                    delay = self._calculate_retry_delay(attempt)
                    logger.warning(
                        f"Error fetching {url}: {e}, retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{self._max_retries + 1})"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"HTTP fetch error for {url} after {self._max_retries + 1} attempts: {e}")
                    raise

        # Should not reach here, but just in case
        if last_error:
            raise last_error
        raise RuntimeError(f"Unexpected error fetching {url}")

    async def head(
        self,
        url: str,
        *,
        timeout: float = 10.0,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """
        Perform an HTTP HEAD request.

        Args:
            url: The URL to check
            timeout: Request timeout in seconds
            headers: Optional additional headers

        Returns:
            HttpResponse (content will be empty bytes)
        """
        if self._session is None:
            raise RuntimeError("Client not initialized. Use 'async with' context manager.")

        # Merge auth headers with request-specific headers
        request_headers = dict(self._auth_headers)
        if headers:
            request_headers.update(headers)
        self._validate_request_headers(request_headers)

        current_url = url
        current_headers = self._headers_for_url(dict(request_headers), current_url)
        redirect_count = 0

        while True:
            self._download_policy.validate_request_url(current_url)
            self._validate_url(current_url)

            async with (
                self._rate_limiter.limit(current_url),
                self._session.head(
                    current_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers=current_headers if current_headers else None,
                    proxy=self._request_proxy,
                    allow_redirects=False,
                ) as response,
            ):
                redirect = self._next_redirect(response, current_url, current_headers, redirect_count, url)
                if redirect is not None:
                    current_url, current_headers, redirect_count = redirect
                    continue

                response_headers = dict(response.headers)
                content_type = response.headers.get("Content-Type", "")
                self._download_policy.validate_response_headers(
                    current_url,
                    status_code=response.status,
                    headers=response_headers,
                    content_type=content_type,
                )

                return HttpResponse(
                    status_code=response.status,
                    content=b"",
                    content_type=content_type,
                    headers=response_headers,
                    url=str(response.url),
                )
