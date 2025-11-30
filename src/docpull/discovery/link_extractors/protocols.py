"""Protocol definitions for link extraction."""

from typing import Optional, Protocol


class LinkExtractor(Protocol):
    """
    Protocol for extracting links from page content.

    Implementations can use different strategies:
    - Static HTML parsing (BeautifulSoup)
    - Enhanced patterns (data-href, onclick)
    - Browser-based with JS execution
    """

    async def extract_links(
        self,
        url: str,
        content: Optional[bytes] = None,
    ) -> list[str]:
        """
        Extract links from a page.

        Args:
            url: The page URL (may need to fetch if content is None)
            content: Optional pre-fetched HTML content

        Returns:
            List of absolute URLs found on the page
        """
        ...
