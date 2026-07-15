"""Explicit, content-free controlled baseline management."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .integrity import file_sha256, load_portable_report, strict_json_file
from .models import CaseScore


def update_baseline(report_path: Path, baseline_path: Path, *, reason: str) -> dict[str, Any]:
    if not reason.strip():
        raise ValueError("baseline update requires a non-empty reason")
    report = load_portable_report(report_path)
    previous_hash = _optional_hash(baseline_path)
    payload = {
        "schema_version": 2,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason.strip(),
        "previous_sha256": previous_hash,
        "source_report_sha256": file_sha256(report_path),
        "suite_sha256": report.manifest.suite_sha256,
        "protocol_sha256": report.manifest.protocol_sha256,
        "system": report.manifest.system,
        "adapter_version": report.manifest.adapter_version,
        "environment_label": report.manifest.environment_label,
        "cache_policy": report.manifest.cache_policy,
        "cases": _case_snapshot(report.scores),
    }
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def check_baseline(report_path: Path, baseline_path: Path) -> tuple[dict[str, Any], bool]:
    report = load_portable_report(report_path)
    baseline = strict_json_file(baseline_path)
    if baseline.get("schema_version") != 2:
        raise ValueError("baseline is not schema v2")
    if baseline.get("suite_sha256") != report.manifest.suite_sha256:
        raise ValueError("baseline and report suite hashes differ")
    if baseline.get("protocol_sha256") != report.manifest.protocol_sha256:
        raise ValueError("baseline and report protocol hashes differ")
    current = _case_snapshot(report.scores)
    rows: list[dict[str, Any]] = []
    blocking = False
    advisory: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline["cases"]) | set(current)):
        old = baseline["cases"].get(case_id)
        new = current.get(case_id)
        classification = _classification(old, new)
        critical = bool((new or old or {}).get("critical", False))
        if classification == "regression" and critical:
            blocking = True
        rows.append(
            {
                "case_id": case_id,
                "lane": (new or old or {}).get("lane"),
                "critical": critical,
                "classification": classification,
                "baseline_passed": None if old is None else old["passed"],
                "current_passed": None if new is None else new["passed"],
            }
        )
        if old and new:
            advisory.extend(_performance_advisories(case_id, old, new))
    result = {
        "schema_version": 2,
        "baseline_sha256": file_sha256(baseline_path),
        "report_sha256": file_sha256(report_path),
        "blocking_regression": blocking,
        "rows": rows,
        "performance_advisories": advisory,
    }
    return result, not blocking


def _case_snapshot(scores: list[CaseScore]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        grouped[score.case_id].append(score)
    return {
        case_id: {
            "lane": items[0].lane.value,
            "critical": items[0].critical,
            "status": "unsupported" if all(item.status == "unsupported" for item in items) else "observed",
            "passed": all(item.passed for item in items),
            "p50_elapsed_seconds": median(item.elapsed_seconds for item in items),
            "p95_elapsed_seconds": _percentile([item.elapsed_seconds for item in items], 0.95),
            "peak_rss_bytes": max(
                (item.peak_rss_bytes or 0 for item in items),
                default=0,
            ),
        }
        for case_id, items in grouped.items()
    }


def _classification(old: dict[str, Any] | None, new: dict[str, Any] | None) -> str:
    if new is None:
        return "regression"
    if new["status"] == "unsupported":
        return "unsupported"
    if old is None:
        return "improvement" if new["passed"] else "persistent_gap"
    if old["status"] == "unsupported" and new["status"] != "unsupported":
        return "improvement"
    if old["passed"] and not new["passed"]:
        return "regression"
    if not old["passed"] and new["passed"]:
        return "improvement"
    if not new["passed"]:
        return "persistent_gap"
    return "unchanged"


def _performance_advisories(case_id: str, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for key, floor in (
        ("p50_elapsed_seconds", 0.1),
        ("p95_elapsed_seconds", 0.1),
        ("peak_rss_bytes", 10 * 1024 * 1024),
    ):
        baseline = float(old.get(key, 0))
        current = float(new.get(key, 0))
        delta = current - baseline
        if baseline > 0 and current > baseline * 1.2 and delta >= floor:
            output.append(
                {
                    "case_id": case_id,
                    "metric": key,
                    "baseline": baseline,
                    "current": current,
                    "relative_change": current / baseline - 1,
                    "blocking": False,
                }
            )
    return output


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * quantile + 0.999999) - 1))]


def _hash(path: Path) -> str:
    return file_sha256(path)


def _optional_hash(path: Path) -> str | None:
    return _hash(path) if path.exists() else None
