from __future__ import annotations

from datetime import timezone

from docpull.time_utils import parse_persisted_datetime, utc_now_iso


def test_utc_now_iso_is_timezone_explicit() -> None:
    assert utc_now_iso().endswith("+00:00")


def test_parse_persisted_datetime_normalizes_legacy_naive_values() -> None:
    parsed = parse_persisted_datetime("2026-04-26T00:00:00")

    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-04-26T00:00:00+00:00"


def test_parse_persisted_datetime_accepts_z_suffix() -> None:
    parsed = parse_persisted_datetime("2026-04-26T00:00:00Z")

    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-04-26T00:00:00+00:00"
