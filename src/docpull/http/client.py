"""Async HTTP client with retry logic and rate limiting."""

from __future__ import annotations

import asyncio
import logging
import random
from types import TracebackType

import aiohttp

from .protocols import HttpResponse
from .rate_limiter import AdaptiveRateLimiter, PerHostRateLimiter

# Better encoding detection (charset-normalizer is an aiohttp dependency)
try:
    from charset_normalizer import from_bytes as detect_encoding

    CHARSET_NORMALIZER_AVAILABLE = True
except ImportError:
    CHARSET_NORMALIZER_AVAILABLE = False

logger = logging.getLogger(__name__)


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

    MAX_CONTENT_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_DOWNLOAD_TIME = 300  # 5 minutes

    # Status codes that warrant a retry
    RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

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

        if user_agent is None:
            user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (docpull/2.0)"
        self._user_agent = user_agent

        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> AsyncHttpClient:
        """Enter async context and create session."""
        connector = aiohttp.TCPConnector(
            limit=100,  # Total connection limit
            limit_per_host=10,  # Per-host connection limit
            ttl_dns_cache=300,  # DNS cache TTL
        )
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
        jitter: float = random.uniform(0, 1)
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
                async with (
                    self._rate_limiter.limit(url),
                    self._session.get(
                        url,
                        timeout=aiohttp.ClientTimeout(total=timeout_val),
                        headers=request_headers,
                        proxy=self._proxy,
                        allow_redirects=True,
                    ) as response,
                ):
                    # Check for retryable status codes
                    if response.status in self.RETRYABLE_STATUS_CODES:
                        # Record rate limit for adaptive rate limiter
                        if response.status == 429 and isinstance(self._rate_limiter, AdaptiveRateLimiter):
                            retry_after = response.headers.get("Retry-After")
                            retry_seconds = (
                                int(retry_after) if retry_after and retry_after.isdigit() else None
                            )
                            await self._rate_limiter.record_rate_limit(url, retry_seconds)

                        if attempt < self._max_retries:
                            delay = self._calculate_retry_delay(attempt)
                            logger.warning(
                                f"Got {response.status} for {url}, retrying in {delay:.1f}s "
                                f"(attempt {attempt + 1}/{self._max_retries + 1})"
                            )
                            await asyncio.sleep(delay)
                            continue
                        # Last attempt - let it raise
                        response.raise_for_status()

                    # Check Content-Length if available
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self._max_content_size:
                        raise ValueError(f"Content too large: {content_length} bytes")

                    # Read content with size limit
                    content = b""
                    async for chunk in response.content.iter_chunked(8192):
                        content += chunk
                        if len(content) > self._max_content_size:
                            raise ValueError(f"Content size limit exceeded: >{self._max_content_size} bytes")

                    content_type = response.headers.get("Content-Type", "")

                    # Record success for adaptive rate limiter
                    if isinstance(self._rate_limiter, AdaptiveRateLimiter):
                        await self._rate_limiter.record_success(url)

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

            except Exception:
                # Non-retryable error - re-raise immediately
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

        async with (
            self._rate_limiter.limit(url),
            self._session.head(
                url,
                timeout=aiohttp.ClientTimeout(total=timeout),
                headers=request_headers if request_headers else None,
                proxy=self._proxy,
                allow_redirects=True,
            ) as response,
        ):
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
