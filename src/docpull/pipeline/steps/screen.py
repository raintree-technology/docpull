"""InjectionScreenStep - advisory prompt-injection screening of converted Markdown."""

from __future__ import annotations

import logging

from ...security.injection import screen_text
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class InjectionScreenStep:
    """
    Pipeline step that labels converted Markdown with an advisory trust label.

    Runs after conversion (needs ``ctx.markdown``) and before the save steps
    so the label reaches manifest records via ``ctx.metadata``. Every screened
    page gets ``ctx.metadata["injection_screen"]`` (clean pages included);
    full spans go to ``ctx.metadata["injection_screen_spans"]`` only when the
    page is suspicious, keeping manifests lean.

    The label is advisory metadata, never a block: this step never skips or
    fails a page, no matter what the screen finds.
    """

    name = "injection_screen"

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        if ctx.should_skip or ctx.error or ctx.markdown is None:
            return ctx

        result = screen_text(ctx.markdown)
        ctx.metadata["injection_screen"] = result.summary()
        if result.spans:
            ctx.metadata["injection_screen_spans"] = result.span_dicts()
            logger.debug(
                "Injection screen flagged %s: %d span(s) in families %s",
                ctx.url,
                len(result.spans),
                ", ".join(result.families),
            )
        return ctx
