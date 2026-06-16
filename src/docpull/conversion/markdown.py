"""HTML to Markdown conversion."""

from __future__ import annotations

import logging
import re
import textwrap
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin, urlsplit

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
_LATEX_ESCAPED_DELIMITER_RE = re.compile(r"\\{2,}([()\[\]])")
_INLINE_CODE_RE = re.compile(r"(`+)(.*?)(\1)")
_PROTECTED_URL_RE = re.compile(r"<([^>]+)>")


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


def _restore_latex_math_delimiters(markdown: str) -> str:
    """Undo html2text escaping for LaTeX ``\\(...\\)`` / ``\\[...\\]`` delimiters."""

    def restore_segment(segment: str) -> str:
        return _LATEX_ESCAPED_DELIMITER_RE.sub(r"\\\1", segment)

    restored_lines: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in markdown.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            restored_lines.append(line)
            continue
        if in_fence:
            restored_lines.append(line)
            continue

        pieces: list[str] = []
        cursor = 0
        for match in _INLINE_CODE_RE.finditer(line):
            pieces.append(restore_segment(line[cursor : match.start()]))
            pieces.append(match.group(0))
            cursor = match.end()
        pieces.append(restore_segment(line[cursor:]))
        restored_lines.append("".join(pieces))
    return "".join(restored_lines)


def _unescape_markdown_url(url: str) -> str:
    """Remove Markdown escaping that html2text applies inside URL destinations."""
    return re.sub(r"\\([()<>\\])", r"\1", url)


def _escape_markdown_url(url: str) -> str:
    """Escape characters that would terminate a bare Markdown URL destination."""
    return url.replace("\\", "\\\\").replace(" ", "%20").replace("(", r"\(").replace(")", r"\)")


def _split_link_target(raw_target: str) -> tuple[str, str]:
    """Split ``url "optional title"`` into destination and suffix."""
    stripped = raw_target.strip()
    leading = raw_target[: len(raw_target) - len(raw_target.lstrip())]
    if not stripped:
        return raw_target, ""

    if stripped.startswith("<"):
        end = stripped.find(">")
        if end != -1:
            destination = stripped[: end + 1]
            suffix = stripped[end + 1 :]
            return leading + destination, suffix

    in_angle = False
    escaped = False
    for index, char in enumerate(stripped):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "<":
            in_angle = True
            continue
        if char == ">":
            in_angle = False
            continue
        if char.isspace() and not in_angle:
            return leading + stripped[:index], stripped[index:]
    return leading + stripped, ""


def _normalize_link_destination(destination: str, base_url: str) -> str | None:
    """Return an absolute Markdown URL destination, or None when it should stay unchanged."""
    leading = destination[: len(destination) - len(destination.lstrip())]
    stripped = destination.strip()
    if not stripped:
        return None

    protected = _PROTECTED_URL_RE.search(stripped)
    if protected:
        raw_url = protected.group(1)
    elif stripped.startswith("<") and stripped.endswith(">"):
        raw_url = stripped[1:-1]
    else:
        raw_url = stripped

    raw_url = _normalize_scheme(_unescape_markdown_url(raw_url))
    if raw_url.startswith("#"):
        return None
    if raw_url.startswith(("mailto:", "tel:", "data:")):
        return None

    parsed = urlsplit(raw_url)
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None

    absolute_url = raw_url if parsed.scheme in {"http", "https"} else urljoin(base_url, raw_url)
    return leading + _escape_markdown_url(absolute_url)


def _normalize_protected_absolute_destination(destination: str) -> str | None:
    leading = destination[: len(destination) - len(destination.lstrip())]
    protected = _PROTECTED_URL_RE.search(destination.strip())
    if not protected:
        return None
    raw_url = _normalize_scheme(_unescape_markdown_url(protected.group(1)))
    if urlsplit(raw_url).scheme not in {"http", "https"}:
        return None
    return leading + _escape_markdown_url(raw_url)


