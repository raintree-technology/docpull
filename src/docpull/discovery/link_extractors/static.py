"""Static HTML link extraction using BeautifulSoup."""

import logging
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ...http.protocols import HttpClient

logger = logging.getLogger(__name__)


class StaticLinkExtractor:
    """
    Extract links using static HTML parsing (BeautifulSoup).

    This is the default behavior, suitable for:
    - Server-rendered pages
    - Sites with standard <a href> links

    Example:
        extractor = StaticLinkExtractor(http_client)
        links = await extractor.extract_links("https://example.com")
    """

    # Patterns to skip when extracting links
    SKIP_PREFIXES = ("javascript:", "#", "mailto:", "tel:", "data:")

    def __init__(
        self,
        http_client: HttpClient,
    ):
        """
        Initialize the static link extractor.

        Args:
            http_client: HTTP client for fetching pages when content not provided
        """
        self._client = http_client

    async def extract_links(
        self,
        url: str,
        content: Optional[bytes] = None,
    ) -> list[str]:
        """
        Extract links from HTML using BeautifulSoup.

        Args:
            url: The page URL
            content: Optional pre-fetched HTML content

        Returns:
            List of absolute URLs found on the page
        """
        if content is None:
            content = await self._fetch_content(url)
            if content is None:
                return []

        return self._parse_links(content, url)

    async def _fetch_content(self, url: str) -> Optional[bytes]:
        """
        Fetch page content for link extraction.

        Args:
            url: URL to fetch

        Returns:
            HTML content as bytes, or None if fetch failed
        """
        try:
            response = await self._client.get(url, timeout=30.0)

            if response.status_code != 200:
                return None

            # Only process HTML content
            content_type = response.content_type.lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return None

            return response.content

        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return None

    def _parse_links(self, html: bytes, base_url: str) -> list[str]:
        """
        Parse links from HTML content.

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

            if not self._is_valid_href(href):
                continue

            resolved = self._resolve_url(href, base_url)
            if resolved:
                links.append(resolved)

        return links

    def _is_valid_href(self, href: str) -> bool:
        """
        Check if href should be processed.

        Args:
            href: The href attribute value

        Returns:
            True if the href is valid for processing
        """
        if not href:
            return False

        return all(not href.startswith(prefix) for prefix in self.SKIP_PREFIXES)

    def _resolve_url(self, href: str, base_url: str) -> Optional[str]:
        """
        Resolve and clean a URL.

        Args:
            href: The href to resolve
            base_url: Base URL for resolution

        Returns:
            Cleaned absolute URL, or None if resolution failed
        """
        try:
            absolute_url = urljoin(base_url, href)
        except Exception:
            return None

        # Remove fragment
        parsed = urlparse(absolute_url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean_url += f"?{parsed.query}"

        return clean_url
