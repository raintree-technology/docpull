"""SaveStep - File saving pipeline step."""

import asyncio
import logging
from pathlib import Path

from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class SaveStep:
    """
    Pipeline step that saves content to a file.

    Writes ctx.markdown (preferred) or raw ctx.html to ctx.output_path.
    Creates parent directories as needed.

    Example:
        save_step = SaveStep()

        ctx = await save_step.execute(ctx)
        if not ctx.should_skip:
            print(f"Saved to {ctx.output_path}")
    """

    name = "save"

    def __init__(
        self,
        base_output_dir: Path | None = None,
        emit_chunks: bool = False,
        skill_name: str | None = None,
        skill_description: str | None = None,
    ) -> None:
        """
        Initialize the save step.

        Args:
            base_output_dir: Optional base directory for output path validation.
                            If set, output paths must be within this directory.
            emit_chunks: When True and ``ctx.chunks`` is populated, write one
                file per chunk (``<stem>.<NN>.md``) rather than the full doc.
            skill_name: When set, ``finalize()`` writes a ``SKILL.md``
                manifest into ``base_output_dir`` so the directory loads
                as a Claude Code skill without manual editing.
            skill_description: Optional explicit ``description:`` field
                for the SKILL manifest. If None, derived from the first
                page's metadata.
        """
        self._base_output_dir = base_output_dir
        self._emit_chunks = emit_chunks
        self._skill_name = skill_name
        self._skill_description = skill_description
        self._first_metadata: dict[str, object] | None = None
        self._first_title: str | None = None

    def _validate_output_path(self, output_path: Path) -> Path:
        """
        Validate that output path is safe.

        Args:
            output_path: The path to validate

        Returns:
            Resolved absolute path

        Raises:
            ValueError: If path is outside base directory (if configured)
        """
        resolved = output_path.resolve()

        if self._base_output_dir is not None:
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
        """
        Execute the save step.

        Args:
            ctx: Page context with content to save
            emit: Optional callback to emit events

        Returns:
            PageContext (unchanged, or with error set)
        """
        url = ctx.url
        output_path = ctx.output_path

        # Determine content to save
        if ctx.markdown is not None:
            content = ctx.markdown
        elif ctx.html is not None:
            # Fall back to raw HTML if no markdown conversion
            content = ctx.html.decode("utf-8", errors="replace")
        else:
            ctx.should_skip = True
            ctx.skip_reason = "No content to save"
            logger.warning(f"Skipping {url}: no content to save")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        message="No content to save",
                    )
                )
            return ctx

        try:
            # Validate output path
            validated_path = self._validate_output_path(output_path)

            # Ensure parent directory exists
            validated_path.parent.mkdir(parents=True, exist_ok=True)

            # Write chunked output if requested and available
            if self._emit_chunks and ctx.chunks:
                stem = validated_path.stem
                parent = validated_path.parent
                ext = validated_path.suffix or ".md"
                width = max(2, len(str(len(ctx.chunks) - 1)))
                for chunk in ctx.chunks:
                    idx = getattr(chunk, "index", 0)
                    text = getattr(chunk, "text", "")
                    chunk_path = parent / f"{stem}.{idx:0{width}d}{ext}"
                    await asyncio.to_thread(chunk_path.write_text, text, encoding="utf-8")
                logger.info("Saved %d chunks: %s.*%s", len(ctx.chunks), parent / stem, ext)
            else:
                # Write full document (use asyncio.to_thread to avoid blocking)
                await asyncio.to_thread(
                    validated_path.write_text,
                    content,
                    encoding="utf-8",
                )
                logger.info(f"Saved: {validated_path}")

            # Snapshot the first successful page's metadata for SKILL.md.
            if self._skill_name and self._first_metadata is None:
                self._first_metadata = dict(ctx.metadata or {})
                self._first_title = ctx.title

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.PAGE_SAVED,
                        url=url,
                        output_path=validated_path,
                        message=f"Saved to {validated_path}",
                    )
                )

            return ctx

        except ValueError as e:
            # Path validation error
            ctx.error = str(e)
            ctx.should_skip = True
            logger.error(f"Path validation failed for {url}: {e}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=url,
                        error=str(e),
                        message=f"Path validation failed: {e}",
                    )
                )
            raise

        except OSError as e:
            # File system error
            ctx.error = f"Failed to save: {e}"
            logger.error(f"Failed to save {url} to {output_path}: {e}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=url,
                        output_path=output_path,
                        error=str(e),
                        message=f"Failed to save: {e}",
                    )
                )
            raise

    def finalize(self) -> None:
        """Write SKILL.md if a skill name was configured.

        Called from ``Fetcher.__aexit__`` so it runs after every page has
        been saved. Idempotent: a second call is a no-op (the file is
        re-written with the same content).
        """
        if not self._skill_name or self._base_output_dir is None:
            return
        manifest_path = self._base_output_dir / "SKILL.md"
        description = self._skill_description or self._derive_description()
        body = self._render_skill_manifest(description)
        try:
            self._base_output_dir.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(body, encoding="utf-8")
            logger.info("Wrote skill manifest: %s", manifest_path)
        except OSError as e:
            logger.error("Failed to write SKILL.md to %s: %s", manifest_path, e)

    def _derive_description(self) -> str:
        """Derive a description from the first page's metadata."""
        meta = self._first_metadata or {}
        for key in ("description", "site_name"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if self._first_title:
            return f"Documentation snapshot from {self._first_title}."
        return f"Documentation snapshot for the {self._skill_name} skill."

    def _render_skill_manifest(self, description: str) -> str:
        # Escape double-quotes in the description for safe YAML emission.
        safe_desc = description.replace('"', '\\"')
        # Truncate to 200 chars; Claude Code skill descriptions should be
        # short and stable across re-fetches.
        if len(safe_desc) > 200:
            safe_desc = safe_desc[:197].rstrip() + "..."
        return (
            "---\n"
            f'name: {self._skill_name}\n'
            f'description: "{safe_desc}"\n'
            "---\n\n"
            f"# {self._skill_name}\n\n"
            f"{description}\n\n"
            "This skill was generated by [docpull](https://docpull.raintree.technology). "
            "Source documents live alongside this manifest in this directory.\n"
        )
