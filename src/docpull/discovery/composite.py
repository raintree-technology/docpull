"""Composite URL discovery combining multiple strategies."""

import logging
from collections.abc import AsyncIterator
from typing import Optional

from .crawler import LinkCrawler
from .filters import SeenUrlTracker
from .sitemap import SitemapDiscoverer

logger = logging.getLogger(__name__)


class CompositeDiscoverer:
    """
    Combines sitemap and crawling discovery strategies.

    Tries sitemap first (faster, more complete), then falls back to
    link crawling if sitemap yields insufficient results.

    Features:
    - Automatic deduplication across strategies
    - Configurable minimum URLs before fallback
    - Respects max_urls limit across all strategies

    Example:
        discoverer = CompositeDiscoverer(
            sitemap_discoverer=sitemap,
            link_crawler=crawler,
            fallback_threshold=10,
        )

        async for url in discoverer.discover("https://docs.example.com"):
            print(f"Found: {url}")
    """

    def __init__(
        self,
        sitemap_discoverer: SitemapDiscoverer,
        link_crawler: Optional[LinkCrawler] = None,
        fallback_threshold: int = 5,
    ):
        """
        Initialize the composite discoverer.

        Args:
            sitemap_discoverer: Sitemap-based discovery
            link_crawler: Optional crawler for fallback (if None, no fallback)
            fallback_threshold: Minimum URLs from sitemap before skipping crawl
        """
        self._sitemap = sitemap_discoverer
        self._crawler = link_crawler
        self._fallback_threshold = fallback_threshold
        self._seen = SeenUrlTracker()

    async def discover(
        self,
        start_url: str,
        *,
        max_urls: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Discover URLs using sitemap with crawl fallback.

        Strategy:
        1. Try sitemap discovery first
        2. If sitemap yields < fallback_threshold URLs, also crawl
        3. Deduplicate across both strategies

        Args:
            start_url: The URL to start discovery from
            max_urls: Maximum number of URLs to discover

        Yields:
            Discovered URLs (deduplicated)
        """
        self._seen.clear()
        count = 0

        # Phase 1: Sitemap discovery
        logger.debug(f"Starting sitemap discovery for {start_url}")
        sitemap_count = 0

        async for url in self._sitemap.discover(start_url, max_urls=max_urls):
            if not self._seen.add(url):
                continue

            yield url
            count += 1
            sitemap_count += 1

            if max_urls is not None and count >= max_urls:
                logger.info(f"Discovery complete: {count} URLs from sitemap")
                return

        logger.debug(f"Sitemap discovery yielded {sitemap_count} URLs")

        # Phase 2: Crawl fallback (if enabled and needed)
        if self._crawler is None:
            logger.info(f"Discovery complete: {count} URLs (no crawler configured)")
            return

        if sitemap_count >= self._fallback_threshold:
            logger.info(
                f"Discovery complete: {count} URLs from sitemap "
                f"(above threshold of {self._fallback_threshold})"
            )
            return

        logger.debug(
            f"Sitemap yielded {sitemap_count} URLs (below threshold "
            f"{self._fallback_threshold}), falling back to crawling"
        )

        remaining = max_urls - count if max_urls is not None else None

        async for url in self._crawler.discover(start_url, max_urls=remaining):
            if not self._seen.add(url):
                continue

            yield url
            count += 1

            if max_urls is not None and count >= max_urls:
                break

        logger.info(f"Discovery complete: {count} total URLs")
