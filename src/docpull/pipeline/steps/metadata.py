"""Pipeline step for metadata extraction."""

import logging
from typing import Optional

from bs4 import BeautifulSoup, Tag

from ...metadata_extractor import RichMetadataExtractor
from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class MetadataStep:
    """
    Pipeline step that extracts metadata from HTML.

    Extracts title, description, and optionally rich metadata
    (Open Graph, JSON-LD, microdata).

    Example:
        step = MetadataStep(extract_rich=True)
        ctx = await step.execute(ctx, emit=callback)
        # ctx.title, ctx.metadata now populated
    """

    name = "metadata"

    def __init__(
        self,
        extract_rich: bool = False,
    ):
        """
        Initialize the metadata step.

        Args:
            extract_rich: Whether to extract rich metadata (OG, JSON-LD, etc.)
        """
        self._extract_rich = extract_rich
        self._rich_extractor = RichMetadataExtractor() if extract_rich else None

    def _extract_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract page title from HTML."""
        # Try og:title first
        og_title = soup.find("meta", property="og:title")
        if isinstance(og_title, Tag) and og_title.get("content"):
            return str(og_title["content"]).strip()

        # Then standard title tag
        title_tag = soup.find("title")
        if isinstance(title_tag, Tag) and title_tag.string:
            return title_tag.string.strip()

        # Finally h1
        h1 = soup.find("h1")
        if isinstance(h1, Tag):
            return h1.get_text(strip=True)

        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract page description from HTML."""
        # Try og:description first
        og_desc = soup.find("meta", property="og:description")
        if isinstance(og_desc, Tag) and og_desc.get("content"):
            return str(og_desc["content"]).strip()

        # Then meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if isinstance(meta_desc, Tag) and meta_desc.get("content"):
            return str(meta_desc["content"]).strip()

        return None

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Extract metadata from HTML content.

        Reads from ctx.html, populates ctx.title, ctx.metadata.

        Args:
            ctx: Page context with HTML content
            emit: Optional event emitter

        Returns:
            Updated context with metadata
        """
        if ctx.should_skip or ctx.error:
            return ctx

        if ctx.html is None:
            # No HTML, can't extract metadata
            return ctx

        try:
            # Parse HTML
            soup = BeautifulSoup(ctx.html, "html.parser")

            # Extract basic metadata
            ctx.title = self._extract_title(soup)
            description = self._extract_description(soup)

            # Initialize metadata dict
            if ctx.metadata is None:
                ctx.metadata = {}

            if description:
                ctx.metadata["description"] = description

            # Extract rich metadata if enabled
            if self._extract_rich and self._rich_extractor:
                html_str = ctx.html.decode("utf-8", errors="replace")
                rich_meta = self._rich_extractor.extract(html_str, ctx.url)
                ctx.metadata.update(self._rich_extractor.merge_with_fallback(rich_meta, ctx.title))
                # Update title from rich metadata if better
                if not ctx.title and ctx.metadata.get("title"):
                    ctx.title = ctx.metadata["title"]

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.METADATA_EXTRACTED,
                        url=ctx.url,
                        message=f"Extracted metadata: title='{ctx.title or 'None'}'",
                    )
                )

            logger.debug(f"Extracted metadata for {ctx.url}: title='{ctx.title}'")
            return ctx

        except Exception as e:
            logger.warning(f"Failed to extract metadata from {ctx.url}: {e}")
            # Non-fatal, continue pipeline
            return ctx
