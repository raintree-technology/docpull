"""SaveStep - File saving pipeline step."""

import asyncio
import logging
from pathlib import Path
from typing import Optional

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
        base_output_dir: Optional[Path] = None,
    ) -> None:
        """
        Initialize the save step.

        Args:
            base_output_dir: Optional base directory for output path validation.
                            If set, output paths must be within this directory.
        """
        self._base_output_dir = base_output_dir

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
        emit: Optional[EventEmitter] = None,
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

            # Write content (use asyncio.to_thread to avoid blocking)
            await asyncio.to_thread(
                validated_path.write_text,
                content,
                encoding="utf-8",
            )

            logger.info(f"Saved: {validated_path}")

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
