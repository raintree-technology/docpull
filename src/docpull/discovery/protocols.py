"""Protocol definitions for URL discovery."""

from collections.abc import AsyncIterator
from typing import Optional, Protocol


class UrlFilter(Protocol):
    """
    Protocol for URL filtering.

    Implementations decide which URLs to include during discovery.
    """

    def should_include(self, url: str) -> bool:
        """
        Determine if a URL should be included.

        Args:
            url: The URL to check

        Returns:
            True if the URL should be included, False to filter it out
        """
        ...


class UrlDiscoverer(Protocol):
    """
    Protocol for URL discovery strategies.

    Implementations discover URLs to fetch from a starting point.
    """

    async def discover(
        self,
        start_url: str,
        *,
        max_urls: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """
        Discover URLs starting from the given URL.

        Yields URLs as they are discovered, allowing for streaming consumption.

        Args:
            start_url: The URL to start discovery from
            max_urls: Maximum number of URLs to discover (None = unlimited)

        Yields:
            Discovered URLs
        """
        ...
