"""Event types for the streaming fetch API."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class SkipReason(str, Enum):
    """Reasons for skipping a URL during fetch."""

    ROBOTS_DISALLOWED = "robots_disallowed"
    ALREADY_FETCHED = "already_fetched"
    CACHE_UNCHANGED = "cache_unchanged"
    INVALID_CONTENT_TYPE = "invalid_content_type"
    DUPLICATE_CONTENT = "duplicate_content"
    PATTERN_EXCLUDED = "pattern_excluded"
    MAX_DEPTH_EXCEEDED = "max_depth_exceeded"
    HTTP_ERROR = "http_error"
    FILE_EXISTS = "file_exists"
    DRY_RUN = "dry_run"


class EventType(str, Enum):
    """Types of events emitted during fetch operations."""

    # Lifecycle events
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RESUMED = "resumed"

    # Discovery phase
    DISCOVERY_STARTED = "discovery_started"
    URL_DISCOVERED = "url_discovered"
    SITEMAP_FOUND = "sitemap_found"
    DISCOVERY_COMPLETE = "discovery_complete"

    # Fetch phase
    FETCH_STARTED = "fetch_started"
    FETCH_PROGRESS = "fetch_progress"
    FETCH_COMPLETED = "fetch_completed"
    FETCH_FAILED = "fetch_failed"
    FETCH_SKIPPED = "fetch_skipped"
    FETCH_RETRYING = "fetch_retrying"

    # Processing phase
    PAGE_CONVERTED = "page_converted"
    METADATA_EXTRACTED = "metadata_extracted"
    PAGE_SAVED = "page_saved"
    PAGE_DEDUPLICATED = "page_deduplicated"
    PAGE_FILTERED = "page_filtered"

    # Post-processing phase
    PROCESSING_STARTED = "processing_started"
    PROCESSING_COMPLETED = "processing_completed"
    INDEX_GENERATED = "index_generated"
    ARCHIVE_CREATED = "archive_created"
    GIT_COMMITTED = "git_committed"


@dataclass
class FetchEvent:
    """
    Event emitted during fetch operations.

    Provides typed fields for common event data instead of a generic dict.
    This makes it easier to handle events in consumer code.

    Example:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_PROGRESS:
                print(f"Progress: {event.current}/{event.total}")
            elif event.type == EventType.FETCH_COMPLETED:
                print(f"Saved: {event.output_path}")
            elif event.type == EventType.FETCH_FAILED:
                print(f"Error: {event.url} - {event.error}")
    """

    type: EventType

    # Timestamp (always UTC)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Common fields
    url: Optional[str] = None
    message: Optional[str] = None
    error: Optional[str] = None

    # Progress tracking
    current: Optional[int] = None
    total: Optional[int] = None

    # Typed payload fields for specific events
    bytes_downloaded: Optional[int] = None
    status_code: Optional[int] = None
    output_path: Optional[Path] = None
    content_type: Optional[str] = None
    retry_attempt: Optional[int] = None
    duplicate_of: Optional[str] = None  # URL of original for dedup events
    skip_reason: Optional["SkipReason"] = None  # Reason for skipping a URL

    @property
    def progress_percent(self) -> Optional[float]:
        """Calculate progress percentage if current and total are set."""
        if self.current is not None and self.total and self.total > 0:
            return (self.current / self.total) * 100
        return None

    @property
    def is_error(self) -> bool:
        """Check if this is an error event."""
        return self.type in (EventType.FAILED, EventType.FETCH_FAILED)

    @property
    def is_progress(self) -> bool:
        """Check if this is a progress event."""
        return self.type == EventType.FETCH_PROGRESS


@dataclass
class FetchStats:
    """
    Cumulative statistics for a fetch operation.

    Collected during the fetch and available on completion.
    """

    urls_discovered: int = 0
    pages_fetched: int = 0
    pages_skipped: int = 0
    pages_failed: int = 0
    pages_deduplicated: int = 0
    pages_filtered: int = 0
    bytes_downloaded: int = 0
    files_saved: int = 0
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        total = self.pages_fetched + self.pages_failed
        if total == 0:
            return 0.0
        return (self.pages_fetched / total) * 100

    def to_dict(self) -> dict:
        """Convert stats to dictionary for serialization."""
        return {
            "urls_discovered": self.urls_discovered,
            "pages_fetched": self.pages_fetched,
            "pages_skipped": self.pages_skipped,
            "pages_failed": self.pages_failed,
            "pages_deduplicated": self.pages_deduplicated,
            "pages_filtered": self.pages_filtered,
            "bytes_downloaded": self.bytes_downloaded,
            "files_saved": self.files_saved,
            "duration_seconds": round(self.duration_seconds, 2),
            "success_rate": round(self.success_rate, 1),
        }
