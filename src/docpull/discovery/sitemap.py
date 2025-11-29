"""Sitemap-based URL discovery."""

import logging
from collections.abc import AsyncIterator
from typing import Optional
from urllib.parse import urlparse

from defusedxml import ElementTree

from ..http.protocols import HttpClient
from ..security.url_validator import UrlValidator
from .filters import PatternFilter, SeenUrlTracker

logger = logging.getLogger(__name__)

# Sitemap XML namespace
SITEMAP_NS = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}


class SitemapDiscoverer:
    """
    Discovers URLs from sitemap.xml and sitemap index files.

    Features:
    - Parses standard sitemap.xml format
    - Handles sitemap index files (recursive discovery)
    - XXE protection via defusedxml
    - Size limits to prevent DoS
    - URL validation and filtering

    Example:
        http_client = AsyncHttpClient(rate_limiter)
        discoverer = SitemapDiscoverer(http_client, url_validator)

        async for url in discoverer.discover("https://example.com"):
            print(f"Found: {url}")
    """

    MAX_SITEMAP_SIZE = 50 * 1024 * 1024  # 50 MB
    MAX_SITEMAP_DEPTH = 5  # Maximum nesting for sitemap indexes

    def __init__(
        self,
        http_client: HttpClient,
        url_validator: UrlValidator,
        pattern_filter: Optional[PatternFilter] = None,
    ):
        """
        Initialize the sitemap discoverer.

        Args:
            http_client: HTTP client for fetching sitemaps
            url_validator: URL validator for security checks
            pattern_filter: Optional pattern filter for URLs
        """
        self._client = http_client
        self._validator = url_validator
        self._filter = pattern_filter
        self._seen = SeenUrlTracker()

    def _guess_sitemap_urls(self, base_url: str) -> list[str]:
        """
        Guess possible sitemap locations for a URL.

        Args:
            base_url: The starting URL

        Returns:
            List of possible sitemap URLs to try
        """
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        return [
            f"{base}/sitemap.xml",
            f"{base}/sitemap_index.xml",
            f"{base}/sitemap/sitemap.xml",
            f"{base}/sitemaps/sitemap.xml",
        ]

    async def _fetch_sitemap(self, url: str) -> Optional[bytes]:
        """
        Fetch sitemap content with size validation.

        Args:
            url: Sitemap URL to fetch

        Returns:
            Sitemap content as bytes, or None if fetch failed
        """
        if not self._validator.is_valid(url):
            logger.debug(f"Sitemap URL validation failed: {url}")
            return None

        try:
            response = await self._client.get(url, timeout=30.0)

            if response.status_code != 200:
                logger.debug(f"Sitemap returned status {response.status_code}: {url}")
                return None

            if len(response.content) > self.MAX_SITEMAP_SIZE:
                logger.warning(f"Sitemap too large ({len(response.content)} bytes): {url}")
                return None

            return response.content

        except Exception as e:
            logger.debug(f"Failed to fetch sitemap {url}: {e}")
            return None

    def _parse_sitemap(self, content: bytes) -> tuple[list[str], list[str]]:
        """
        Parse sitemap XML content.

        Args:
            content: Raw XML bytes

        Returns:
            Tuple of (page_urls, sitemap_urls)
        """
        page_urls: list[str] = []
        sitemap_urls: list[str] = []

        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as e:
            logger.warning(f"Failed to parse sitemap XML: {e}")
            return page_urls, sitemap_urls

        # Try with namespace first, then without
        for use_ns in [True, False]:
            ns = SITEMAP_NS if use_ns else {}
            prefix = "ns:" if use_ns else ""

            # Find page URLs
            for url_elem in root.findall(f".//{prefix}url/{prefix}loc", ns):
                if url_elem.text:
                    page_urls.append(url_elem.text.strip())

            # Find nested sitemap URLs
            for sitemap_elem in root.findall(f".//{prefix}sitemap/{prefix}loc", ns):
                if sitemap_elem.text:
                    sitemap_urls.append(sitemap_elem.text.strip())

            if page_urls or sitemap_urls:
                break

        return page_urls, sitemap_urls

    async def _discover_from_sitemap(
        self,
        sitemap_url: str,
        depth: int = 0,
        max_urls: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Recursively discover URLs from a sitemap.

        Args:
            sitemap_url: URL of sitemap to process
            depth: Current recursion depth
            max_urls: Maximum URLs to yield (None = unlimited)

        Yields:
            Discovered page URLs
        """
        if depth > self.MAX_SITEMAP_DEPTH:
            logger.warning(f"Max sitemap depth exceeded at {sitemap_url}")
            return

        content = await self._fetch_sitemap(sitemap_url)
        if content is None:
            return

        page_urls, nested_sitemaps = self._parse_sitemap(content)

        logger.debug(
            f"Sitemap {sitemap_url}: {len(page_urls)} URLs, " f"{len(nested_sitemaps)} nested sitemaps"
        )

        # Yield page URLs
        count = 0
        for url in page_urls:
            # Check max URLs limit
            if max_urls is not None and count >= max_urls:
                return

            # Validate URL
            if not self._validator.is_valid(url):
                continue

            # Apply pattern filter
            if self._filter and not self._filter.should_include(url):
                continue

            # Skip duplicates
            if not self._seen.add(url):
                continue

            yield url
            count += 1

        # Process nested sitemaps
        remaining = max_urls - count if max_urls is not None else None
        for nested_url in nested_sitemaps:
            if remaining is not None and remaining <= 0:
                return

            async for url in self._discover_from_sitemap(nested_url, depth + 1, remaining):
                yield url
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        return

    async def discover(
        self,
        start_url: str,
        *,
        max_urls: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Discover URLs from sitemap for a website.

        Tries common sitemap locations if not provided directly.

        Args:
            start_url: The base URL or direct sitemap URL
            max_urls: Maximum number of URLs to discover

        Yields:
            Discovered URLs
        """
        self._seen.clear()

        # If URL looks like a sitemap, use it directly
        if start_url.endswith(".xml"):
            async for url in self._discover_from_sitemap(start_url, max_urls=max_urls):
                yield url
            return

        # Try common sitemap locations
        sitemap_urls = self._guess_sitemap_urls(start_url)

        count = 0
        for sitemap_url in sitemap_urls:
            remaining = max_urls - count if max_urls is not None else None

            async for url in self._discover_from_sitemap(sitemap_url, max_urls=remaining):
                yield url
                count += 1

                if max_urls is not None and count >= max_urls:
                    return

        if count == 0:
            logger.info(f"No sitemap found for {start_url}")
