"""Fail if METRICS.md has not been refreshed recently enough."""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_MAX_AGE_HOURS = 168
TIMESTAMP_RE = re.compile(r"^_Last updated: (?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\.", re.M)


def parse_timestamp(text: str) -> datetime:
    match = TIMESTAMP_RE.search(text)
    if match is None:
        raise ValueError("Could not find a METRICS.md '_Last updated:' UTC timestamp.")
    return datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def max_age_from_env() -> timedelta:
    raw = os.environ.get("METRICS_MAX_AGE_HOURS", str(DEFAULT_MAX_AGE_HOURS))
    try:
        hours = float(raw)
    except ValueError as err:
        raise ValueError(f"METRICS_MAX_AGE_HOURS must be numeric, got {raw!r}.") from err
    if hours <= 0:
        raise ValueError(f"METRICS_MAX_AGE_HOURS must be positive, got {raw!r}.")
    return timedelta(hours=hours)


def main() -> int:
    path = Path(os.environ.get("METRICS_FILE", "METRICS.md"))

    try:
        max_age = max_age_from_env()
        timestamp = parse_timestamp(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        print(f"metrics freshness check failed: {err}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    age = now - timestamp

    if age < timedelta(minutes=-5):
        print(f"{path} timestamp is in the future: {timestamp:%Y-%m-%d %H:%M UTC}", file=sys.stderr)
        return 1

    if age > max_age:
        print(
            f"{path} is stale: last updated {timestamp:%Y-%m-%d %H:%M UTC}, "
            f"age {age.total_seconds() / 3600:.1f}h exceeds {max_age.total_seconds() / 3600:.1f}h.",
            file=sys.stderr,
        )
        print("Run `make metrics` or re-run the Update metrics workflow.", file=sys.stderr)
        return 1

    print(f"{path} is fresh: last updated {timestamp:%Y-%m-%d %H:%M UTC}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
