"""Base classes for the fetch pipeline architecture."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Protocol, runtime_checkable

from ..models.events import EventType, FetchEvent

# Type alias for event emitter function
EventEmitter = Callable[[FetchEvent], None]


@dataclass
class PageContext:
    """
    Context object passed through pipeline steps.

    Contains all state for processing a single page, accumulated
    as it moves through the pipeline.

    Attributes:
        url: The URL being fetched
        output_path: Target path for saving the file
        html: Raw HTML content (bytes to avoid encoding issues)
        markdown: Converted markdown content
        metadata: Extracted metadata (Open Graph, JSON-LD, etc.)
        should_skip: If True, remaining steps will be skipped
        skip_reason: Human-readable reason for skipping
        error: Error message if an exception occurred
    """

    url: str
    output_path: Path

    # Content (accumulated through pipeline)
    html: Optional[bytes] = None
    markdown: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    title: Optional[str] = None

    # Status
    should_skip: bool = False
    skip_reason: Optional[str] = None
    error: Optional[str] = None

    # Additional data from fetch
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    bytes_downloaded: int = 0

    # HTTP caching headers (for incremental updates)
    etag: Optional[str] = None
    last_modified: Optional[str] = None


@runtime_checkable
class FetchStep(Protocol):
    """
    Protocol for pipeline steps.

    Each step receives a PageContext, processes it, and returns
    the (possibly modified) context.

    Error Handling Contract:
    - For expected skips (robots.txt, dedup): set ctx.should_skip = True
      and ctx.skip_reason = "reason"
    - For unexpected failures: raise an exception
    - The pipeline will catch exceptions and set ctx.error

    Example implementation:
        class ValidateStep:
            name = "validate"

            async def execute(
                self,
                ctx: PageContext,
                emit: Optional[EventEmitter] = None
            ) -> PageContext:
                if not self.validator.is_valid(ctx.url):
                    ctx.should_skip = True
                    ctx.skip_reason = "URL validation failed"
                return ctx
    """

    name: str

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Execute this pipeline step.

        Args:
            ctx: The page context with accumulated state
            emit: Optional callback to emit events

        Returns:
            The (possibly modified) page context
        """
        ...


@dataclass
class FetchPipeline:
    """
    Pipeline for processing a single page through multiple steps.

    Steps are executed in order. If a step sets ctx.should_skip = True,
    remaining steps are skipped. If a step raises an exception, the
    error is captured in ctx.error and processing stops.

    Example:
        pipeline = FetchPipeline(steps=[
            ValidateStep(validator),
            FetchStep(http_client),
            ConvertStep(converter),
            MetadataStep(extractor),
            SaveStep(),
        ])

        ctx = await pipeline.execute(url, output_path, emit=log_event)
        if ctx.error:
            logger.error(f"Failed: {ctx.error}")
        elif ctx.should_skip:
            logger.info(f"Skipped: {ctx.skip_reason}")
        else:
            logger.info(f"Saved: {ctx.output_path}")
    """

    steps: list[FetchStep]

    async def execute(
        self,
        url: str,
        output_path: Path,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Execute the pipeline for a URL.

        Args:
            url: The URL to process
            output_path: Where to save the output
            emit: Optional callback for emitting events

        Returns:
            PageContext with final state (check error/should_skip for status)
        """
        ctx = PageContext(url=url, output_path=output_path)

        for step in self.steps:
            if ctx.should_skip:
                break

            try:
                ctx = await step.execute(ctx, emit)
            except Exception as e:
                ctx.error = f"{step.name}: {e}"
                ctx.should_skip = True

                # Emit failure event
                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_FAILED,
                            url=url,
                            error=ctx.error,
                        )
                    )
                break

        return ctx

    def add_step(self, step: FetchStep) -> "FetchPipeline":
        """
        Add a step to the pipeline (fluent API).

        Args:
            step: The step to add

        Returns:
            Self for chaining
        """
        self.steps.append(step)
        return self
