"""Open Knowledge Format save step."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ...conversion.markdown import FrontmatterBuilder
from ...conversion.special_cases import _split_markdown_frontmatter
from ...models.document import DocumentRecord
from ...models.events import EventType, FetchEvent, SkipReason
from ...models.run import RunIdentity
from ...output_contract import document_context_fields
from ..base import EventEmitter, PageContext
from ..manifest import CorpusManifest
from .convert import _extract_headings

logger = logging.getLogger(__name__)

OKF_CONCEPT_TYPE = "Web Page"
OKF_CHUNK_TYPE = "Web Page Chunk"
_INDEX_FILENAME = "index.md"


@dataclass(frozen=True)
class OkfIndexEntry:
    """One generated OKF concept entry for directory indexes."""

    relative_path: str
    title: str
    description: str | None = None


class OkfSaveStep:
    """Save converted Markdown as an OKF bundle."""

    name = "save_okf"

    def __init__(
        self,
        base_output_dir: Path,
        *,
        emit_chunks: bool = False,
        run_identity: RunIdentity | None = None,
    ) -> None:
        self._base_output_dir = base_output_dir
        self._emit_chunks = emit_chunks
        self._run_identity = run_identity
        self._frontmatter_builder = FrontmatterBuilder()
        self._manifest = CorpusManifest(
            base_output_dir,
            output_format="okf",
            run_identity=run_identity,
        )
        self._index_entries: list[OkfIndexEntry] = []
        self._seen_entries: set[str] = set()

    def _validate_output_path(self, output_path: Path) -> Path:
        resolved = output_path.resolve()
        base_resolved = self._base_output_dir.resolve()
        try:
            resolved.relative_to(base_resolved)
        except ValueError as err:
            raise ValueError(f"Output path {resolved} is outside base directory {base_resolved}") from err
        return resolved

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        if ctx.markdown is not None:
            body = ctx.markdown
        elif ctx.html is not None:
            body = ctx.html.decode("utf-8", errors="replace")
        else:
            ctx.mark_skipped("No content to save", SkipReason.NO_CONTENT_TO_SAVE)
            logger.warning("Skipping %s: no content to save", ctx.url)
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=ctx.url,
                        message="No content to save",
                        skip_reason=SkipReason.NO_CONTENT_TO_SAVE,
                    )
                )
            return ctx

        try:
            validated_path = self._validate_output_path(ctx.output_path)
            validated_path.parent.mkdir(parents=True, exist_ok=True)

            if self._emit_chunks and ctx.chunks:
                await self._write_chunks(ctx, validated_path)
            else:
                content = self._render_concept(ctx, body)
                await asyncio.to_thread(validated_path.write_text, content, encoding="utf-8")
                ctx.markdown = content
                ctx.persisted_path = validated_path
                record = DocumentRecord.from_page(
                    url=ctx.url,
                    title=ctx.title,
                    content=content,
                    metadata=ctx.metadata,
                    extraction=ctx.extraction_info,
                    source_type=ctx.source_type,
                    run_identity=self._run_identity,
                    **document_context_fields(ctx, output_format="okf"),
                )
                self._manifest.add_record(record, validated_path)
                self._add_index_entry(validated_path, self._entry_title(ctx), self._description(ctx.metadata))
                logger.info("Saved OKF concept: %s", validated_path)

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.PAGE_SAVED,
                        url=ctx.url,
                        output_path=validated_path,
                        message=f"Saved OKF concept to {validated_path}",
                    )
                )
            return ctx

        except ValueError as e:
            ctx.mark_failed(str(e))
            logger.error("Path validation failed for %s: %s", ctx.url, e)
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        error=str(e),
                        output_path=ctx.output_path,
                        message=f"Path validation failed: {e}",
                    )
                )
            raise
        except OSError as e:
            ctx.mark_failed(f"Failed to save OKF concept: {e}")
            logger.error("Failed to save %s to %s: %s", ctx.url, ctx.output_path, e)
            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        output_path=ctx.output_path,
                        error=str(e),
                        message=f"Failed to save OKF concept: {e}",
                    )
                )
            raise

    async def _write_chunks(
        self,
        ctx: PageContext,
        validated_path: Path,
    ) -> None:
        stem = validated_path.stem
        parent = validated_path.parent
        ext = validated_path.suffix or ".md"
        width = max(2, len(str(len(ctx.chunks) - 1)))
        first_chunk_path: Path | None = None
        for chunk in ctx.chunks:
            idx = getattr(chunk, "index", 0)
            text = getattr(chunk, "text", "")
            heading = getattr(chunk, "heading", None)
            title = self._entry_title(ctx, suffix=f"chunk {idx + 1}")
            content = self._render_concept(
                ctx,
                text,
                concept_type=OKF_CHUNK_TYPE,
                chunk_index=idx,
                chunk_heading=heading,
                token_count=getattr(chunk, "token_count", None),
            )
            chunk_path = parent / f"{stem}.{idx:0{width}d}{ext}"
            await asyncio.to_thread(chunk_path.write_text, content, encoding="utf-8")
            record = DocumentRecord.from_page(
                url=ctx.url,
                title=ctx.title,
                content=content,
                metadata=ctx.metadata,
                extraction=ctx.extraction_info,
                source_type=ctx.source_type,
                run_identity=self._run_identity,
                **document_context_fields(ctx, output_format="okf"),
                chunk_index=idx,
                chunk_heading=heading,
                token_count=getattr(chunk, "token_count", None),
            )
            self._manifest.add_record(record, chunk_path)
            self._add_index_entry(chunk_path, title, heading or self._description(ctx.metadata))
            if first_chunk_path is None:
                first_chunk_path = chunk_path
        ctx.persisted_path = first_chunk_path
        logger.info("Saved %d OKF chunks: %s.*%s", len(ctx.chunks), parent / stem, ext)

    def _render_concept(
        self,
        ctx: PageContext,
        body: str,
        *,
        concept_type: str = OKF_CONCEPT_TYPE,
        chunk_index: int | None = None,
        chunk_heading: str | None = None,
        token_count: int | None = None,
    ) -> str:
        _, stripped_body = _split_markdown_frontmatter(body)
        stripped_body = stripped_body.strip()
        metadata = ctx.metadata or {}
        extra = self._extra_fields(ctx, stripped_body)
        if chunk_index is not None:
            extra["chunk_index"] = chunk_index
        if chunk_heading:
            extra["chunk_heading"] = chunk_heading
        if token_count is not None:
            extra["token_count"] = token_count
        frontmatter = self._frontmatter_builder.build_okf(
            concept_type=concept_type,
            title=ctx.title,
            resource=ctx.url,
            description=self._description(metadata),
            tags=self._tags(metadata),
            timestamp=self._timestamp(metadata),
            source=ctx.url,
            **extra,
        )
        return frontmatter + stripped_body + "\n"

    def _extra_fields(self, ctx: PageContext, body: str) -> dict[str, Any]:
        metadata = ctx.metadata or {}
        extra: dict[str, Any] = {}
        if ctx.source_type and ctx.source_type != "generic":
            extra["source_type"] = ctx.source_type
        for key in ("author", "section", "canonical_url", "site_name", "framework"):
            value = metadata.get(key)
            if value:
                extra[key] = value
        headings = _extract_headings(body)
        if headings:
            extra["headings"] = headings
        return extra

    @staticmethod
    def _description(metadata: dict[str, Any]) -> str | None:
        value = metadata.get("description")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _timestamp(metadata: dict[str, Any]) -> str | None:
        for key in ("modified_time", "published_time"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _tags(metadata: dict[str, Any]) -> list[str] | None:
        raw = metadata.get("tags") or metadata.get("keywords")
        values: list[str]
        if isinstance(raw, str):
            values = [part.strip() for part in raw.split(",")]
        elif isinstance(raw, (list, tuple, set)):
            values = [str(part).strip() for part in raw]
        else:
            return None
        seen: set[str] = set()
        tags: list[str] = []
        for value in values:
            if not value or value in seen:
                continue
            seen.add(value)
            tags.append(value)
        return tags or None

    def _entry_title(self, ctx: PageContext, suffix: str | None = None) -> str:
        title = ctx.title or Path(ctx.output_path).stem.replace("_", " ").strip() or "Untitled"
        return f"{title} ({suffix})" if suffix else title

    @staticmethod
    def _index_text(value: str | None) -> str:
        if not value:
            return ""
        return str(value).replace("\r", " ").replace("\n", " ").replace("\x00", " ").strip()

    @classmethod
    def _link_label(cls, value: str) -> str:
        return cls._index_text(value).replace("[", r"\[").replace("]", r"\]")

    def _add_index_entry(self, output_path: Path, title: str, description: str | None) -> None:
        relative_path = output_path.resolve().relative_to(self._base_output_dir.resolve()).as_posix()
        if relative_path in self._seen_entries:
            return
        self._seen_entries.add(relative_path)
        self._index_entries.append(
            OkfIndexEntry(
                relative_path=relative_path,
                title=title,
                description=description,
            )
        )

    def finalize(self) -> Path:
        """Write the corpus manifest and generated OKF index files."""
        manifest_path = self._manifest.finalize()
        self._write_indexes()
        return manifest_path

    def _write_indexes(self) -> None:
        if not self._index_entries:
            return

        dirs: set[PurePosixPath] = {PurePosixPath(".")}
        for entry in self._index_entries:
            parent = PurePosixPath(entry.relative_path).parent
            dirs.add(parent)
            while str(parent) not in {"", "."}:
                parent = parent.parent
                dirs.add(parent)

        for directory in sorted(dirs, key=lambda item: item.as_posix()):
            content = self._render_index(directory)
            if str(directory) == ".":
                content = '---\nokf_version: "0.1"\n---\n\n' + content
            relative_dir = Path() if str(directory) == "." else Path(directory.as_posix())
            index_path = self._base_output_dir / relative_dir
            index_path.mkdir(parents=True, exist_ok=True)
            (index_path / _INDEX_FILENAME).write_text(content, encoding="utf-8")

    def _render_index(self, directory: PurePosixPath) -> str:
        direct_entries: list[OkfIndexEntry] = []
        subdirs: dict[str, int] = {}
        for entry in self._index_entries:
            path = PurePosixPath(entry.relative_path)
            parent = path.parent
            if parent == directory:
                direct_entries.append(entry)
                continue
            try:
                relative = path.relative_to(directory)
            except ValueError:
                continue
            parts = relative.parts
            if len(parts) > 1:
                subdirs[parts[0]] = subdirs.get(parts[0], 0) + 1

        lines: list[str] = []
        if direct_entries:
            lines.extend(["# Concepts", ""])
            for entry in sorted(direct_entries, key=lambda item: item.title.lower()):
                filename = PurePosixPath(entry.relative_path).name
                label = self._link_label(entry.title)
                description_text = self._index_text(entry.description)
                description = f" - {description_text}" if description_text else ""
                lines.append(f"* [{label}]({filename}){description}")
            lines.append("")

        if subdirs:
            lines.extend(["# Directories", ""])
            for name, count in sorted(subdirs.items()):
                label = self._link_label(name.replace("_", " "))
                noun = "concept" if count == 1 else "concepts"
                lines.append(f"* [{label}]({name}/) - {count} {noun}")
            lines.append("")

        if not lines:
            lines = ["# Concepts", ""]
        return "\n".join(lines).rstrip() + "\n"
