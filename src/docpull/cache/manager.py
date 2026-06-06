"""Cache management for update detection and incremental fetching."""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Any, TypedDict

from ..time_utils import parse_persisted_datetime, utc_now, utc_now_iso
from .frontier import FrontierStore

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
    schema_version: int
    run_fingerprint: dict[str, Any]


class CacheState(TypedDict, total=False):
    """Type for cache state (serialized format uses lists)."""

    fetched_urls: list[str]
    failed_urls: list[str]
    last_run: str | None


class DiscoveredUrlsState(TypedDict, total=False):
    """Type for discovered URLs persistence (for resume capability)."""

    start_url: str
    discovered_at: str
    config_fingerprint: dict[str, Any]
    urls: list[str]


class _InternalState:
    """Internal state using sets for O(1) lookups."""

    def __init__(self) -> None:
        self.fetched_urls: set[str] = set()
        self.failed_urls: set[str] = set()
        self.last_run: str | None = None

    @classmethod
    def from_cache_state(cls, state: CacheState) -> _InternalState:
        """Create internal state from serialized CacheState."""
        internal = cls()
        internal.fetched_urls = set(_string_list(state.get("fetched_urls")))
        internal.failed_urls = set(_string_list(state.get("failed_urls")))
        last_run = state.get("last_run")
        internal.last_run = last_run if isinstance(last_run, str) else None
        return internal

    def to_cache_state(self) -> CacheState:
        """Convert to serializable CacheState."""
        return {
            "fetched_urls": sorted(self.fetched_urls),
            "failed_urls": sorted(self.failed_urls),
            "last_run": self.last_run,
        }


def _string_list(value: object) -> list[str]:
    """Return only string items from a persisted JSON list."""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


