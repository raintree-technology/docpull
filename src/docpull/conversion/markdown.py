"""HTML to Markdown conversion."""

from __future__ import annotations

import logging
import re
import textwrap
from typing import Any
from urllib.parse import urljoin

import html2text

from .extractor import (
    DOCPULL_FENCE_SENTINEL_PREFIX,
    DOCPULL_FENCE_SENTINEL_SUFFIX,
)

logger = logging.getLogger(__name__)


def _normalize_scheme(url: str) -> str:
    """Fix ``https:/example.com`` (single slash) produced by html2text escaping."""
    return re.sub(r"^(https?:)/(?!/)", r"\1//", url)


# html2text wraps <pre><code> in [code]/[/code] markers and indents the body
# by 4 spaces. The opening marker may carry trailing whitespace
# (`[code] \n`); tolerate it so we don't miss real code blocks.
_HTML2TEXT_CODE_BLOCK_RE = re.compile(
    r"\[code\][ \t]*\n(.*?)\n[ \t]*\[/code\]",
    re.DOTALL,
)
_FENCE_SENTINEL_RE = re.compile(
    rf"^[ \t]*{re.escape(DOCPULL_FENCE_SENTINEL_PREFIX)}"
    rf"([\w+#-]+){re.escape(DOCPULL_FENCE_SENTINEL_SUFFIX)}[ \t]*\n",
    re.MULTILINE,
)


def _rewrite_html2text_code_blocks(markdown: str) -> str:
    """Replace ``[code]...[/code]`` markers with GFM fenced blocks.

    html2text indents the body of a ``[code]`` block by 4 spaces; we dedent
    that consistently. If the body's first line is a docpull language
    sentinel (injected by the extractor), the fence is opened with that
    language; otherwise the fence is bare.
    """

    def replace(match: re.Match[str]) -> str:
        body = match.group(1)
        body = textwrap.dedent(body)
        lang = ""
        sentinel_match = _FENCE_SENTINEL_RE.match(body)
        if sentinel_match:
            lang = sentinel_match.group(1)
            body = body[sentinel_match.end() :]
        body = body.rstrip("\n")
        return f"```{lang}\n{body}\n```"

    return _HTML2TEXT_CODE_BLOCK_RE.sub(replace, markdown)


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
        # Convert html2text's [code]/[/code] markers into GFM fences,
        # recovering the language tag from the docpull sentinel injected
        # by MainContentExtractor when the source HTML carried a Prism /
        # highlight.js / Shiki language class. Must run BEFORE blank-line
        # collapsing so the rewritten fences sit on their own lines.
        markdown = _rewrite_html2text_code_blocks(markdown)

        # Remove excessive blank lines
        markdown = re.sub(r"\n{3,}", "\n\n", markdown)

        # Unmangle html2text's protect_links output:
        #   [text](prefix/<https:/real.url>)  ->  [text](https://real.url)
        # The angle-bracketed inner URL is the true absolute URL (the prefix is
        # the page base that html2text erroneously prepended). Allow empty link
        # text too (image-only links, icon wrappers).
        markdown = re.sub(
            r"\[([^\]]*)\]\([^)]*<(https?:/[^>]+)>\)",
            lambda m: f"[{m.group(1)}]({_normalize_scheme(m.group(2))})",
            markdown,
        )

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
