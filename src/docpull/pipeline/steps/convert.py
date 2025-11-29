"""Pipeline step for HTML to Markdown conversion."""

import logging
from typing import Optional

from ...conversion.extractor import MainContentExtractor
from ...conversion.markdown import FrontmatterBuilder, HtmlToMarkdown
from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class ConvertStep:
    """
    Pipeline step that converts HTML to Markdown.

    Extracts main content from HTML, converts to Markdown,
    and optionally adds YAML frontmatter.

    Example:
        step = ConvertStep(add_frontmatter=True)
        ctx = await step.execute(ctx, emit=callback)
        # ctx.markdown now contains the converted content
    """

    name = "convert"

    def __init__(
        self,
        extractor: Optional[MainContentExtractor] = None,
        converter: Optional[HtmlToMarkdown] = None,
        add_frontmatter: bool = True,
    ):
        """
        Initialize the convert step.

        Args:
            extractor: Content extractor (uses default if None)
            converter: Markdown converter (uses default if None)
            add_frontmatter: Whether to add YAML frontmatter
        """
        self._extractor = extractor or MainContentExtractor()
        self._converter = converter or HtmlToMarkdown()
        self._add_frontmatter = add_frontmatter
        self._frontmatter_builder = FrontmatterBuilder() if add_frontmatter else None

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Convert HTML content to Markdown.

        Reads from ctx.html, writes to ctx.markdown.

        Args:
            ctx: Page context with HTML content
            emit: Optional event emitter

        Returns:
            Updated context with markdown content
        """
        if ctx.should_skip or ctx.error:
            return ctx

        if ctx.html is None:
            ctx.error = "No HTML content to convert"
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        error=ctx.error,
                    )
                )
            return ctx

        try:
            # Extract main content
            extracted_html = self._extractor.extract(ctx.html, ctx.url)

            if not extracted_html.strip():
                logger.warning(f"No content extracted from {ctx.url}")
                ctx.should_skip = True
                ctx.skip_reason = "No content extracted"
                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=ctx.url,
                            message=ctx.skip_reason,
                        )
                    )
                return ctx

            # Convert to markdown
            markdown = self._converter.convert(extracted_html, ctx.url)

            # Add frontmatter if enabled
            if self._add_frontmatter and self._frontmatter_builder:
                frontmatter = self._frontmatter_builder.build(
                    title=ctx.title,
                    url=ctx.url,
                )
                markdown = frontmatter + markdown

            ctx.markdown = markdown

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.PAGE_CONVERTED,
                        url=ctx.url,
                        message=f"Converted to {len(markdown)} bytes of Markdown",
                    )
                )

            logger.debug(f"Converted {ctx.url} to {len(markdown)} bytes of Markdown")
            return ctx

        except Exception as e:
            logger.error(f"Conversion failed for {ctx.url}: {e}")
            ctx.error = f"Conversion failed: {e}"
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        error=ctx.error,
                    )
                )
            return ctx