class CacheManager:
    """Manage cache for tracking fetched documents and detecting updates.

    Features:
    - Batched writes: Changes are accumulated in memory and written on flush()
    - O(1) URL lookups: Uses sets internally for fast membership checks
    - TTL support: Cache entries can be evicted after a configurable time
    - Consistent hashing: Uses bytes input for SHA-256 computation
    """

    def __init__(self, cache_dir: Path, ttl_days: int | None = None):
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
        self.discovered_urls_file = self.cache_dir / "discovered_urls.json"
        self.frontier_file = self.cache_dir / "frontier.json"
        self.frontier = FrontierStore(self.frontier_file)

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
                    data: Any = json.load(f)
                if not isinstance(data, dict):
                    msg = "manifest root is not an object"
                    raise ValueError(msg)
                manifest: dict[str, ManifestEntry] = {}
                for url, raw_entry in data.items():
                    if not isinstance(url, str) or not isinstance(raw_entry, dict):
                        logger.warning("Skipping invalid cache manifest entry for %r", url)
                        continue
                    entry: ManifestEntry = {}
                    for key in ("checksum", "file_path", "fetched_at", "etag", "last_modified"):
                        value = raw_entry.get(key)
                        if isinstance(value, str):
                            entry[key] = value  # type: ignore[literal-required]
                    size = raw_entry.get("size")
                    if isinstance(size, int):
                        entry["size"] = size
                    schema_version = raw_entry.get("schema_version")
                    if isinstance(schema_version, int):
                        entry["schema_version"] = schema_version
                    run_fingerprint = raw_entry.get("run_fingerprint")
                    if isinstance(run_fingerprint, dict):
                        entry["run_fingerprint"] = run_fingerprint
                    manifest[url] = entry
                return manifest
            except Exception as e:
                logger.warning(f"Could not load manifest: {e}")

        return {}

    def _write_json(self, path: Path, data: object) -> None:
        """Atomically write JSON data to a cache file."""
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.cache_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_path = Path(f.name)
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            temp_path.replace(path)
        except Exception:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            raise

    def _save_manifest(self) -> None:
        """Save manifest to disk (internal, called by flush)."""
        if not self._manifest_dirty:
            return
        try:
            self._write_json(self.manifest_file, self.manifest)
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
                    data: Any = json.load(f)
                if not isinstance(data, dict):
                    msg = "state root is not an object"
                    raise ValueError(msg)
                last_run = data.get("last_run")
                return {
                    "fetched_urls": _string_list(data.get("fetched_urls")),
                    "failed_urls": _string_list(data.get("failed_urls")),
                    "last_run": last_run if isinstance(last_run, str) else None,
                }
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
            self._write_json(self.state_file, state)
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

    def __enter__(self) -> CacheManager:
        """Context manager entry."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object | None,
    ) -> None:
        """Context manager exit - auto-flush on exit."""
        self.flush()

    @staticmethod
    def compute_checksum(content: str | bytes) -> str:
        """Compute SHA-256 checksum of content.

        Args:
            content: Content to hash (str or bytes)

        Returns:
            SHA-256 hex digest
        """
        if isinstance(content, str):
            content = content.encode("utf-8")
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _content_size(content: str | bytes) -> int:
        if isinstance(content, str):
            return len(content.encode("utf-8"))
        return len(content)

    def update_cache(
        self,
        url: str,
        content: str | bytes,
        file_path: Path,
        etag: str | None = None,
        last_modified: str | None = None,
        run_fingerprint: dict[str, Any] | None = None,
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
            "fetched_at": utc_now_iso(),
            "size": self._content_size(content),
            "schema_version": 1,
        }
        if run_fingerprint is not None:
            self.manifest[url]["run_fingerprint"] = run_fingerprint

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
        self._state.failed_urls.discard(url)
        self.frontier.mark_succeeded(url)
        self._state_dirty = True

    def mark_failed(self, url: str) -> None:
        """Mark URL as failed.

        Args:
            url: URL that failed

        Note:
            Changes are batched. Call flush() to persist to disk.
        """
        self._state.failed_urls.add(url)
        self._state.fetched_urls.discard(url)
        self.frontier.mark_failed(url)
        self._state_dirty = True

    def get_fetched_urls(self) -> set[str]:
        """Get set of URLs that have been successfully fetched.

        Returns:
            Set of fetched URLs (copy to prevent mutation)
        """
        return self._state.fetched_urls.copy()

    def start_session(self) -> None:
        """Start a new fetch session.

        Note:
            Changes are batched. Call flush() to persist to disk.
        """
        self._state.last_run = utc_now_iso()
        self._state_dirty = True

    def evict_expired(self, ttl_days: int | None = None) -> int:
        """Remove cache entries older than TTL.

        Args:
            ttl_days: Days before entries expire (uses instance default if None)

        Returns:
            Number of entries evicted
        """
        ttl = ttl_days if ttl_days is not None else self.ttl_days
        if ttl is None:
            return 0

        cutoff = utc_now() - timedelta(days=ttl)
        to_remove = []

        for url, entry in self.manifest.items():
            fetched_at = entry.get("fetched_at")
            if fetched_at:
                try:
                    entry_time = parse_persisted_datetime(fetched_at)
                    if entry_time < cutoff:
                        to_remove.append(url)
                except ValueError as err:
                    logger.warning("Invalid cache timestamp for %s: %s", url, err)

        for url in to_remove:
            del self.manifest[url]

        if to_remove:
            for url in to_remove:
                self._state.fetched_urls.discard(url)
                self._state.failed_urls.discard(url)
            self._manifest_dirty = True
            self._state_dirty = True
            logger.info(f"Evicted {len(to_remove)} expired cache entries")

        return len(to_remove)

    # Resume capability methods

    def save_discovered_urls(
        self,
        urls: list[str],
        start_url: str,
        config_fingerprint: dict[str, Any] | None = None,
    ) -> None:
        """Save discovered URLs for resume capability.

        Args:
            urls: List of discovered URLs
            start_url: The starting URL for this crawl

        Note:
            This is written immediately (not batched) to ensure
            URLs are persisted before fetching begins.
        """
        data: DiscoveredUrlsState = {
            "start_url": start_url,
            "discovered_at": utc_now_iso(),
            "urls": urls,
        }
        if config_fingerprint is not None:
            data["config_fingerprint"] = config_fingerprint
            self.frontier.initialize(start_url=start_url, run_fingerprint=config_fingerprint)
        self.frontier.add_many(urls, source="discovery")
        self.frontier.save()
        try:
            self._write_json(self.discovered_urls_file, data)
            logger.info(f"Saved {len(urls)} discovered URLs for resume capability")
        except Exception as e:
            logger.error(f"Could not save discovered URLs: {e}")

    def load_discovered_urls(
        self,
        start_url: str,
        config_fingerprint: dict[str, Any] | None = None,
    ) -> list[str] | None:
        """Load previously discovered URLs if they match the start URL.

        Args:
            start_url: The starting URL to match

        Returns:
            List of discovered URLs if found and matching, None otherwise
        """
        if not self.discovered_urls_file.exists():
            return None

        try:
            with open(self.discovered_urls_file, encoding="utf-8") as f:
                data: Any = json.load(f)
            if not isinstance(data, dict):
                msg = "discovered URLs root is not an object"
                raise ValueError(msg)

            if data.get("start_url") != start_url:
                logger.info("Discovered URLs file exists but start_url doesn't match")
                return None
            if config_fingerprint is not None:
                persisted_fingerprint = data.get("config_fingerprint")
                if isinstance(persisted_fingerprint, dict) and persisted_fingerprint != config_fingerprint:
                    logger.info("Discovered URLs file exists but crawl fingerprint doesn't match")
                    return None

            urls = _string_list(data.get("urls"))
            logger.info(f"Loaded {len(urls)} discovered URLs from previous run")
            return urls
        except Exception as e:
            logger.warning(f"Could not load discovered URLs: {e}")
            return None

    def get_pending_urls(
        self,
        start_url: str,
        config_fingerprint: dict[str, Any] | None = None,
    ) -> list[str] | None:
        """Get URLs that were discovered but not yet fetched.

        Args:
            start_url: The starting URL to match

        Returns:
            List of pending URLs, or None if no resume data available
        """
        if config_fingerprint is not None and self.frontier.compatible(
            start_url=start_url,
            run_fingerprint=config_fingerprint,
        ):
            pending = self.frontier.pending_urls()
            logger.info("Found %d pending URLs from frontier", len(pending))
            return pending

        discovered = self.load_discovered_urls(start_url, config_fingerprint=config_fingerprint)
        if discovered is None:
            return None

        # Filter out already-fetched URLs
        fetched = self.get_fetched_urls()
        pending = [url for url in discovered if url not in fetched]
        logger.info(f"Found {len(pending)} pending URLs (out of {len(discovered)} discovered)")
        return pending

    def clear_discovered_urls(self) -> None:
        """Clear discovered URLs file (called on successful completion).

        This should be called after a successful fetch to clean up
        the resume state.
        """
        if self.discovered_urls_file.exists():
            try:
                self.discovered_urls_file.unlink()
                logger.info("Cleared discovered URLs file")
            except Exception as e:
                logger.warning(f"Could not clear discovered URLs file: {e}")
        self.frontier.clear()
