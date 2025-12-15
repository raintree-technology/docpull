"""Browser-based link extraction with JavaScript execution and network interception."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Check for Playwright availability
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.async_api import Page, Request

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

if TYPE_CHECKING:
    from ...concurrency.browser_pool import BrowserContextPool


class BrowserLinkExtractor:
    """
    Extract links using Playwright with JavaScript execution.

    Features:
    - Executes JavaScript to reveal dynamically-loaded content
    - Intercepts network requests to discover API-fetched URLs
    - Scrolls page to trigger lazy loading
    - Extracts from rendered DOM (not source HTML)

    Best for:
    - SPAs (React, Vue, Angular)
    - Sites with client-side routing
    - Pages that load content via XHR/fetch

    Example:
        async with BrowserContextPool() as pool:
            extractor = BrowserLinkExtractor(pool)
            links = await extractor.extract_links("https://example.com")

    Requires: pip install docpull[js]
    """

    def __init__(
        self,
        browser_pool: BrowserContextPool,
        intercept_requests: bool = True,
        scroll_for_lazy_load: bool = True,
        scroll_count: int = 3,
        scroll_delay: float = 0.3,
        wait_until: str = "networkidle",
    ):
        """
        Initialize the browser link extractor.

        Args:
            browser_pool: Browser context pool for page rendering
            intercept_requests: Capture XHR/fetch URLs during page load
            scroll_for_lazy_load: Scroll page to trigger lazy content
            scroll_count: Number of scroll actions
            scroll_delay: Delay between scrolls (seconds)
            wait_until: Wait condition for page load
        """
        if not PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for browser link extraction. Install with: pip install docpull[js]"
            )

        self._pool = browser_pool
        self._intercept = intercept_requests
        self._scroll = scroll_for_lazy_load
        self._scroll_count = scroll_count
        self._scroll_delay = scroll_delay
        self._wait_until = wait_until

    async def extract_links(
        self,
        url: str,
        content: bytes | None = None,  # Ignored - always fetches via browser
    ) -> list[str]:
        """
        Extract links by rendering page with Playwright.

        Process:
        1. Navigate to page with optional network interception
        2. Wait for network idle
        3. Optionally scroll to trigger lazy loading
        4. Extract all links from rendered DOM
        5. Return intercepted navigation URLs + DOM links

        Args:
            url: The page URL to extract links from
            content: Ignored (browser always fetches the page)

        Returns:
            List of absolute URLs found on the page
        """
        discovered_urls: set[str] = set()
        intercepted_urls: list[str] = []

        try:
            async with self._pool.acquire() as page:
                # Set up network interception
                if self._intercept:

                    def handle_request(request: Request) -> None:
                        # Capture navigation and API requests
                        if request.resource_type in ("document", "xhr", "fetch"):
                            req_url = request.url
                            # Filter out common non-page resources
                            if self._is_potential_page_url(req_url):
                                intercepted_urls.append(req_url)

                    page.on("request", handle_request)  # type: ignore[arg-type]

                # Navigate to page
                try:
                    response = await page.goto(
                        url,
                        wait_until=self._wait_until,  # type: ignore[arg-type]
                    )

                    if response is None or response.status >= 400:
                        logger.warning(
                            f"Browser navigation failed for {url}: "
                            f"status={response.status if response else 'None'}"
                        )
                        return []

                except Exception as e:
                    logger.warning(f"Failed to load {url}: {e}")
                    return []

                # Scroll for lazy loading
                if self._scroll:
                    await self._scroll_page(page)

                # Extract links from rendered DOM
                dom_links = await self._extract_dom_links(page, url)
                discovered_urls.update(dom_links)

                # Add intercepted URLs
                discovered_urls.update(intercepted_urls)

        except Exception as e:
            logger.error(f"Browser link extraction error for {url}: {e}")
            return []

        # Filter to same domain by default for discovery
        base_domain = urlparse(url).netloc
        filtered_urls = [u for u in discovered_urls if urlparse(u).netloc == base_domain]

        return filtered_urls

    async def _scroll_page(self, page: Page) -> None:  # type: ignore[no-any-unimported]
        """Scroll page to trigger lazy loading."""
        import asyncio

        try:
            for _ in range(self._scroll_count):
                await page.evaluate("window.scrollBy(0, window.innerHeight)")
                await asyncio.sleep(self._scroll_delay)

            # Scroll back to top
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.2)
        except Exception as e:
            logger.debug(f"Scroll failed: {e}")

    async def _extract_dom_links(self, page: Page, base_url: str) -> list[str]:  # type: ignore[no-any-unimported]
        """
        Extract links from the rendered DOM using JavaScript.

        This runs in the browser context and can access dynamically-added content.
        """
        try:
            links = await page.evaluate(
                """
                () => {
                    const links = new Set();
                    const baseUrl = window.location.href;

                    // Standard anchors
                    document.querySelectorAll('a[href]').forEach(a => {
                        try {
                            const href = a.getAttribute('href');
                            if (href && !href.startsWith('#') && !href.startsWith('javascript:')) {
                                const url = new URL(href, baseUrl).href;
                                links.add(url);
                            }
                        } catch (e) {}
                    });

                    // Data attributes commonly used for navigation
                    const dataAttrs = ['data-href', 'data-url', 'data-link', 'data-route'];
                    dataAttrs.forEach(attr => {
                        document.querySelectorAll(`[${attr}]`).forEach(el => {
                            try {
                                const val = el.getAttribute(attr);
                                if (val) {
                                    const url = new URL(val, baseUrl).href;
                                    links.add(url);
                                }
                            } catch (e) {}
                        });
                    });

                    // Next.js prefetch links
                    document.querySelectorAll('link[rel="prefetch"][href]').forEach(link => {
                        try {
                            const href = link.getAttribute('href');
                            if (href) {
                                const url = new URL(href, baseUrl).href;
                                links.add(url);
                            }
                        } catch (e) {}
                    });

                    // Navigation elements that might have routes
                    document.querySelectorAll('[role="link"], [role="menuitem"]').forEach(el => {
                        const href = el.getAttribute('href') || el.getAttribute('data-href');
                        if (href) {
                            try {
                                const url = new URL(href, baseUrl).href;
                                links.add(url);
                            } catch (e) {}
                        }
                    });

                    return Array.from(links);
                }
            """
            )
            return links if links else []
        except Exception as e:
            logger.debug(f"DOM link extraction failed: {e}")
            return []

    def _is_potential_page_url(self, url: str) -> bool:
        """
        Check if a URL might be a page (vs static resource).

        Filters out common non-page resources to reduce noise.
        """
        # Skip data URLs
        if url.startswith("data:"):
            return False

        parsed = urlparse(url)

        # Skip common static resource extensions
        static_extensions = {
            ".css",
            ".js",
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".svg",
            ".woff",
            ".woff2",
            ".ttf",
            ".eot",
            ".ico",
            ".mp4",
            ".webp",
            ".mp3",
            ".wav",
            ".pdf",
            ".zip",
            ".json",
            ".xml",
        }

        path = parsed.path.lower()
        for ext in static_extensions:
            if path.endswith(ext):
                return False

        # Skip tracking/analytics URLs
        tracking_patterns = [
            "google-analytics",
            "googletagmanager",
            "analytics",
            "pixel",
            "beacon",
            "tracking",
            "metrics",
        ]
        url_lower = url.lower()
        return all(pattern not in url_lower for pattern in tracking_patterns)
