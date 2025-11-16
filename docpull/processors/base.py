"""Base processor interface for post-processing pipeline."""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProcessorContext:
    """Context passed to processors containing fetched files and metadata."""

    # File paths that were fetched
    files: list[Path] = field(default_factory=list)

    # Metadata for each file (URL, size, checksum, etc.)
    metadata: dict[Path, dict[str, any]] = field(default_factory=dict)

    # Output directory
    output_dir: Path = Path("./docs")

    # Files to skip (already processed)
    skip_files: set[Path] = field(default_factory=set)

    # Statistics
    stats: dict[str, int] = field(default_factory=dict)


@dataclass
class ProcessorResult:
    """Result from a processor."""

    # Files after processing
    files: list[Path]

    # Updated metadata
    metadata: dict[Path, dict[str, any]]

    # Files that were removed/skipped
    removed_files: list[Path] = field(default_factory=list)

    # Processor-specific stats
    stats: dict[str, any] = field(default_factory=dict)

    # Messages/warnings
    messages: list[str] = field(default_factory=list)


class BaseProcessor(ABC):
    """Base class for all processors.

    Processors run after fetching to optimize, filter, or transform content.
    They operate on batches of files and can remove, modify, or annotate them.
    """

    def __init__(self, config: Optional[dict[str, any]] = None):
        """Initialize processor with optional configuration.

        Args:
            config: Processor-specific configuration dict
        """
        self.config = config or {}
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    def process(self, context: ProcessorContext) -> ProcessorResult:
        """Process a batch of files.

        Args:
            context: ProcessorContext with files and metadata

        Returns:
            ProcessorResult with processed files and stats
        """
        pass

    def should_skip(self, file_path: Path, context: ProcessorContext) -> bool:
        """Check if a file should be skipped.

        Args:
            file_path: Path to check
            context: Current processor context

        Returns:
            True if file should be skipped
        """
        return file_path in context.skip_files

    def log_stats(self, result: ProcessorResult) -> None:
        """Log processor statistics.

        Args:
            result: ProcessorResult to log
        """
        if result.stats:
            self.logger.info(f"{self.__class__.__name__} stats: {result.stats}")
        if result.messages:
            for msg in result.messages:
                self.logger.info(msg)


class ProcessorPipeline:
    """Pipeline for running multiple processors in sequence."""

    def __init__(self, processors: list[BaseProcessor]):
        """Initialize pipeline with processors.

        Args:
            processors: List of processors to run in order
        """
        self.processors = processors
        self.logger = logging.getLogger(__name__)

    def run(self, context: ProcessorContext) -> ProcessorContext:
        """Run all processors in sequence.

        Args:
            context: Initial processor context

        Returns:
            Updated context after all processors
        """
        self.logger.info(f"Running {len(self.processors)} processors")

        for processor in self.processors:
            self.logger.debug(f"Running {processor.__class__.__name__}")

            try:
                result = processor.process(context)
                processor.log_stats(result)

                # Update context with results
                context.files = result.files
                context.metadata = result.metadata
                context.skip_files.update(result.removed_files)

                # Merge stats
                for key, value in result.stats.items():
                    context.stats[f"{processor.__class__.__name__}.{key}"] = value

            except Exception as e:
                self.logger.error(f"Processor {processor.__class__.__name__} failed: {e}", exc_info=True)
                # Continue with other processors
                continue

        self.logger.info(f"Pipeline complete. Files: {len(context.files)}")
        return context
