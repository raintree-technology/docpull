"""Durable crawl frontier state for pause/resume and provenance."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ..models.schema import FRONTIER_SCHEMA_VERSION
from ..time_utils import utc_now_iso

logger = logging.getLogger(__name__)


class FrontierState(str, Enum):
    """Lifecycle state for a URL in the crawl frontier."""

    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass
class FrontierEntry:
    url: str
    state: FrontierState = FrontierState.QUEUED
    depth: int | None = None
    source: str | None = None
    discovered_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    attempts: int = 0
    last_error: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FrontierEntry | None:
        url = data.get("url")
        if not isinstance(url, str):
            return None
        try:
            state = FrontierState(str(data.get("state", FrontierState.QUEUED.value)))
        except ValueError:
            state = FrontierState.QUEUED
        attempts = data.get("attempts")
        discovered_at = data.get("discovered_at")
        updated_at = data.get("updated_at")
        return cls(
            url=url,
            state=state,
            depth=data.get("depth") if isinstance(data.get("depth"), int) else None,
            source=data.get("source") if isinstance(data.get("source"), str) else None,
            discovered_at=discovered_at if isinstance(discovered_at, str) else utc_now_iso(),
            updated_at=updated_at if isinstance(updated_at, str) else utc_now_iso(),
            attempts=attempts if isinstance(attempts, int) else 0,
            last_error=data.get("last_error") if isinstance(data.get("last_error"), str) else None,
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "state": self.state.value,
            "depth": self.depth,
            "source": self.source,
            "discovered_at": self.discovered_at,
            "updated_at": self.updated_at,
            "attempts": self.attempts,
            "last_error": self.last_error,
        }


class FrontierStore:
    """Small JSON-backed frontier store.

    The store is intentionally simple because docpull is single-process today.
    It gives us explicit URL lifecycle state and a compatibility fingerprint
    without introducing a queue service or SQLite dependency for markdown users.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.journal_path = self.path.with_suffix(self.path.suffix + ".journal")
        self.entries: dict[str, FrontierEntry] = {}
        self.start_url: str | None = None
        self.run_fingerprint: dict[str, object] | None = None
        self.created_at: str | None = None
        self.updated_at: str | None = None
        self._journal_needs_separator = False
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as err:
                logger.warning("Could not load frontier store %s: %s", self.path, err)
            else:
                self._load_snapshot(data)
        self._replay_journal()

    def _load_snapshot(self, data: object) -> None:
        if not isinstance(data, dict) or data.get("schema_version") != FRONTIER_SCHEMA_VERSION:
            return
        entries = data.get("entries")
        if not isinstance(entries, list):
            return
        self.start_url = data.get("start_url") if isinstance(data.get("start_url"), str) else None
        fingerprint = data.get("run_fingerprint")
        self.run_fingerprint = fingerprint if isinstance(fingerprint, dict) else None
        self.created_at = data.get("created_at") if isinstance(data.get("created_at"), str) else None
        self.updated_at = data.get("updated_at") if isinstance(data.get("updated_at"), str) else None
        for item in entries:
            if not isinstance(item, dict):
                continue
            entry = FrontierEntry.from_json(item)
            if entry:
                self.entries[entry.url] = entry

    def _replay_journal(self) -> None:
        """Apply complete transition records after the last snapshot."""
        if not self.journal_path.exists():
            return
        try:
            with self.journal_path.open(encoding="utf-8") as journal:
                for line_number, line in enumerate(journal, start=1):
                    if not line.strip():
                        continue
                    try:
                        update = json.loads(line)
                    except json.JSONDecodeError:
                        if not line.endswith("\n"):
                            self._journal_needs_separator = True
                        logger.warning(
                            "Ignoring incomplete frontier journal record %s:%d",
                            self.journal_path,
                            line_number,
                        )
                        continue
                    if not isinstance(update, dict):
                        continue
                    if update.get("schema_version") != FRONTIER_SCHEMA_VERSION:
                        continue
                    raw_entry = update.get("entry")
                    if not isinstance(raw_entry, dict):
                        continue
                    entry = FrontierEntry.from_json(raw_entry)
                    if entry is not None:
                        self.entries[entry.url] = entry
                        self.updated_at = entry.updated_at
        except OSError as err:
            logger.warning("Could not load frontier journal %s: %s", self.journal_path, err)

    def _append_entry(self, entry: FrontierEntry) -> None:
        """Durably append one URL transition without rewriting the frontier."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": FRONTIER_SCHEMA_VERSION,
            "entry": entry.to_json(),
        }
        with self.journal_path.open("a", encoding="utf-8") as journal:
            if self._journal_needs_separator and self.journal_path.stat().st_size:
                journal.write("\n")
            self._journal_needs_separator = False
            journal.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            journal.write("\n")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = utc_now_iso()
        if self.created_at is None:
            self.created_at = now
        self.updated_at = now
        data = {
            "schema_version": FRONTIER_SCHEMA_VERSION,
            "start_url": self.start_url,
            "run_fingerprint": self.run_fingerprint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "entries": [entry.to_json() for entry in self.entries.values()],
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self.journal_path.unlink(missing_ok=True)
            self._journal_needs_separator = False
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def flush(self) -> None:
        """Compact pending journal transitions into the stable snapshot."""
        if self.journal_path.exists():
            self.save()

    def initialize(self, *, start_url: str, run_fingerprint: dict[str, object]) -> None:
        if self.start_url != start_url or self.run_fingerprint != run_fingerprint:
            self.entries.clear()
            self.created_at = utc_now_iso()
        self.start_url = start_url
        self.run_fingerprint = run_fingerprint
        self.save()

    def compatible(self, *, start_url: str, run_fingerprint: dict[str, object]) -> bool:
        return self.start_url == start_url and self.run_fingerprint == run_fingerprint

    def add(self, url: str, *, depth: int | None = None, source: str | None = None) -> None:
        if url in self.entries:
            return
        self.entries[url] = FrontierEntry(url=url, depth=depth, source=source)

    def add_many(self, urls: list[str], *, source: str | None = None) -> None:
        for url in urls:
            self.add(url, source=source)

    def mark_processing(self, url: str) -> None:
        entry = self.entries.get(url)
        if not entry:
            self.add(url)
            entry = self.entries[url]
        entry.state = FrontierState.PROCESSING
        entry.attempts += 1
        entry.updated_at = utc_now_iso()
        self._append_entry(entry)

    def mark_succeeded(self, url: str) -> None:
        self._mark_terminal(url, FrontierState.SUCCEEDED)

    def mark_skipped(self, url: str) -> None:
        self._mark_terminal(url, FrontierState.SKIPPED)

    def mark_failed(self, url: str, error: str | None = None) -> None:
        self._mark_terminal(url, FrontierState.FAILED, error=error)

    def _mark_terminal(self, url: str, state: FrontierState, error: str | None = None) -> None:
        entry = self.entries.get(url)
        if not entry:
            self.add(url)
            entry = self.entries[url]
        entry.state = state
        entry.last_error = error
        entry.updated_at = utc_now_iso()
        self._append_entry(entry)

    def pending_urls(self) -> list[str]:
        terminal = {FrontierState.SUCCEEDED, FrontierState.SKIPPED}
        return [url for url, entry in self.entries.items() if entry.state not in terminal]

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.journal_path.unlink(missing_ok=True)
        self.entries.clear()
        self.start_url = None
        self.run_fingerprint = None
        self.created_at = None
        self.updated_at = None
        self._journal_needs_separator = False
