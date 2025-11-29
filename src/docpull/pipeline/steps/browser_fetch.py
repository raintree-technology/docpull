"""Pipeline step for browser-based fetching with JavaScript rendering."""

import logging
from typing import Optional

from ...concurrency.browser_pool import PLAYWRIGHT_AVAILABLE, BrowserFetcher
from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class BrowserFetchStep:
    """
    Pipeline step that fetches pages using browser rendering.

    Uses Playwright to render JavaScript-heavy pages before extraction.
    Falls back gracefully if Playwright is not installed.

    Example:
        async with BrowserFetcher() as fetcher:
            step = BrowserFetchStep(browser_fetcher=fetcher)
            ctx = await step.execute(ctx, emit=callback)
    """

    name = "browser_fetch"

    def __init__(
        self,
        browser_fetcher: Optional[BrowserFetcher] = None,
        scroll_for_lazy_load: bool = False,
        scroll_count: int = 3,
    ):
        """
        Initialize the browser fetch step.

        Args:
            browser_fetcher: BrowserFetcher instance (must be initialized)
            scroll_for_lazy_load: Whether to scroll to trigger lazy loading
            scroll_count: Number of scroll actions if scrolling enabled
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for browser fetching. " "Install with: pip install docpull[js]"
            )

        self._fetcher = browser_fetcher
        self._scroll = scroll_for_lazy_load
        self._scroll_count = scroll_count

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Fetch page content using browser rendering.

        Args:
            ctx: Page context with URL
            emit: Optional event emitter

        Returns:
            Updated context with HTML content
        """
        if ctx.should_skip or ctx.error:
            return ctx

        if self._fetcher is None:
            ctx.error = "BrowserFetcher not initialized"
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        error=ctx.error,
                    )
                )
            return ctx

        if emit:
            emit(
                FetchEvent(
                    type=EventType.FETCH_STARTED,
                    url=ctx.url,
                    message=f"Fetching with browser: {ctx.url}",
                )
            )

        try:
            # Fetch with or without scrolling
            if self._scroll:
                html = await self._fetcher.fetch_with_scroll(
                    ctx.url,
                    scroll_count=self._scroll_count,
                )
            else:
                html = await self._fetcher.fetch(ctx.url)

            if html is None:
                ctx.error = "Browser fetch returned no content"
                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_FAILED,
                            url=ctx.url,
                            error=ctx.error,
                        )
                    )
                return ctx

            ctx.html = html
            ctx.bytes_downloaded = len(html)
            ctx.status_code = 200  # Assume success if we got content

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_COMPLETED,
                        url=ctx.url,
                        bytes_downloaded=ctx.bytes_downloaded,
                        status_code=200,
                        message=f"Browser fetched {len(html)} bytes",
                    )
                )

            logger.debug(f"Browser fetched {ctx.url}: {len(html)} bytes")
            return ctx

        except Exception as e:
            ctx.error = f"Browser fetch failed: {e}"
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        error=ctx.error,
                    )
                )
            logger.error(f"Browser fetch error for {ctx.url}: {e}")
            return ctx
