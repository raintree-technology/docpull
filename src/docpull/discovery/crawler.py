"""Link crawling URL discovery."""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..http.protocols import HttpClient
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator
from ._fetch import fetch_html
from .filters import DomainFilter, PatternFilter, SeenUrlTracker

if TYPE_CHECKING:
    from .link_extractors.protocols import LinkExtractor

logger = logging.getLogger(__name__)


class LinkCrawler:
    """
    Discovers URLs by crawling and following links.

    Features:
    - Breadth-first crawling
    - Depth limiting
    - Domain restriction (stays on same domain)
    - robots.txt compliance
    - URL validation and filtering

    Example:
        http_client = AsyncHttpClient(rate_limiter)
        crawler = LinkCrawler(http_client, url_validator, robots_checker)

        async for url in crawler.discover("https://docs.example.com", max_depth=3):
            print(f"Found: {url}")
    """

    def __init__(
        self,
        http_client: HttpClient,
        url_validator: UrlValidator,
        robots_checker: RobotsChecker,
        max_depth: int = 5,
        pattern_filter: PatternFilter | None = None,
        stay_on_domain: bool = True,
        link_extractor: LinkExtractor | None = None,
    ):
        """
        Initialize the link crawler.

        Args:
            http_client: HTTP client for fetching pages
            url_validator: URL validator for security checks
            robots_checker: robots.txt compliance checker
            max_depth: Maximum crawl depth from starting URL
            pattern_filter: Optional pattern filter for URLs
            stay_on_domain: Whether to only follow links on same domain
            link_extractor: Optional custom link extractor (defaults to internal)
        """
        self._client = http_client
        self._validator = url_validator
        self._robots = robots_checker
        self._max_depth = max_depth
        self._pattern_filter = pattern_filter
        self._stay_on_domain = stay_on_domain
        self._link_extractor = link_extractor
        self._seen = SeenUrlTracker()
        self._domain_filter: DomainFilter | None = None

    def _extract_links(self, html: bytes, base_url: str) -> list[str]:
        """
        Extract links from HTML content.

        Args:
            html: Raw HTML bytes
            base_url: Base URL for resolving relative links

        Returns:
            List of absolute URLs
        """
        links: list[str] = []

        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception as e:
            logger.debug(f"Failed to parse HTML: {e}")
            return links

        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]

            # Skip empty, anchor-only, or javascript links
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # Resolve relative URLs
            try:
                absolute_url = urljoin(base_url, href)
            except ValueError:
                continue

            # Remove fragment
            parsed = urlparse(absolute_url)
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if parsed.query:
                clean_url += f"?{parsed.query}"

            links.append(clean_url)

        return links

    def _should_crawl(self, url: str) -> bool:
        """
        Check if a URL is safe and in-scope for crawling.

        Args:
            url: URL to check

        Returns:
            True if URL can be fetched and traversed
        """
        # Security validation
        if not self._validator.is_valid(url):
            return False

        # robots.txt check
        if not self._robots.is_allowed(url):
            return False

        # Domain filter
        return not self._domain_filter or self._domain_filter.should_include(url)

    def _should_include(self, url: str) -> bool:
        """Check whether a crawled URL should be emitted to consumers."""
        return not (self._pattern_filter and not self._pattern_filter.should_include(url))

    async def discover(
        self,
        start_url: str,
        *,
        max_urls: int | None = None,
        max_depth: int | None = None,
    ) -> AsyncIterator[str]:
        """
        Discover URLs by crawling from a starting point.

        Uses breadth-first search with depth limiting.

        Args:
            start_url: The URL to start crawling from
            max_urls: Maximum number of URLs to discover
            max_depth: Maximum depth (overrides instance default)

        Yields:
            Discovered URLs
        """
        self._seen.clear()

        # Set up domain filter
        if self._stay_on_domain:
            self._domain_filter = DomainFilter(start_url, allow_subdomains=False)
        else:
            self._domain_filter = None

        # Use provided max_depth or instance default
        effective_max_depth = max_depth if max_depth is not None else self._max_depth

        count = 0

        if not self._should_crawl(start_url):
            logger.info("Skipping disallowed start URL: %s", start_url)
            return

        # BFS queue: (url, depth)
        queue: deque[tuple[str, int]] = deque()
        queue.append((start_url, 0))
        self._seen.add(start_url)

        # Traverse the seed even if path filters exclude it, otherwise a common
        # "start at /, include only /docs/*" crawl never reaches the docs tree.
        if self._should_include(start_url):
            yield start_url
            count += 1

            if max_urls is not None and count >= max_urls:
                return

        while queue:
            current_url, depth = queue.popleft()

            # Stop if max depth reached
            if depth >= effective_max_depth:
                continue

            # Extract links using custom extractor or built-in method
            if self._link_extractor is not None:
                # Custom extractor handles fetching internally
                links = await self._link_extractor.extract_links(current_url)
            else:
                # Built-in extraction with separate fetch
                html = await fetch_html(self._client, current_url)
                if html is None:
                    continue
                links = self._extract_links(html, current_url)

            logger.debug(f"Found {len(links)} links on {current_url}")

            for link in links:
                # Check if already seen
                if not self._seen.add(link):
                    continue

                # Check if should crawl
                if not self._should_crawl(link):
                    continue

                # Add to queue for further crawling
                if depth + 1 < effective_max_depth:
                    queue.append((link, depth + 1))

                if not self._should_include(link):
                    continue

                # Yield the URL
                yield link
                count += 1

                if max_urls is not None and count >= max_urls:
                    return

        logger.info(f"Crawl complete: found {count} URLs")
