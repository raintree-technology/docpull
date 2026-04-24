"""Optional trafilatura-based content extractor.

Trafilatura is purpose-built for main-content extraction and generally beats
CSS-heuristic + html2text on noisy pages (blogs, marketing-heavy doc sites).
It is an optional dependency; import errors surface only when the extractor
is actually used.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class TrafilaturaExtractor:
    """Extract and convert to Markdown using trafilatura.

    Unlike the default extractor, this single class handles both extraction
    and Markdown conversion — trafilatura produces Markdown directly.

    Raises:
        ImportError: If trafilatura is not installed (install via ``pip
            install docpull[trafilatura]``).
    """

    name = "trafilatura"

    def __init__(self, include_links: bool = True, include_tables: bool = True) -> None:
        try:
            import trafilatura  # noqa: F401
        except ImportError as err:
            raise ImportError(
                "trafilatura extractor requires the 'trafilatura' package. "
                "Install it with: pip install docpull[trafilatura]"
            ) from err
        self._include_links = include_links
        self._include_tables = include_tables

    def extract(self, html: bytes, url: str) -> str:
        """Extract main content and return as Markdown.

        The return value is *Markdown*, not HTML — consumers should skip the
        HtmlToMarkdown converter when using this extractor.
        """
        import trafilatura

        try:
            text = html.decode("utf-8", errors="replace")
        except UnicodeDecodeError:
            text = html.decode("latin-1", errors="replace")

        result = trafilatura.extract(
            text,
            url=url,
            output_format="markdown",
            include_links=self._include_links,
            include_tables=self._include_tables,
            include_comments=False,
            include_formatting=True,
            favor_precision=True,
        )
        if result is None:
            logger.debug("trafilatura returned no content for %s", url)
            return ""
        return result.strip() + "\n"


__all__ = ["TrafilaturaExtractor"]
