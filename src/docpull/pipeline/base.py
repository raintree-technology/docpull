"""Base classes for the fetch pipeline architecture."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..models.events import EventType, FetchEvent, SkipReason

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
        source_type: Detected framework (nextjs, docusaurus, mintlify, etc.)
        chunks: List of token-bounded Markdown chunks (if chunking enabled)
    """

    url: str
    output_path: Path

    # Content (accumulated through pipeline)
    html: bytes | None = None
    markdown: str | None = None
    metadata: dict = field(default_factory=dict)
    title: str | None = None
    extraction_info: dict[str, Any] = field(default_factory=dict)

    # Status
    should_skip: bool = False
    skip_reason: str | None = None
    skip_code: SkipReason | None = None
    error: str | None = None

    # Additional data from fetch
    status_code: int | None = None
    http_attempts: int | None = None
    retry_after_seconds: float | None = None
    content_type: str | None = None
    bytes_downloaded: int = 0
    persisted_path: Path | None = None

    # HTTP caching headers (for incremental updates)
    etag: str | None = None
    last_modified: str | None = None

    # Raw response capture for WARC output. Populated by FetchStep only when
    # WARC output is enabled, and snapshotted before ConvertStep can mutate
    # ctx.html, so the archived bytes are exactly what the server sent.
    raw_response_headers: dict[str, str] | None = None
    raw_content: bytes | None = field(default=None, repr=False)
    warc_record_id: str | None = None

    # Framework detection and LLM-oriented output
    source_type: str | None = None
    chunks: list[object] = field(default_factory=list)

    # Internal parse cache shared by adjacent HTML pipeline steps. Kept out
    # of repr/serialization and released by ConvertStep after use.
    parsed_html: object | None = field(default=None, repr=False)

    def mark_skipped(self, reason: str, code: SkipReason | None = None) -> None:
        self.should_skip = True
        self.skip_reason = reason
        self.skip_code = code

    def mark_failed(self, error: str) -> None:
        self.error = error
        self.should_skip = False


class PipelineStatus(str, Enum):
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class PipelineResult:
    ctx: PageContext
    status: PipelineStatus
    failed_step: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == PipelineStatus.SUCCEEDED

    @property
    def skipped(self) -> bool:
        return self.status == PipelineStatus.SKIPPED

    @property
    def failed(self) -> bool:
        return self.status == PipelineStatus.FAILED


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
        emit: EventEmitter | None = None,
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

    async def execute_result(
        self,
        url: str,
        output_path: Path,
        emit: EventEmitter | None = None,
    ) -> PipelineResult:
        """
        Execute the pipeline for a URL.

        Args:
            url: The URL to process
            output_path: Where to save the output
            emit: Optional callback for emitting events

        Returns:
            PipelineResult with terminal status and final context.
        """
        ctx = PageContext(url=url, output_path=output_path)

        for step in self.steps:
            try:
                ctx = await step.execute(ctx, emit)
            except Exception as e:
                ctx.mark_failed(f"{step.name}: {e}")

                # Emit failure event
                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_FAILED,
                            url=url,
                            error=ctx.error,
                        )
                    )
                return PipelineResult(ctx=ctx, status=PipelineStatus.FAILED, failed_step=step.name)

            if ctx.error:
                return PipelineResult(ctx=ctx, status=PipelineStatus.FAILED, failed_step=step.name)
            if ctx.should_skip:
                return PipelineResult(ctx=ctx, status=PipelineStatus.SKIPPED)

        return PipelineResult(ctx=ctx, status=PipelineStatus.SUCCEEDED)

    async def execute(
        self,
        url: str,
        output_path: Path,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        """Compatibility wrapper returning only the final context."""
        return (await self.execute_result(url, output_path, emit)).ctx
