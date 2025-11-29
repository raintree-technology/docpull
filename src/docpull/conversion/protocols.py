"""Protocol definitions for content conversion."""

from typing import Protocol


class ContentExtractor(Protocol):
    """
    Protocol for extracting main content from HTML.

    Implementations should extract the main article/documentation content
    while removing navigation, headers, footers, ads, etc.
    """

    def extract(self, html: bytes, url: str) -> str:
        """
        Extract main content from HTML.

        Args:
            html: Raw HTML bytes
            url: Source URL (for relative link resolution)

        Returns:
            Extracted HTML content as string (cleaned but still HTML)
        """
        ...


class MarkdownConverter(Protocol):
    """
    Protocol for converting HTML to Markdown.

    Implementations convert cleaned HTML to Markdown format.
    """

    def convert(self, html: str, url: str) -> str:
        """
        Convert HTML to Markdown.

        Args:
            html: HTML content string
            url: Source URL (for resolving relative links)

        Returns:
            Markdown string
        """
        ...
