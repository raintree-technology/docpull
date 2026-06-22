"""Pipeline step for optional local browser rendering."""

from __future__ import annotations

import logging
from pathlib import Path

from ...conversion.special_cases import looks_like_spa
from ...models.config import RenderConfig
from ...models.events import EventType, FetchEvent
from ...rendering import (
    Renderer,
    append_rendered_page_record,
    render_metadata,
    render_url,
)
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class RenderStep:
    """Render a page through an explicit local browser backend."""

    name = "render"

    def __init__(
        self,
        *,
        render_config: RenderConfig,
        output_dir: Path,
        renderer: Renderer | None = None,
    ) -> None:
        self._config = render_config
        self._output_dir = output_dir
        self._renderer = renderer

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        if ctx.should_skip or ctx.error or not self._config.enabled:
            return ctx

        if self._config.mode == "fallback" and ctx.html is not None and not looks_like_spa(ctx.html):
            return ctx

        page = await render_url(ctx.url, config=self._config, renderer=self._renderer)
        ctx.html = page.html
        ctx.content_type = "text/html; charset=utf-8"
        ctx.status_code = ctx.status_code or 200
        ctx.bytes_downloaded = page.html_bytes

        metadata = render_metadata(page, self._config)
        ctx.metadata["rendered"] = True
        ctx.metadata["render"] = metadata
        ctx.extraction_info["render"] = {
            "backend": page.backend,
            "html_sha256": page.html_sha256,
            "html_bytes": page.html_bytes,
            "mode": self._config.mode,
        }
        append_rendered_page_record(
            self._output_dir,
            page,
            self._config,
            source="fetch_pipeline",
        )
        logger.debug("Rendered %s via %s (%d bytes)", ctx.url, page.backend, page.html_bytes)

        if emit:
            emit(
                FetchEvent(
                    type=EventType.PAGE_RENDERED,
                    url=ctx.url,
                    bytes_downloaded=page.html_bytes,
                    content_type=ctx.content_type,
                    message=f"Rendered {page.html_bytes} bytes of HTML",
                )
            )
        return ctx
