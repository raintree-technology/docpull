"""Pipeline step for HTML to Markdown conversion."""

import logging
from typing import Optional

from ...conversion.extractor import MainContentExtractor
from ...conversion.markdown import FrontmatterBuilder, HtmlToMarkdown
from ...conversion.special_cases import (
    DEFAULT_CHAIN,
    SpecialCaseExtractor,
    detect_source_type,
    looks_like_spa,
    looks_like_spa_output,
)
from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class ConvertStep:
    """Convert HTML (or structured JSON feeds) to Markdown.

    Runs a chain of framework-specific fast extractors first (Next.js
    ``__NEXT_DATA__``, OpenAPI JSON, Mintlify, etc.) and falls back to the
    generic CSS-heuristic extractor + html2text. Optionally detects SPA pages
    that would otherwise produce empty Markdown and fails loud.
    """

    name = "convert"

    def __init__(
        self,
        extractor: Optional[MainContentExtractor] = None,
        converter: Optional[HtmlToMarkdown] = None,
        add_frontmatter: bool = True,
        special_cases: Optional[list[SpecialCaseExtractor]] = None,
        enable_special_cases: bool = True,
        use_trafilatura: bool = False,
        strict_js_required: bool = False,
    ):
        """Initialize the convert step.

        Args:
            extractor: Content extractor (defaults to ``MainContentExtractor``).
            converter: Markdown converter (defaults to ``HtmlToMarkdown``).
            add_frontmatter: Whether to prepend YAML frontmatter.
            special_cases: Custom extractor chain (defaults to ``DEFAULT_CHAIN``).
            enable_special_cases: Run framework-specific extractors first.
            use_trafilatura: Use the optional trafilatura extractor instead
                of the default one. Requires ``pip install docpull[trafilatura]``.
            strict_js_required: If True, a page that appears to be a JS-only
                SPA and produces empty content raises (ctx.error) instead of
                silently skipping.
        """
        self._add_frontmatter = add_frontmatter
        self._frontmatter_builder = FrontmatterBuilder() if add_frontmatter else None
        self._enable_special_cases = enable_special_cases
        self._special_cases = special_cases if special_cases is not None else list(DEFAULT_CHAIN)
        self._strict_js_required = strict_js_required
        self._use_trafilatura = use_trafilatura

        if use_trafilatura:
            from ...conversion.trafilatura_extractor import TrafilaturaExtractor

            self._trafilatura = TrafilaturaExtractor()
            self._extractor = None
            self._converter = None
        else:
            self._trafilatura = None
            self._extractor = extractor or MainContentExtractor()
            self._converter = converter or HtmlToMarkdown()

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        if ctx.should_skip or ctx.error:
            return ctx

        if ctx.html is None:
            ctx.error = "No HTML content to convert"
            if emit:
                emit(FetchEvent(type=EventType.FETCH_FAILED, url=ctx.url, error=ctx.error))
            return ctx

        try:
            ctx.source_type = detect_source_type(ctx.html, ctx.url)

            special_markdown = self._try_special_cases(ctx)
            if special_markdown is not None:
                markdown = special_markdown
            elif self._trafilatura is not None:
                markdown = self._trafilatura.extract(ctx.html, ctx.url)
            else:
                assert self._extractor is not None
                assert self._converter is not None
                extracted_html = self._extractor.extract(ctx.html, ctx.url)
                if not extracted_html.strip():
                    return self._handle_empty_content(ctx, emit)
                markdown = self._converter.convert(extracted_html, ctx.url)

            if not markdown or not markdown.strip():
                return self._handle_empty_content(ctx, emit)

            # Post-conversion SPA check: the extractor may have produced a
            # "Loading..." shell. Treat as empty if so.
            if looks_like_spa_output(markdown):
                return self._handle_empty_content(ctx, emit)

            if self._add_frontmatter and self._frontmatter_builder:
                extra: dict[str, object] = {}
                if ctx.source_type and ctx.source_type != "generic":
                    extra["source_type"] = ctx.source_type
                frontmatter = self._frontmatter_builder.build(
                    title=ctx.title,
                    url=ctx.url,
                    **extra,
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
            logger.debug("Converted %s to %d bytes of Markdown", ctx.url, len(markdown))
            return ctx

        except Exception as e:  # noqa: BLE001
            logger.error("Conversion failed for %s: %s", ctx.url, e)
            ctx.error = f"Conversion failed: {e}"
            if emit:
                emit(FetchEvent(type=EventType.FETCH_FAILED, url=ctx.url, error=ctx.error))
            return ctx

    def _try_special_cases(self, ctx: PageContext) -> str | None:
        if not self._enable_special_cases or ctx.html is None:
            return None
        for extractor in self._special_cases:
            try:
                result = extractor.try_extract(ctx.html, ctx.url)
            except Exception as err:  # noqa: BLE001
                logger.debug("Special-case extractor %s raised: %s", extractor.name, err)
                continue
            if result is None:
                continue
            ctx.source_type = result.source_type
            if result.title and not ctx.title:
                ctx.title = result.title
            if result.extra:
                for k, v in result.extra.items():
                    ctx.metadata.setdefault(k, v)
            logger.debug("Special-case %s matched for %s", extractor.name, ctx.url)
            return result.markdown
        return None

    def _handle_empty_content(self, ctx: PageContext, emit: Optional[EventEmitter]) -> PageContext:
        is_spa = ctx.html is not None and looks_like_spa(ctx.html)
        if self._strict_js_required and is_spa:
            ctx.error = (
                "Page appears to be a JavaScript-only SPA; rendered content was empty. "
                "docpull does not execute JavaScript. Either disable --strict-js-required "
                "or fetch a server-rendered mirror."
            )
            if emit:
                emit(FetchEvent(type=EventType.FETCH_FAILED, url=ctx.url, error=ctx.error))
            return ctx
        ctx.should_skip = True
        ctx.skip_reason = "JS-only SPA: no content without JS render" if is_spa else "No content extracted"
        if is_spa:
            logger.warning("Likely JS-only SPA at %s (no server-rendered content)", ctx.url)
        else:
            logger.warning("No content extracted from %s", ctx.url)
        if emit:
            emit(
                FetchEvent(
                    type=EventType.FETCH_SKIPPED,
                    url=ctx.url,
                    message=ctx.skip_reason,
                )
            )
        return ctx
