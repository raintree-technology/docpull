"""Streaming deduplication for real-time duplicate detection during fetch."""

from __future__ import annotations

import asyncio
import hashlib


class StreamingDeduplicator:
    """
    Real-time content deduplication during the fetch phase.

    Unlike post-processing deduplication (which reads files from disk after
    all pages are saved), streaming deduplication checks content before
    writing to disk. This saves:
    - Network bandwidth (by skipping processing of duplicates)
    - Disk I/O (by not writing duplicates)
    - Time (by not re-reading files for hash computation)

    Example:
        dedup = StreamingDeduplicator()

        async for url, content in fetch_pages():
            should_save, duplicate_of = await dedup.check_and_register(url, content)
            if should_save:
                save_to_disk(content)
            else:
                logger.info(f"Skipping duplicate: {url} (same as {duplicate_of})")
    """

    def __init__(self) -> None:
        """Initialize the deduplicator with empty state."""
        # hash -> representative_url (the first URL with this content)
        self._seen: dict[str, str] = {}
        self._lock = asyncio.Lock()

        # Statistics
        self._total_checked: int = 0
        self._duplicates_found: int = 0

    @staticmethod
    def compute_hash(content: str | bytes) -> str:
        """
        Compute SHA-256 hash of content.

        Args:
            content: Content to hash (str or bytes)

        Returns:
            Hex-encoded SHA-256 hash string

        Note:
            This uses the same algorithm as CacheManager.compute_checksum()
            for consistent hashing across the caching system.
        """
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    async def check_and_register(
        self,
        url: str,
        content: str | bytes,
    ) -> tuple[bool, str | None]:
        """
        Check if content is a duplicate and register if new.

        This is the main interface for streaming deduplication. Call this
        with each page's content before saving to disk.

        Args:
            url: The URL this content came from
            content: The content (str or bytes, before any transformation)

        Returns:
            A tuple of (should_save, duplicate_of_url):
            - (True, None) = new content, save it
            - (False, url) = duplicate of the returned URL, skip saving
        """
        content_hash = self.compute_hash(content)

        async with self._lock:
            self._total_checked += 1

            if content_hash in self._seen:
                self._duplicates_found += 1
                return (False, self._seen[content_hash])

            # First occurrence - register it
            self._seen[content_hash] = url
            return (True, None)

    async def is_duplicate(self, content: str | bytes) -> bool:
        """
        Check if content has been seen before (read-only).

        Unlike check_and_register, this doesn't register the content.
        Useful for checking without committing to save.

        Args:
            content: The content to check (str or bytes)

        Returns:
            True if content has been seen before
        """
        content_hash = self.compute_hash(content)
        async with self._lock:
            return content_hash in self._seen

    def get_stats(self) -> dict:
        """
        Get deduplication statistics.

        Returns:
            Dictionary with:
            - unique_pages: Number of unique content hashes seen
            - total_checked: Total pages processed
            - duplicates_found: Number of duplicates skipped
            - dedup_rate: Percentage of pages that were duplicates
        """
        dedup_rate = 0.0
        if self._total_checked > 0:
            dedup_rate = (self._duplicates_found / self._total_checked) * 100

        return {
            "unique_pages": len(self._seen),
            "total_checked": self._total_checked,
            "duplicates_found": self._duplicates_found,
            "dedup_rate": round(dedup_rate, 1),
        }

    def clear(self) -> None:
        """Clear all state (for testing or reset)."""
        self._seen.clear()
        self._total_checked = 0
        self._duplicates_found = 0
