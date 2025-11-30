"""Enhanced link extraction with data attributes, onclick handlers, and JSON-LD."""

import json
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ...http.protocols import HttpClient

logger = logging.getLogger(__name__)


class EnhancedLinkExtractor:
    """
    Enhanced link extraction for semi-dynamic pages.

    In addition to standard <a href>, extracts links from:
    - data-href, data-url, data-link attributes
    - onclick handlers with URL patterns
    - JSON-LD structured data
    - Next.js/Nuxt prefetch hints

    Best for sites that use some JavaScript but still have URL hints
    in the DOM.

    Example:
        extractor = EnhancedLinkExtractor(http_client)
        links = await extractor.extract_links("https://example.com")
    """

    # Patterns to skip when extracting links
    SKIP_PREFIXES = ("javascript:", "#", "mailto:", "tel:", "data:")

    # Data attributes that commonly contain URLs
    DATA_ATTRS = [
        "data-href",
        "data-url",
        "data-link",
        "data-src",
        "data-page",
        "data-route",
    ]

    # Regex patterns for onclick handlers
    ONCLICK_PATTERNS = [
        # location.href = '/path' or window.location = '/path'
        re.compile(r"(?:location\.href|window\.location)\s*=\s*['\"]([^'\"]+)['\"]"),
        # router.push('/path') - Vue/React routers
        re.compile(r"router\.push\(['\"]([^'\"]+)['\"]"),
        # navigate('/path') - generic navigation
        re.compile(r"navigate\(['\"]([^'\"]+)['\"]"),
        # go('/path') - generic navigation
        re.compile(r"go\(['\"]([^'\"]+)['\"]"),
        # history.push('/path')
        re.compile(r"history\.push\(['\"]([^'\"]+)['\"]"),
    ]

    def __init__(
        self,
        http_client: HttpClient,
        enable_data_attrs: bool = True,
        enable_onclick: bool = True,
        enable_json_ld: bool = True,
        enable_prefetch: bool = True,
        custom_data_attrs: Optional[list[str]] = None,
    ):
        """
        Initialize the enhanced link extractor.

        Args:
            http_client: HTTP client for fetching pages
            enable_data_attrs: Enable data-* attribute extraction
            enable_onclick: Enable onclick handler parsing
            enable_json_ld: Enable JSON-LD URL extraction
            enable_prefetch: Enable prefetch/preload link extraction
            custom_data_attrs: Additional data attributes to check
        """
        self._client = http_client
        self._enable_data_attrs = enable_data_attrs
        self._enable_onclick = enable_onclick
        self._enable_json_ld = enable_json_ld
        self._enable_prefetch = enable_prefetch
        self._data_attrs = self.DATA_ATTRS.copy()
        if custom_data_attrs:
            self._data_attrs.extend(custom_data_attrs)

    async def extract_links(
        self,
        url: str,
        content: Optional[bytes] = None,
    ) -> list[str]:
        """
        Extract links using enhanced patterns.

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

        links: set[str] = set()

        try:
            soup = BeautifulSoup(content, "html.parser")
        except Exception as e:
            logger.debug(f"Failed to parse HTML: {e}")
            return []

        # Standard <a href> extraction
        links.update(self._extract_standard_links(soup, url))

        # Data attribute extraction
        if self._enable_data_attrs:
            links.update(self._extract_data_attr_links(soup, url))

        # onclick handler extraction
        if self._enable_onclick:
            links.update(self._extract_onclick_links(soup, url))

        # JSON-LD extraction
        if self._enable_json_ld:
            links.update(self._extract_json_ld_links(soup, url))

        # Prefetch/preload link extraction
        if self._enable_prefetch:
            links.update(self._extract_prefetch_links(soup, url))

        return list(links)

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

            content_type = response.content_type.lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                return None

            return response.content

        except Exception as e:
            logger.debug(f"Failed to fetch {url}: {e}")
            return None

    def _extract_standard_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """Extract links from standard <a href> tags."""
        links = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            resolved = self._resolve_url(href, base_url)
            if resolved:
                links.append(resolved)
        return links

    def _extract_data_attr_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """Extract links from data-* attributes."""
        links = []
        for attr in self._data_attrs:
            for elem in soup.find_all(attrs={attr: True}):
                href = elem.get(attr)
                if href:
                    resolved = self._resolve_url(href, base_url)
                    if resolved:
                        links.append(resolved)
        return links

    def _extract_onclick_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """
        Extract URLs from onclick handlers.

        Matches patterns like:
        - onclick="location.href='/path'"
        - onclick="router.push('/path')"
        """
        links = []
        for elem in soup.find_all(onclick=True):
            onclick = elem.get("onclick", "")
            for pattern in self.ONCLICK_PATTERNS:
                for match in pattern.findall(onclick):
                    resolved = self._resolve_url(match, base_url)
                    if resolved:
                        links.append(resolved)
        return links

    def _extract_json_ld_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """
        Extract URLs from JSON-LD structured data.

        Looks for <script type="application/ld+json"> and extracts URLs
        from common fields like 'url', '@id', 'mainEntityOfPage'.
        """
        links = []
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue

            try:
                data = json.loads(script.string)
                urls = self._extract_urls_from_json(data, base_url)
                links.extend(urls)
            except json.JSONDecodeError:
                continue

        return links

    def _extract_urls_from_json(self, data: dict | list, base_url: str) -> list[str]:
        """Recursively extract URLs from JSON-LD data."""
        urls = []

        # URL-like field names to check
        url_fields = {"url", "@id", "mainEntityOfPage", "sameAs", "image", "logo"}

        if isinstance(data, dict):
            for key, value in data.items():
                if key in url_fields and isinstance(value, str):
                    resolved = self._resolve_url(value, base_url)
                    if resolved:
                        urls.append(resolved)
                elif isinstance(value, dict | list):
                    urls.extend(self._extract_urls_from_json(value, base_url))

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict | list):
                    urls.extend(self._extract_urls_from_json(item, base_url))
                elif isinstance(item, str):
                    # sameAs can be a list of URLs
                    resolved = self._resolve_url(item, base_url)
                    if resolved:
                        urls.append(resolved)

        return urls

    def _extract_prefetch_links(self, soup: BeautifulSoup, base_url: str) -> list[str]:
        """
        Extract URLs from prefetch/preload hints.

        Looks for:
        - <link rel="prefetch" href="...">
        - <link rel="preload" href="...">
        - <link rel="prerender" href="...">
        """
        links = []
        prefetch_rels = {"prefetch", "preload", "prerender"}

        for link in soup.find_all("link", href=True):
            rel = link.get("rel", [])
            # rel can be a list or string
            if isinstance(rel, str):
                rel = [rel]

            if any(r in prefetch_rels for r in rel):
                href = link["href"]
                # Only include document-like resources (filter out CSS, JS, fonts)
                as_type = link.get("as", "")
                if as_type in ("", "document", "fetch"):
                    resolved = self._resolve_url(href, base_url)
                    if resolved:
                        links.append(resolved)

        return links

    def _is_valid_href(self, href: str) -> bool:
        """Check if href should be processed."""
        if not href:
            return False

        return all(not href.startswith(prefix) for prefix in self.SKIP_PREFIXES)

    def _resolve_url(self, href: str, base_url: str) -> Optional[str]:
        """Resolve and clean a URL."""
        if not self._is_valid_href(href):
            return None

        try:
            absolute_url = urljoin(base_url, href)
        except Exception:
            return None

        # Validate it's a proper URL
        parsed = urlparse(absolute_url)
        if not parsed.scheme or not parsed.netloc:
            return None

        # Only allow http/https
        if parsed.scheme not in ("http", "https"):
            return None

        # Remove fragment
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if parsed.query:
            clean_url += f"?{parsed.query}"

        return clean_url