def _find_matching_bracket(markdown: str, start: int) -> int:
    depth = 1
    escaped = False
    index = start + 1
    while index < len(markdown):
        char = markdown[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _find_matching_paren(markdown: str, start: int) -> int:
    depth = 0
    escaped = False
    in_angle = False
    quote: str | None = None
    index = start + 1
    while index < len(markdown):
        char = markdown[index]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "<":
            in_angle = True
        elif char == ">":
            in_angle = False
        elif char == "(" and not in_angle:
            depth += 1
        elif char == ")" and not in_angle:
            if depth == 0:
                return index
            depth -= 1
        index += 1
    return -1


def _rewrite_markdown_links(
    markdown: str,
    rewrite_destination: Callable[[str], str | None],
) -> str:
    def rewrite_line(line: str) -> str:
        out: list[str] = []
        index = 0
        while index < len(line):
            char = line[index]
            if char == "`":
                tick_count = 1
                while index + tick_count < len(line) and line[index + tick_count] == "`":
                    tick_count += 1
                marker = "`" * tick_count
                end = line.find(marker, index + tick_count)
                if end == -1:
                    out.append(line[index:])
                    break
                out.append(line[index : end + tick_count])
                index = end + tick_count
                continue

            link_start = index
            if char == "!" and index + 1 < len(line) and line[index + 1] == "[":
                bracket_start = index + 1
            elif char == "[":
                bracket_start = index
            else:
                out.append(char)
                index += 1
                continue

            bracket_end = _find_matching_bracket(line, bracket_start)
            if bracket_end == -1 or bracket_end + 1 >= len(line) or line[bracket_end + 1] != "(":
                out.append(char)
                index += 1
                continue

            paren_start = bracket_end + 1
            paren_end = _find_matching_paren(line, paren_start)
            if paren_end == -1:
                out.append(char)
                index += 1
                continue

            raw_target = line[paren_start + 1 : paren_end]
            destination, suffix = _split_link_target(raw_target)
            normalized = rewrite_destination(destination)
            if normalized is None:
                out.append(line[link_start : paren_end + 1])
            else:
                out.append(line[link_start : paren_start + 1])
                out.append(normalized)
                out.append(suffix)
                out.append(")")
            index = paren_end + 1
        return "".join(out)

    lines: list[str] = []
    in_fence = False
    fence_marker = ""
    for line in markdown.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif stripped.startswith(fence_marker):
                in_fence = False
                fence_marker = ""
            lines.append(line)
            continue
        if in_fence:
            lines.append(line)
            continue
        lines.append(rewrite_line(line))
    return "".join(lines)


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
        markdown = _restore_latex_math_delimiters(markdown)
        markdown = _rewrite_markdown_links(markdown, _normalize_protected_absolute_destination)

        markdown = re.sub(r"\n{3,}", "\n\n", markdown)

        markdown = "\n".join(line.rstrip() for line in markdown.split("\n"))

        return markdown.strip() + "\n"

    def _fix_relative_links(self, markdown: str, base_url: str) -> str:
        """Ensure all links are absolute."""
        return _rewrite_markdown_links(
            markdown,
            lambda destination: _normalize_link_destination(destination, base_url),
        )

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
            self._converter.baseurl = url

            markdown = self._converter.handle(html)

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

    @staticmethod
    def _inline(value: Any) -> str:
        """Collapse CR/LF/NUL so an interpolated value stays on its own YAML line.

        Page-supplied metadata (JSON-LD ``keywords``, OpenGraph ``article:tag``,
        etc.) flows into frontmatter. Without this, a newline in a tag/keyword
        would break out of the list item and inject attacker-chosen top-level
        keys (e.g. ``draft: true``) into the document frontmatter.
        """
        return str(value).replace("\r", " ").replace("\n", " ").replace("\x00", " ")

    @classmethod
    def _quoted(cls, value: Any) -> str:
        """Return a YAML double-quoted scalar for one inline value."""
        safe_value = cls._inline(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{safe_value}"'

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
            lines.append(f"title: {self._quoted(title)}")

        if url:
            lines.append(f"source: {self._inline(url)}")

        if description:
            # Escape quotes and truncate long descriptions
            lines.append(f"description: {self._quoted(description[:500])}")

        for key, value in extra_fields.items():
            if value is not None:
                if isinstance(value, str):
                    lines.append(f"{key}: {self._quoted(value)}")
                elif isinstance(value, (list, tuple)):
                    lines.append(f"{key}:")
                    for item in value:
                        # Quote + escape each item so a hostile tag/keyword (from
                        # page JSON-LD / OpenGraph) stays a single YAML string and
                        # cannot inject new keys or produce malformed frontmatter.
                        lines.append(f"  - {self._quoted(item)}")
                else:
                    lines.append(f"{key}: {self._inline(value)}")

        lines.append("---")
        return "\n".join(lines) + "\n\n"

    def build_okf(
        self,
        *,
        concept_type: str = "Web Page",
        title: str | None = None,
        resource: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        timestamp: str | None = None,
        source: str | None = None,
        **extra_fields: Any,
    ) -> str:
        """Build Open Knowledge Format concept frontmatter."""
        lines = ["---", f"type: {self._quoted(concept_type)}"]

        if title:
            lines.append(f"title: {self._quoted(title)}")

        if description:
            lines.append(f"description: {self._quoted(description[:500])}")

        if resource:
            lines.append(f"resource: {self._inline(resource)}")

        if tags:
            lines.append("tags:")
            for tag in tags:
                lines.append(f"  - {self._quoted(tag)}")

        if timestamp:
            lines.append(f"timestamp: {self._inline(timestamp)}")

        if source:
            # docpull extension retained for compatibility with existing consumers.
            lines.append(f"source: {self._inline(source)}")

        for key, value in extra_fields.items():
            if value is not None:
                if isinstance(value, str):
                    lines.append(f"{key}: {self._quoted(value)}")
                elif isinstance(value, (list, tuple)):
                    lines.append(f"{key}:")
                    for item in value:
                        lines.append(f"  - {self._quoted(item)}")
                else:
                    lines.append(f"{key}: {self._inline(value)}")

        lines.append("---")
        return "\n".join(lines) + "\n\n"
