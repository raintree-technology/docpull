"""NdjsonSaveStep - Stream documents to newline-delimited JSON.

NDJSON is the format of choice for agents that want to consume results while
a crawl is still running. Each line is a complete JSON object. The file can
also be written to stdout (``path="-"``) for direct piping into other tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import IO

from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class NdjsonSaveStep:
    """Stream one JSON object per page (or per chunk) to an NDJSON file.

    If ``filename`` is ``"-"`` the output goes to stdout, flushed after each
    record so an external process can consume it live.
    """

    name = "save_ndjson"

    def __init__(
        self,
        base_output_dir: Path,
        filename: str = "documents.ndjson",
        emit_chunks: bool = False,
    ) -> None:
        self._base_dir = base_output_dir.resolve()
        self._filename = filename
        self._emit_chunks = emit_chunks
        self._fp: IO[str] | None = None
        self._document_count = 0
        self._chunk_count = 0
        self._output_path: Path | None = None
        self._lock = asyncio.Lock()

    def _ensure_open(self) -> IO[str]:
        if self._fp is not None:
            return self._fp
        if self._filename == "-":
            self._fp = sys.stdout
            self._output_path = None
        else:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._output_path = self._base_dir / self._filename
            self._fp = self._output_path.open("w", encoding="utf-8")
        return self._fp

    def _write_record(self, record: dict[str, object]) -> None:
        fp = self._ensure_open()
        fp.write(json.dumps(record, ensure_ascii=False))
        fp.write("\n")
        fp.flush()

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        if ctx.should_skip or ctx.error or not ctx.markdown:
            return ctx

        base_record: dict[str, object] = {
            "url": ctx.url,
            "title": ctx.title,
            "source_type": ctx.source_type,
            "metadata": ctx.metadata,
            "fetched_at": datetime.now().isoformat(),
        }

        async with self._lock:
            if self._emit_chunks and ctx.chunks:
                for chunk in ctx.chunks:
                    record = dict(base_record)
                    record["chunk_index"] = getattr(chunk, "index", 0)
                    record["chunk_heading"] = getattr(chunk, "heading", None)
                    record["token_count"] = getattr(chunk, "token_count", None)
                    text = getattr(chunk, "text", "")
                    record["content"] = text
                    record["hash"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
                    self._write_record(record)
                    self._chunk_count += 1
            else:
                record = dict(base_record)
                record["content"] = ctx.markdown
                record["hash"] = hashlib.sha256(ctx.markdown.encode("utf-8")).hexdigest()
                self._write_record(record)
            self._document_count += 1

        if emit:
            emit(
                FetchEvent(
                    type=EventType.PAGE_SAVED,
                    url=ctx.url,
                    message=f"Streamed to NDJSON ({self._document_count} docs, {self._chunk_count} chunks)",
                )
            )
        return ctx

    def finalize(self) -> Path | None:
        """Close the file handle (if any) and return the output path."""
        if self._fp is not None and self._fp is not sys.stdout:
            try:
                self._fp.close()
            except Exception as err:  # noqa: BLE001
                logger.warning("Error closing NDJSON file: %s", err)
        self._fp = None
        if self._output_path:
            logger.info(
                "Wrote %d records (%d chunks) to %s",
                self._document_count,
                self._chunk_count,
                self._output_path,
            )
        return self._output_path

    @property
    def document_count(self) -> int:
        return self._document_count

    @property
    def chunk_count(self) -> int:
        return self._chunk_count
