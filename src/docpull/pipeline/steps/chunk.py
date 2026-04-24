"""Pipeline step for token-aware Markdown chunking."""

from __future__ import annotations

import logging
from typing import Optional

from ...conversion.chunking import TokenCounter, chunk_markdown
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class ChunkStep:
    """Split the converted Markdown into token-bounded chunks.

    Populates ``ctx.chunks`` with ``Chunk`` objects. Downstream save steps
    decide whether to emit one file per chunk or keep the full document.
    """

    name = "chunk"

    def __init__(
        self,
        max_tokens: int = 4000,
        tokenizer: str = "cl100k_base",
        counter: TokenCounter | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._counter = counter or TokenCounter(encoding=tokenizer)

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        if ctx.should_skip or ctx.error or not ctx.markdown:
            return ctx
        try:
            chunks = chunk_markdown(
                ctx.markdown,
                max_tokens=self._max_tokens,
                counter=self._counter,
            )
        except Exception as err:  # noqa: BLE001
            logger.warning("Chunking failed for %s: %s", ctx.url, err)
            return ctx
        ctx.chunks = list(chunks)
        logger.debug("Chunked %s into %d chunks", ctx.url, len(chunks))
        return ctx

    @property
    def tokenizer_exact(self) -> bool:
        return self._counter.exact
