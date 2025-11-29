"""Cache management for update detection and incremental fetching."""

import hashlib
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, TypedDict, Union

logger = logging.getLogger(__name__)

# Default TTL for cache entries (30 days)
DEFAULT_TTL_DAYS = 30


class ManifestEntry(TypedDict, total=False):
    """Type for manifest cache entries."""

    checksum: str
    file_path: str
    fetched_at: str
    size: int
    etag: str
    last_modified: str


class CacheState(TypedDict, total=False):
    """Type for cache state (serialized format uses lists)."""

    fetched_urls: list[str]
    failed_urls: list[str]
    last_run: Optional[str]


class _InternalState:
    """Internal state using sets for O(1) lookups."""

    def __init__(self) -> None:
        self.fetched_urls: set[str] = set()
        self.failed_urls: set[str] = set()
        self.last_run: Optional[str] = None

    @classmethod
    def from_cache_state(cls, state: CacheState) -> "_InternalState":
        """Create internal state from serialized CacheState."""
        internal = cls()
        internal.fetched_urls = set(state.get("fetched_urls", []))
        internal.failed_urls = set(state.get("failed_urls", []))
        internal.last_run = state.get("last_run")
        return internal

    def to_cache_state(self) -> CacheState:
        """Convert to serializable CacheState."""
        return {
            "fetched_urls": list(self.fetched_urls),
            "failed_urls": list(self.failed_urls),
            "last_run": self.last_run,
        }


