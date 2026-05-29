"""UTC time helpers for persisted docpull data."""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return the current instant as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Return the current instant as an ISO-8601 UTC timestamp."""
    return utc_now().isoformat()


def parse_persisted_datetime(value: str) -> datetime:
    """Parse a stored timestamp and normalize it to timezone-aware UTC.

    Older cache files used naive local timestamps. Treat those legacy values
    as UTC so comparisons stay deterministic after newer writes include an
    explicit ``+00:00`` offset.
    """
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
