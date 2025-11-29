"""Browser context pool for JavaScript rendering."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import TracebackType
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Check for Playwright availability
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page, Playwright


class BrowserContextPool:
    """
    Pool of browser contexts for efficient JavaScript rendering.

    Manages a pool of reusable browser contexts to avoid the overhead
    of creating new browser instances for each page. Contexts are
    isolated for security but share the browser process.

    Features:
    - Configurable pool size
    - Automatic context recycling
    - Graceful shutdown
    - Semaphore-based concurrency control

    Example:
        async with BrowserContextPool(max_contexts=5) as pool:
            async with pool.acquire() as page:
                await page.goto("https://example.com")
                html = await page.content()

    Requires: pip install docpull[js]
    """

    def __init__(
        self,
        max_contexts: int = 5,
        headless: bool = True,
        user_agent: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """
        Initialize the browser context pool.

        Args:
            max_contexts: Maximum number of concurrent browser contexts
            headless: Run browser in headless mode
            user_agent: Custom user agent string
            timeout: Default timeout for page operations (seconds)
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for JavaScript rendering. " "Install with: pip install docpull[js]"
            )

        self._max_contexts = max_contexts
        self._headless = headless
        self._user_agent = user_agent
        self._timeout = timeout * 1000  # Convert to milliseconds

        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._context_pool: list[BrowserContext] = []
        self._available: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._initialized = False

    async def _create_context(self) -> BrowserContext:
        """Create a new browser context."""
        context_options: dict[str, object] = {
            "viewport": {"width": 1920, "height": 1080},
            "java_script_enabled": True,
            "ignore_https_errors": True,
        }

        if self._user_agent:
            context_options["user_agent"] = self._user_agent

        if self._browser is None:
            raise RuntimeError("Browser not initialized")
        context = await self._browser.new_context(**context_options)  # type: ignore[arg-type]
        context.set_default_timeout(self._timeout)
        return context

    async def __aenter__(self) -> BrowserContextPool:
        """Enter async context and initialize browser."""
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self._headless,
        )
        self._semaphore = asyncio.Semaphore(self._max_contexts)

        # Pre-create contexts
        for _ in range(self._max_contexts):
            context = await self._create_context()
            self._context_pool.append(context)
            await self._available.put(context)

        self._initialized = True
        logger.info(f"Browser pool initialized with {self._max_contexts} contexts")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context and cleanup resources."""
        # Close all contexts
        for context in self._context_pool:
            try:
                await context.close()
            except Exception as e:
                logger.debug(f"Error closing context: {e}")

        self._context_pool.clear()

        # Close browser
        if self._browser:
            await self._browser.close()
            self._browser = None

        # Stop playwright
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

        self._initialized = False
        logger.info("Browser pool shut down")

    def acquire(self) -> BrowserContextManager:
        """
        Acquire a browser context from the pool.

        Returns a context manager that provides a Page object.

        Example:
            async with pool.acquire() as page:
                await page.goto(url)
                content = await page.content()
        """
        if not self._initialized:
            raise RuntimeError("Browser pool not initialized. Use 'async with' context.")
        return BrowserContextManager(self)

    async def _get_context(self) -> BrowserContext:
        """Get a context from the pool."""
        if self._semaphore is None:
            raise RuntimeError("Pool not initialized")
        await self._semaphore.acquire()
        return await self._available.get()

    async def _return_context(self, context: BrowserContext) -> None:
        """Return a context to the pool."""
        # Clear pages to reset state
        for page in context.pages:
            with contextlib.suppress(Exception):
                await page.close()

        await self._available.put(context)
        if self._semaphore is not None:
            self._semaphore.release()


class BrowserContextManager:
    """Context manager for acquiring a page from the pool."""

    def __init__(self, pool: BrowserContextPool) -> None:
        self._pool = pool
        self._context: BrowserContext | None = None
        self._page: Page | None = None

    async def __aenter__(self) -> Page:
        """Acquire context and create page."""
        self._context = await self._pool._get_context()
        self._page = await self._context.new_page()
        return self._page

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close page and return context to pool."""
        if self._page:
            with contextlib.suppress(Exception):
                await self._page.close()
            self._page = None

        if self._context:
            await self._pool._return_context(self._context)
            self._context = None


class BrowserFetcher:
    """
    Fetch pages using browser rendering.

    Wrapper around BrowserContextPool for simple page fetching.

    Example:
        async with BrowserFetcher() as fetcher:
            html = await fetcher.fetch("https://example.com")
    """

    def __init__(
        self,
        max_contexts: int = 5,
        headless: bool = True,
        user_agent: str | None = None,
        timeout: float = 30.0,
        wait_until: str = "networkidle",
    ) -> None:
        """
        Initialize the browser fetcher.

        Args:
            max_contexts: Maximum concurrent contexts
            headless: Run in headless mode
            user_agent: Custom user agent
            timeout: Page load timeout (seconds)
            wait_until: Wait condition ('load', 'domcontentloaded', 'networkidle')
        """
        self._pool = BrowserContextPool(
            max_contexts=max_contexts,
            headless=headless,
            user_agent=user_agent,
            timeout=timeout,
        )
        self._wait_until = wait_until
        self._timeout = timeout * 1000

    async def __aenter__(self) -> BrowserFetcher:
        """Initialize browser pool."""
        await self._pool.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Shutdown browser pool."""
        await self._pool.__aexit__(exc_type, exc_val, exc_tb)

    async def fetch(self, url: str) -> bytes | None:
        """
        Fetch a page and return its HTML content.

        Args:
            url: URL to fetch

        Returns:
            HTML content as bytes, or None if fetch failed
        """
        try:
            async with self._pool.acquire() as page:
                response = await page.goto(
                    url,
                    wait_until=self._wait_until,  # type: ignore[arg-type]
                    timeout=self._timeout,
                )

                if response is None or response.status >= 400:
                    logger.warning(
                        f"Browser fetch failed for {url}: "
                        f"status={response.status if response else 'None'}"
                    )
                    return None

                content = await page.content()
                return content.encode("utf-8")

        except Exception as e:
            logger.error(f"Browser fetch error for {url}: {e}")
            return None

    async def fetch_with_scroll(
        self,
        url: str,
        scroll_count: int = 3,
        scroll_delay: float = 0.5,
    ) -> bytes | None:
        """
        Fetch a page with scrolling to trigger lazy loading.

        Args:
            url: URL to fetch
            scroll_count: Number of scroll actions
            scroll_delay: Delay between scrolls (seconds)

        Returns:
            HTML content as bytes, or None if fetch failed
        """
        try:
            async with self._pool.acquire() as page:
                response = await page.goto(
                    url,
                    wait_until=self._wait_until,  # type: ignore[arg-type]
                    timeout=self._timeout,
                )

                if response is None or response.status >= 400:
                    return None

                # Scroll to trigger lazy loading
                for _ in range(scroll_count):
                    await page.evaluate("window.scrollBy(0, window.innerHeight)")
                    await asyncio.sleep(scroll_delay)

                # Scroll back to top
                await page.evaluate("window.scrollTo(0, 0)")
                await asyncio.sleep(0.2)

                content = await page.content()
                return content.encode("utf-8")

        except Exception as e:
            logger.error(f"Browser fetch error for {url}: {e}")
            return None