class CacheManager:
    """Manage cache for tracking fetched documents and detecting updates.

    Features:
    - Batched writes: Changes are accumulated in memory and written on flush()
    - O(1) URL lookups: Uses sets internally for fast membership checks
    - TTL support: Cache entries can be evicted after a configurable time
    - Consistent hashing: Uses bytes input for SHA-256 computation
    """

    def __init__(self, cache_dir: Path, ttl_days: Optional[int] = None):
        """Initialize cache manager.

        Args:
            cache_dir: Directory to store cache files
            ttl_days: Days before cache entries expire (None = no expiry)
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_days = ttl_days

        self.manifest_file = self.cache_dir / "manifest.json"
        self.state_file = self.cache_dir / "state.json"

        self.manifest: dict[str, ManifestEntry] = self._load_manifest()
        self._state: _InternalState = _InternalState.from_cache_state(self._load_state())

        # Track if there are unsaved changes (for batched writes)
        self._manifest_dirty = False
        self._state_dirty = False

    def _load_manifest(self) -> dict[str, ManifestEntry]:
        """Load manifest from disk.

        Returns:
            Manifest dict mapping URLs to metadata
        """
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, encoding="utf-8") as f:
                    data: dict[str, ManifestEntry] = json.load(f)
                    return data
            except Exception as e:
                logger.warning(f"Could not load manifest: {e}")

        return {}

    def _save_manifest(self) -> None:
        """Save manifest to disk (internal, called by flush)."""
        if not self._manifest_dirty:
            return
        try:
            with open(self.manifest_file, "w", encoding="utf-8") as f:
                json.dump(self.manifest, f, indent=2, ensure_ascii=False)
            self._manifest_dirty = False
        except Exception as e:
            logger.error(f"Could not save manifest: {e}")

    def _load_state(self) -> CacheState:
        """Load state from disk.

        Returns:
            State dict with progress information
        """
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    data: CacheState = json.load(f)
                    return data
            except Exception as e:
                logger.warning(f"Could not load state: {e}")

        return {
            "fetched_urls": [],
            "failed_urls": [],
            "last_run": None,
        }

    def _save_state(self) -> None:
        """Save state to disk (internal, called by flush)."""
        if not self._state_dirty:
            return
        try:
            state = self._state.to_cache_state()
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            self._state_dirty = False
        except Exception as e:
            logger.error(f"Could not save state: {e}")

    def flush(self) -> None:
        """Flush all pending changes to disk.

        Call this after a batch of operations to persist changes.
        This is more efficient than saving after every operation.
        """
        self._save_manifest()
        self._save_state()

    def __enter__(self) -> "CacheManager":
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[object],
    ) -> None:
        """Context manager exit - auto-flush on exit."""
        self.flush()

    @staticmethod
    def compute_checksum(content: Union[str, bytes]) -> str:
        """Compute SHA-256 checksum of content.

        Args:
            content: Content to hash (str or bytes)

        Returns:
            SHA-256 hex digest
        """
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    def has_changed(
        self,
        url: str,
        content: Optional[str] = None,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> bool:
        """Check if content has changed since last fetch.

        Args:
            url: URL to check
            content: Current content (for checksum comparison)
            etag: HTTP ETag header
            last_modified: HTTP Last-Modified header

        Returns:
            True if content has changed or is new
        """
        if url not in self.manifest:
            return True  # New URL

        cached = self.manifest[url]

        # Check ETag first (most reliable)
        if etag and "etag" in cached:
            return bool(etag != cached["etag"])

        # Check Last-Modified
        if last_modified and "last_modified" in cached:
            return bool(last_modified != cached["last_modified"])

        # Check content checksum
        if content and "checksum" in cached:
            current_checksum = self.compute_checksum(content)
            return bool(current_checksum != cached["checksum"])

        # Can't determine, assume changed
        return True

    def update_cache(
        self,
        url: str,
        content: Union[str, bytes],
        file_path: Path,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ) -> None:
        """Update cache entry for a URL.

        Args:
            url: URL that was fetched
            content: Content that was fetched (str or bytes)
            file_path: Path where content was saved
            etag: HTTP ETag header
            last_modified: HTTP Last-Modified header

        Note:
            Changes are batched. Call flush() to persist to disk.
        """
        self.manifest[url] = {
            "checksum": self.compute_checksum(content),
            "file_path": str(file_path),
            "fetched_at": datetime.now().isoformat(),
            "size": len(content),
        }

        if etag:
            self.manifest[url]["etag"] = etag
        if last_modified:
            self.manifest[url]["last_modified"] = last_modified

        self._manifest_dirty = True

    def mark_fetched(self, url: str) -> None:
        """Mark URL as successfully fetched.

        Args:
            url: URL that was fetched

        Note:
            Changes are batched. Call flush() to persist to disk.
        """
        self._state.fetched_urls.add(url)
        self._state_dirty = True

    def mark_failed(self, url: str) -> None:
        """Mark URL as failed.

        Args:
            url: URL that failed

        Note:
            Changes are batched. Call flush() to persist to disk.
        """
        self._state.failed_urls.add(url)
        self._state_dirty = True

    def get_fetched_urls(self) -> set[str]:
        """Get set of URLs that have been successfully fetched.

        Returns:
            Set of fetched URLs (copy to prevent mutation)
        """
        return self._state.fetched_urls.copy()

    def get_failed_urls(self) -> set[str]:
        """Get set of URLs that failed to fetch.

        Returns:
            Set of failed URLs (copy to prevent mutation)
        """
        return self._state.failed_urls.copy()

    def start_session(self) -> None:
        """Start a new fetch session.

        Note:
            Changes are batched. Call flush() to persist to disk.
        """
        self._state.last_run = datetime.now().isoformat()
        self._state_dirty = True

    def clear_state(self) -> None:
        """Clear incremental state (for fresh start).

        Note:
            This immediately flushes to disk.
        """
        self._state = _InternalState()
        self._state_dirty = True
        self.flush()
        logger.info("Cleared incremental state")

    def get_cache_stats(self) -> dict[str, Union[str, int, None]]:
        """Get cache statistics.

        Returns:
            Dict with cache stats
        """
        return {
            "cached_urls": len(self.manifest),
            "fetched_urls": len(self._state.fetched_urls),
            "failed_urls": len(self._state.failed_urls),
            "last_run": self._state.last_run,
        }

    def evict_expired(self, ttl_days: Optional[int] = None) -> int:
        """Remove cache entries older than TTL.

        Args:
            ttl_days: Days before entries expire (uses instance default if None)

        Returns:
            Number of entries evicted
        """
        ttl = ttl_days if ttl_days is not None else self.ttl_days
        if ttl is None:
            return 0

        cutoff = datetime.now() - timedelta(days=ttl)
        to_remove = []

        for url, entry in self.manifest.items():
            fetched_at = entry.get("fetched_at")
            if fetched_at:
                try:
                    entry_time = datetime.fromisoformat(fetched_at)
                    if entry_time < cutoff:
                        to_remove.append(url)
                except ValueError:
                    pass  # Invalid date format, skip

        for url in to_remove:
            del self.manifest[url]

        if to_remove:
            self._manifest_dirty = True
            logger.info(f"Evicted {len(to_remove)} expired cache entries")

        return len(to_remove)

    def is_fetched(self, url: str) -> bool:
        """Check if URL has been fetched (O(1) lookup).

        Args:
            url: URL to check

        Returns:
            True if URL was successfully fetched
        """
        return url in self._state.fetched_urls

    def is_failed(self, url: str) -> bool:
        """Check if URL has failed (O(1) lookup).

        Args:
            url: URL to check

        Returns:
            True if URL failed to fetch
        """
        return url in self._state.failed_urls
