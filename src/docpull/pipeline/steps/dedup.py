"""Pipeline step for content deduplication using StreamingDeduplicator."""

import logging
from typing import Optional, Union

from ...cache import StreamingDeduplicator
from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class DedupStep:
    """
    Pipeline step for streaming content deduplication.

    Uses the StreamingDeduplicator from the cache module for consistent
    hashing across the entire caching system.

    Example:
        dedup = StreamingDeduplicator()
        step = DedupStep(deduplicator=dedup)
        ctx = await step.execute(ctx, emit=callback)
        # ctx.should_skip = True if duplicate
    """

    name = "dedup"

    def __init__(
        self,
        deduplicator: Optional[StreamingDeduplicator] = None,
        hash_markdown: bool = True,
    ):
        """
        Initialize the dedup step.

        Args:
            deduplicator: StreamingDeduplicator instance (creates new if None)
            hash_markdown: If True, hash markdown content; if False, hash raw HTML
        """
        self._deduplicator = deduplicator or StreamingDeduplicator()
        self._hash_markdown = hash_markdown

    @property
    def deduplicator(self) -> StreamingDeduplicator:
        """Get the streaming deduplicator."""
        return self._deduplicator

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Check content for duplicates.

        Args:
            ctx: Page context with content
            emit: Optional event emitter

        Returns:
            Updated context (may have should_skip=True)
        """
        if ctx.should_skip or ctx.error:
            return ctx

        # Get content to hash
        content: Union[str, bytes]
        if self._hash_markdown and ctx.markdown:
            content = ctx.markdown
        elif ctx.html:
            content = ctx.html
        else:
            # No content to check
            return ctx

        # Check for duplicate using StreamingDeduplicator
        should_save, duplicate_of = await self._deduplicator.check_and_register(ctx.url, content)

        if not should_save and duplicate_of:
            ctx.should_skip = True
            ctx.skip_reason = f"Duplicate of {duplicate_of}"

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.PAGE_DEDUPLICATED,
                        url=ctx.url,
                        duplicate_of=duplicate_of,
                        message=f"Duplicate content (original: {duplicate_of})",
                    )
                )

            logger.debug(f"Duplicate detected: {ctx.url} -> {duplicate_of}")

        return ctx
