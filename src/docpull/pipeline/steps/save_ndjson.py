"""NdjsonSaveStep - Stream documents to newline-delimited JSON.

NDJSON is the format of choice for agents that want to consume results while
a crawl is still running. Each line is a complete JSON object. The file can
also be written to stdout (``path="-"``) for direct piping into other tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from ...models.document import DocumentRecord
from ...models.events import EventType, FetchEvent
from ...models.run import RunIdentity
from ..base import EventEmitter, PageContext
from ..manifest import CorpusManifest

logger = logging.getLogger(__name__)


@dataclass
class _SourceIndexEntry:
    url: str
    title: str | None = None
    source_type: str | None = None
    record_count: int = 0
    token_count: int = 0


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
        run_identity: RunIdentity | None = None,
    ) -> None:
        self._base_dir = base_output_dir.resolve()
        self._filename = filename
        self._emit_chunks = emit_chunks
        self._fp: IO[str] | None = None
        self._document_count = 0
        self._chunk_count = 0
        self._output_path: Path | None = None
        self._lock = asyncio.Lock()
        self._run_identity = run_identity
        self._source_index: dict[str, _SourceIndexEntry] = {}
        self._manifest = CorpusManifest(
            self._base_dir,
            output_format="ndjson",
            run_identity=run_identity,
        )

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
        if "content_hash" in record and "hash" not in record:
            record["hash"] = record["content_hash"]
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

        async with self._lock:
            self._ensure_open()
            manifest_output_path: Path | str | None = (
                self._output_path if self._output_path is not None else "-"
            )
            if self._emit_chunks and ctx.chunks:
                for chunk in ctx.chunks:
                    text = getattr(chunk, "text", "")
                    record = DocumentRecord.from_page(
                        url=ctx.url,
                        title=ctx.title,
                        content=text,
                        metadata=ctx.metadata,
                        extraction=ctx.extraction_info,
                        source_type=ctx.source_type,
                        run_identity=self._run_identity,
                        chunk_index=getattr(chunk, "index", 0),
                        chunk_heading=getattr(chunk, "heading", None),
                        token_count=getattr(chunk, "token_count", None),
                    )
                    self._manifest.add_record(record, manifest_output_path)
                    self._add_source_index_record(record)
                    self._write_record(record.model_dump(mode="json", exclude_none=True))
                    self._chunk_count += 1
            else:
                record = DocumentRecord.from_page(
                    url=ctx.url,
                    title=ctx.title,
                    content=ctx.markdown,
                    metadata=ctx.metadata,
                    extraction=ctx.extraction_info,
                    source_type=ctx.source_type,
                    run_identity=self._run_identity,
                )
                self._manifest.add_record(record, manifest_output_path)
                self._add_source_index_record(record)
                self._write_record(record.model_dump(mode="json", exclude_none=True))
            self._document_count += 1
            ctx.persisted_path = self._output_path

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
        self._manifest.finalize()
        if self._output_path:
            self._write_sources_index()
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

    def _add_source_index_record(self, record: DocumentRecord) -> None:
        entry = self._source_index.setdefault(
            record.url,
            _SourceIndexEntry(
                url=record.url,
                title=record.title,
                source_type=record.source_type,
            ),
        )
        if not entry.title and record.title:
            entry.title = record.title
        if not entry.source_type and record.source_type:
            entry.source_type = record.source_type
        entry.record_count += 1
        entry.token_count += record.token_count or 0

    def _write_sources_index(self) -> Path:
        if self._output_path is None:
            raise RuntimeError("NDJSON output path is not initialized.")
        lines = [
            "# Context Pack Sources",
            "",
            f"Generated from `{self._filename}`.",
            "",
            "## Sources",
            "",
        ]
        if not self._source_index:
            lines.append("_No records were emitted._")
        for index, entry in enumerate(self._source_index.values(), start=1):
            title = entry.title or entry.url
            lines.append(f"{index}. [{title}]({entry.url})")
            lines.append(f"   - Records: {entry.record_count}")
            if entry.token_count:
                lines.append(f"   - Tokens: {entry.token_count}")
            if entry.source_type:
                lines.append(f"   - Source type: `{entry.source_type}`")
            lines.append(f"   - Records file: `{self._filename}`")
        path = self._base_dir / "sources.md"
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return path
