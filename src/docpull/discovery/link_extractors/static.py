"""Static HTML link extraction using BeautifulSoup."""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ...http.protocols import HttpClient
from .._fetch import fetch_html_response

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
        self.last_final_url: str | None = None

    async def extract_links(
        self,
        url: str,
        content: bytes | None = None,
    ) -> list[str]:
        """
        Extract links from HTML using BeautifulSoup.

        Args:
            url: The page URL
            content: Optional pre-fetched HTML content

        Returns:
            List of absolute URLs found on the page
        """
        base_url = url
        self.last_final_url = url
        if content is None:
            response = await fetch_html_response(self._client, url)
            if response is None:
                return []
            content = response.content
            base_url = response.url if isinstance(response.url, str) and response.url else url
            self.last_final_url = base_url

        return self._parse_links(content, base_url)

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

    def _resolve_url(self, href: str, base_url: str) -> str | None:
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
        except Exception as err:
            logger.debug("Could not resolve href %r against %s: %s", href, base_url, err)
            return None

        parsed = urlparse(absolute_url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean_url += f"?{parsed.query}"

        return clean_url
