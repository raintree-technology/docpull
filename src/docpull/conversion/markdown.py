"""HTML to Markdown conversion."""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urljoin

import html2text

logger = logging.getLogger(__name__)


class HtmlToMarkdown:
    """
    Converts HTML content to clean Markdown.

    Uses html2text with optimized settings for documentation.

    Example:
        converter = HtmlToMarkdown()
        markdown = converter.convert(html_string, "https://docs.example.com/page")
    """

    def __init__(
        self,
        body_width: int = 0,
        inline_links: bool = True,
        wrap_links: bool = False,
        ignore_images: bool = False,
        ignore_tables: bool = False,
        protect_links: bool = True,
        unicode_snob: bool = True,
        escape_snob: bool = True,
        mark_code: bool = True,
    ):
        """
        Initialize the Markdown converter.

        Args:
            body_width: Max line width (0 = no wrapping)
            inline_links: Use inline [text](url) vs reference style
            wrap_links: Wrap long links
            ignore_images: Skip image conversion
            ignore_tables: Skip table conversion
            protect_links: Prevent link mangling
            unicode_snob: Use Unicode chars where possible
            escape_snob: Escape special Markdown chars
            mark_code: Mark code blocks with backticks
        """
        self._converter = html2text.HTML2Text()

        # Line width (0 = no wrapping for consistent output)
        self._converter.body_width = body_width

        # Link handling
        self._converter.inline_links = inline_links
        self._converter.wrap_links = wrap_links
        self._converter.protect_links = protect_links

        # Content handling
        self._converter.ignore_images = ignore_images
        self._converter.ignore_tables = ignore_tables
        self._converter.unicode_snob = unicode_snob
        self._converter.escape_snob = escape_snob
        self._converter.mark_code = mark_code

        # Code blocks
        self._converter.default_image_alt = ""
        self._converter.single_line_break = False

    def _clean_output(self, markdown: str) -> str:
        """Clean up the converted Markdown."""
        # Remove excessive blank lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)

        # Fix code block formatting
        # Ensure code blocks have language hint
        markdown = re.sub(r"```\n", "```\n", markdown)

        # Remove trailing whitespace on each line
        markdown = "\n".join(line.rstrip() for line in markdown.split("\n"))

        # Ensure single newline at end
        return markdown.strip() + "\n"

    def _fix_relative_links(self, markdown: str, base_url: str) -> str:
        """Ensure all links are absolute."""

        def replace_link(match: re.Match[str]) -> str:
            text = match.group(1)
            url = match.group(2)

            # Skip anchors and already absolute URLs
            if url.startswith(("#", "http://", "https://", "mailto:", "tel:")):
                result: str = match.group(0)
                return result

            # Convert relative to absolute
            absolute_url = urljoin(base_url, url)
            return f"[{text}]({absolute_url})"

        # Match markdown links [text](url)
        return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, markdown)

    def convert(self, html: str, url: str) -> str:
        """
        Convert HTML to Markdown.

        Args:
            html: HTML content string
            url: Source URL for resolving relative links

        Returns:
            Markdown string
        """
        try:
            # Set base URL for link resolution
            self._converter.baseurl = url

            # Convert to markdown
            markdown = self._converter.handle(html)

            # Clean up output
            markdown = self._clean_output(markdown)

            # Fix any remaining relative links
            markdown = self._fix_relative_links(markdown, url)

            return markdown

        except Exception as e:
            logger.error(f"Failed to convert HTML to Markdown: {e}")
            # Return plain text as fallback
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            text: str = soup.get_text(separator="\n")
            return text.strip() + "\n"


class FrontmatterBuilder:
    """
    Builds YAML frontmatter for Markdown files.

    Example:
        builder = FrontmatterBuilder()
        frontmatter = builder.build(
            title="Getting Started",
            url="https://docs.example.com/getting-started",
            description="How to get started with our product",
        )
    """

    def build(
        self,
        title: str | None = None,
        url: str | None = None,
        description: str | None = None,
        **extra_fields: Any,
    ) -> str:
        """
        Build YAML frontmatter string.

        Args:
            title: Page title
            url: Source URL
            description: Page description
            **extra_fields: Additional frontmatter fields

        Returns:
            YAML frontmatter string (with --- delimiters)
        """
        lines = ["---"]

        if title:
            # Escape quotes in title
            safe_title = title.replace('"', '\\"')
            lines.append(f'title: "{safe_title}"')

        if url:
            lines.append(f"source: {url}")

        if description:
            # Escape quotes and truncate long descriptions
            safe_desc = description[:500].replace('"', '\\"')
            lines.append(f'description: "{safe_desc}"')

        for key, value in extra_fields.items():
            if value is not None:
                if isinstance(value, str):
                    safe_value = value.replace('"', '\\"')
                    lines.append(f'{key}: "{safe_value}"')
                elif isinstance(value, (list, tuple)):
                    lines.append(f"{key}:")
                    for item in value:
                        lines.append(f"  - {item}")
                else:
                    lines.append(f"{key}: {value}")

        lines.append("---")
        return "\n".join(lines) + "\n\n"
