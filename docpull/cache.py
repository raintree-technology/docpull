"""Cache management for update detection and incremental fetching."""

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CacheManager:
    """Manage cache for tracking fetched documents and detecting updates."""

    def __init__(self, cache_dir: Path):
        """Initialize cache manager.

        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.manifest_file = self.cache_dir / "manifest.json"
        self.state_file = self.cache_dir / "state.json"

        self.manifest: dict[str, dict] = self._load_manifest()
        self.state: dict[str, any] = self._load_state()

    def _load_manifest(self) -> dict[str, dict]:
        """Load manifest from disk.

        Returns:
            Manifest dict mapping URLs to metadata
        """
        if self.manifest_file.exists():
            try:
                with open(self.manifest_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load manifest: {e}")

        return {}

    def _save_manifest(self):
        """Save manifest to disk."""
        try:
            with open(self.manifest_file, "w", encoding="utf-8") as f:
                json.dump(self.manifest, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Could not save manifest: {e}")

    def _load_state(self) -> dict[str, any]:
        """Load state from disk.

        Returns:
            State dict with progress information
        """
        if self.state_file.exists():
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Could not load state: {e}")

        return {
            "fetched_urls": [],
            "failed_urls": [],
            "last_run": None,
        }

    def _save_state(self):
        """Save state to disk."""
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Could not save state: {e}")

    def compute_checksum(self, content: str) -> str:
        """Compute checksum of content.

        Args:
            content: Content to hash

        Returns:
            SHA-256 hex digest
        """
        return hashlib.sha256(content.encode()).hexdigest()

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
            return etag != cached["etag"]

        # Check Last-Modified
        if last_modified and "last_modified" in cached:
            return last_modified != cached["last_modified"]

        # Check content checksum
        if content and "checksum" in cached:
            current_checksum = self.compute_checksum(content)
            return current_checksum != cached["checksum"]

        # Can't determine, assume changed
        return True

    def update_cache(
        self,
        url: str,
        content: str,
        file_path: Path,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
    ):
        """Update cache entry for a URL.

        Args:
            url: URL that was fetched
            content: Content that was fetched
            file_path: Path where content was saved
            etag: HTTP ETag header
            last_modified: HTTP Last-Modified header
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

        self._save_manifest()

    def mark_fetched(self, url: str):
        """Mark URL as successfully fetched.

        Args:
            url: URL that was fetched
        """
        if url not in self.state["fetched_urls"]:
            self.state["fetched_urls"].append(url)
        self._save_state()

    def mark_failed(self, url: str):
        """Mark URL as failed.

        Args:
            url: URL that failed
        """
        if url not in self.state["failed_urls"]:
            self.state["failed_urls"].append(url)
        self._save_state()

    def get_fetched_urls(self) -> set[str]:
        """Get set of URLs that have been successfully fetched.

        Returns:
            Set of fetched URLs
        """
        return set(self.state.get("fetched_urls", []))

    def get_failed_urls(self) -> set[str]:
        """Get set of URLs that failed to fetch.

        Returns:
            Set of failed URLs
        """
        return set(self.state.get("failed_urls", []))

    def start_session(self):
        """Start a new fetch session."""
        self.state["last_run"] = datetime.now().isoformat()
        self._save_state()

    def clear_state(self):
        """Clear incremental state (for fresh start)."""
        self.state = {
            "fetched_urls": [],
            "failed_urls": [],
            "last_run": None,
        }
        self._save_state()
        logger.info("Cleared incremental state")

    def get_cache_stats(self) -> dict[str, any]:
        """Get cache statistics.

        Returns:
            Dict with cache stats
        """
        return {
            "cached_urls": len(self.manifest),
            "fetched_urls": len(self.state.get("fetched_urls", [])),
            "failed_urls": len(self.state.get("failed_urls", [])),
            "last_run": self.state.get("last_run"),
        }
