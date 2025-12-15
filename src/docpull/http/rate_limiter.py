"""Per-host rate limiting for polite crawling."""

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class PerHostRateLimiter:
    """
    Rate limiter that enforces per-host concurrency and delay limits.

    This ensures polite crawling by:
    1. Limiting concurrent requests to each host
    2. Enforcing minimum delay between requests to the same host
    3. Using monotonic time for accurate delay calculation

    Example:
        limiter = PerHostRateLimiter(default_delay=0.5, default_concurrent=3)

        async with limiter.limit("https://example.com/page1"):
            await fetch_page(...)

        async with limiter.limit("https://example.com/page2"):
            # Will wait at least 0.5s after page1 completes
            await fetch_page(...)
    """

    def __init__(
        self,
        default_delay: float = 0.5,
        default_concurrent: int = 3,
        host_configs: Optional[dict[str, dict]] = None,
    ):
        """
        Initialize the rate limiter.

        Args:
            default_delay: Minimum seconds between requests to same host
            default_concurrent: Maximum concurrent requests per host
            host_configs: Optional per-host overrides, e.g.:
                {"api.example.com": {"delay": 1.0, "concurrent": 2}}
        """
        self.default_delay = default_delay
        self.default_concurrent = default_concurrent
        self.host_configs = host_configs or {}

        # Per-host state
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._last_request: dict[str, float] = {}
        self._lock = asyncio.Lock()

    def _get_host(self, url: str) -> str:
        """Extract host from URL."""
        return urlparse(url).netloc

    def _get_config(self, host: str) -> tuple[float, int]:
        """Get delay and concurrency for a specific host."""
        if host in self.host_configs:
            cfg = self.host_configs[host]
            return (
                cfg.get("delay", self.default_delay),
                cfg.get("concurrent", self.default_concurrent),
            )
        return self.default_delay, self.default_concurrent

    async def _get_semaphore(self, host: str) -> asyncio.Semaphore:
        """Get or create semaphore for a host."""
        async with self._lock:
            if host not in self._semaphores:
                _, concurrent = self._get_config(host)
                self._semaphores[host] = asyncio.Semaphore(concurrent)
            return self._semaphores[host]

    @asynccontextmanager
    async def limit(self, url: str) -> AsyncIterator[None]:
        """
        Async context manager for rate-limited requests.

        Acquires the host's semaphore slot and enforces delay.

        Args:
            url: The URL being requested

        Yields:
            None - perform your request in the context

        Example:
            async with limiter.limit(url):
                response = await session.get(url)
        """
        host = self._get_host(url)
        delay, _ = self._get_config(host)

        # Get or create semaphore
        sem = await self._get_semaphore(host)

        # Acquire semaphore slot
        async with sem:
            # Enforce per-host delay
            async with self._lock:
                now = time.monotonic()
                last = self._last_request.get(host, 0.0)
                wait_time = max(0.0, delay - (now - last))

                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                self._last_request[host] = time.monotonic()

            yield

    def update_host_config(
        self,
        host: str,
        delay: Optional[float] = None,
        concurrent: Optional[int] = None,
    ) -> None:
        """
        Update configuration for a specific host.

        Useful for applying robots.txt Crawl-delay directives.

        Args:
            host: The host to configure
            delay: New delay value (or None to keep current)
            concurrent: New concurrency limit (or None to keep current)

        Note:
            Changes to concurrent won't affect existing semaphores.
            For safety, set host configs before starting requests.
        """
        if host not in self.host_configs:
            self.host_configs[host] = {}

        if delay is not None:
            self.host_configs[host]["delay"] = delay
        if concurrent is not None:
            self.host_configs[host]["concurrent"] = concurrent

    def get_stats(self) -> dict:
        """Get rate limiter statistics."""
        return {
            "hosts_tracked": len(self._semaphores),
            "custom_configs": len(self.host_configs),
        }


class AdaptiveRateLimiter(PerHostRateLimiter):
    """
    Rate limiter that adapts based on server responses.

    Automatically backs off when receiving 429 (Too Many Requests) responses
    and gradually speeds up after consecutive successful requests.

    Thread-safe: Uses asyncio.Lock to protect shared state modifications.

    Example:
        limiter = AdaptiveRateLimiter(default_delay=0.5)

        # On 429 response:
        limiter.record_rate_limit(url, retry_after=60)

        # On success:
        limiter.record_success(url)
    """

    def __init__(
        self,
        default_delay: float = 0.5,
        default_concurrent: int = 3,
        host_configs: Optional[dict[str, dict]] = None,
        min_delay: float = 0.1,
        max_delay: float = 60.0,
        backoff_factor: float = 2.0,
        success_threshold: int = 10,
    ):
        """
        Initialize the adaptive rate limiter.

        Args:
            default_delay: Minimum seconds between requests to same host
            default_concurrent: Maximum concurrent requests per host
            host_configs: Optional per-host overrides
            min_delay: Minimum delay (won't speed up below this)
            max_delay: Maximum delay (won't slow down above this)
            backoff_factor: Multiplier for delay on rate limit
            success_threshold: Successful requests before speeding up
        """
        super().__init__(default_delay, default_concurrent, host_configs)
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._backoff_factor = backoff_factor
        self._success_threshold = success_threshold

        # Adaptive state (protected by _adaptive_lock)
        self._success_counts: dict[str, int] = {}
        self._current_delays: dict[str, float] = {}
        self._adaptive_lock = asyncio.Lock()

    async def record_rate_limit(self, url: str, retry_after: Optional[int] = None) -> None:
        """
        Record a 429 response and increase delay for the host.

        Args:
            url: The URL that received a 429
            retry_after: Optional Retry-After header value in seconds
        """
        host = self._get_host(url)

        async with self._adaptive_lock:
            current = self._current_delays.get(host, self.default_delay)

            if retry_after is not None and retry_after > 0:
                # Use Retry-After if provided
                new_delay = min(float(retry_after), self._max_delay)
            else:
                # Exponential backoff
                new_delay = min(current * self._backoff_factor, self._max_delay)

            self._current_delays[host] = new_delay
            self._success_counts[host] = 0
            self.update_host_config(host, delay=new_delay)

        logger.info(f"Rate limited by {host}, increasing delay to {new_delay:.1f}s")

    async def record_success(self, url: str) -> None:
        """
        Record a successful request. May decrease delay after threshold.

        Args:
            url: The URL that succeeded
        """
        host = self._get_host(url)

        async with self._adaptive_lock:
            self._success_counts[host] = self._success_counts.get(host, 0) + 1

            if self._success_counts[host] >= self._success_threshold:
                current = self._current_delays.get(host, self.default_delay)
                new_delay = max(current / self._backoff_factor, self._min_delay)

                if new_delay < current:
                    self._current_delays[host] = new_delay
                    self.update_host_config(host, delay=new_delay)
                    self._success_counts[host] = 0
                    logger.debug(f"Reducing delay for {host} to {new_delay:.1f}s")

    def get_stats(self) -> dict:
        """Get adaptive rate limiter statistics."""
        base_stats = super().get_stats()
        base_stats["adapted_hosts"] = len(self._current_delays)
        return base_stats
