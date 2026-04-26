"""Async HTTP client with retry logic and rate limiting."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import secrets
import socket
from types import TracebackType
from urllib.parse import urljoin, urlparse

import aiohttp
from aiohttp.abc import AbstractResolver

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
        family: int = socket.AF_UNSPEC,
    ) -> list[dict[str, object]]:
        try:
            addresses = self._url_validator.resolve_allowed_addresses(host)
        except ValueError as err:
            raise OSError(str(err)) from err

        results: list[dict[str, object]] = []
        for address in addresses:
            ip = ipaddress.ip_address(address)
            entry_family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
            if family not in {socket.AF_UNSPEC, entry_family}:
                continue

            results.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": port,
                    "family": entry_family,
                    "proto": socket.IPPROTO_TCP,
                    "flags": socket.AI_NUMERICHOST,
                }
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
        """
        self._rate_limiter = rate_limiter
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._max_content_size = max_content_size
        self._proxy = proxy
        self._default_timeout = default_timeout
        self._auth_headers = auth_headers or {}
        self._url_validator = url_validator
        self._auth_scope_hosts = {host.lower() for host in auth_scope_hosts} if auth_scope_hosts else None

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

        return {key: value for key, value in headers.items() if key.lower() not in self.SENSITIVE_HEADERS}

    def _headers_for_url(self, headers: dict[str, str], target_url: str) -> dict[str, str]:
        """Strip scoped auth state before off-origin requests."""
        if self._auth_scope_hosts is None:
            return headers

        hostname = urlparse(target_url).hostname
        if hostname and hostname.lower() in self._auth_scope_hosts:
            return headers

        return {key: value for key, value in headers.items() if key.lower() not in self.SENSITIVE_HEADERS}

    async def __aenter__(self) -> AsyncHttpClient:
        """Enter async context and create session."""
        connector_kwargs: dict[str, object] = {
            "limit": 100,  # Total connection limit
            "limit_per_host": 10,  # Per-host connection limit
            "ttl_dns_cache": 300,  # DNS cache TTL
        }
        if self._url_validator is not None and self._proxy is None:
            connector_kwargs["resolver"] = _ValidatedResolver(self._url_validator)
        elif self._proxy is not None and self._url_validator is not None:
            logger.warning(
                "Proxy mode: DNS-pinning resolver is not active. "
                "URL validation still runs pre-flight, but the proxy resolves DNS independently."
            )

        connector = aiohttp.TCPConnector(**connector_kwargs)
        self._session = aiohttp.ClientSession(
            connector=connector,
            headers={"User-Agent": self._user_agent},
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

        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                current_url = url
                current_headers = self._headers_for_url(dict(request_headers), current_url)
                redirect_count = 0

                while True:
                    self._validate_url(current_url)

                    async with (
                        self._rate_limiter.limit(current_url),
                        self._session.get(
                            current_url,
                            timeout=aiohttp.ClientTimeout(total=timeout_val),
                            headers=current_headers,
                            proxy=self._proxy,
                            allow_redirects=False,
                        ) as response,
                    ):
                        location = response.headers.get("Location")
                        if response.status in self.REDIRECT_STATUS_CODES and location:
                            if redirect_count >= self.MAX_REDIRECTS:
                                raise ValueError(f"Too many redirects while fetching {url}")

                            redirect_url = self._resolve_redirect_url(current_url, location)
                            current_headers = self._headers_for_url(
                                self._headers_for_redirect(
                                    current_headers,
                                    current_url,
                                    redirect_url,
                                ),
                                redirect_url,
                            )
                            current_url = redirect_url
                            redirect_count += 1
                            continue

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

                        content_length = response.headers.get("Content-Length")
                        if content_length and int(content_length) > self._max_content_size:
                            raise ValueError(f"Content too large: {content_length} bytes")

                        content = b""
                        async for chunk in response.content.iter_chunked(8192):
                            content += chunk
                            if len(content) > self._max_content_size:
                                raise ValueError(
                                    f"Content size limit exceeded: >{self._max_content_size} bytes"
                                )

                        content_type = response.headers.get("Content-Type", "")

                        if isinstance(self._rate_limiter, AdaptiveRateLimiter):
                            await self._rate_limiter.record_success(current_url)

                        return HttpResponse(
                            status_code=response.status,
                            content=content,
                            content_type=content_type,
                            headers=dict(response.headers),
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

        current_url = url
        current_headers = self._headers_for_url(dict(request_headers), current_url)
        redirect_count = 0

        while True:
            self._validate_url(current_url)

            async with (
                self._rate_limiter.limit(current_url),
                self._session.head(
                    current_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                    headers=current_headers if current_headers else None,
                    proxy=self._proxy,
                    allow_redirects=False,
                ) as response,
            ):
                location = response.headers.get("Location")
                if response.status in self.REDIRECT_STATUS_CODES and location:
                    if redirect_count >= self.MAX_REDIRECTS:
                        raise ValueError(f"Too many redirects while fetching {url}")

                    redirect_url = self._resolve_redirect_url(current_url, location)
                    current_headers = self._headers_for_url(
                        self._headers_for_redirect(
                            current_headers,
                            current_url,
                            redirect_url,
                        ),
                        redirect_url,
                    )
                    current_url = redirect_url
                    redirect_count += 1
                    continue

                return HttpResponse(
                    status_code=response.status,
                    content=b"",
                    content_type=response.headers.get("Content-Type", ""),
                    headers=dict(response.headers),
                    url=str(response.url),
                )

    def decode_content(self, response: HttpResponse) -> str:
        """
        Decode response content to string.

        Convenience method that uses intelligent encoding detection.

        Args:
            response: HttpResponse to decode

        Returns:
            Decoded string content
        """
        return self._decode_content(response.content, response.content_type)
