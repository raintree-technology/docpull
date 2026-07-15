from __future__ import annotations

import hashlib
import json
from pathlib import Path

from docpull_bench.lifecycle import (
    LifecycleReport,
    publish_lifecycle_report,
    run_lifecycle_benchmark,
)


def test_controlled_lifecycle_suite_passes_and_writes_portable_reports(tmp_path: Path) -> None:
    report, run_dir = run_lifecycle_benchmark(output_dir=tmp_path / "runs")

    assert report.check_count == 10
    assert report.passed_count == 10
    assert report.pass_rate == 1
    assert {check.id for check in report.checks} == {
        "raw-contract",
        "eval-grade-contract",
        "stable-identities",
        "exact-diff",
        "offline-cited-search",
        "agent-exports",
        "context-ci",
        "lockfile-drift",
        "credential-non-persistence",
        "zero-budget-block",
    }
    report_path = run_dir / "report.json"
    written = LifecycleReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    portable_text = report_path.read_text(encoding="utf-8")
    assert written.pass_rate == 1
    assert "docpull-lifecycle-" not in portable_text
    assert "DOCPULL_LIFECYCLE_SENTINEL_SECRET" not in portable_text
    assert (run_dir / "REPORT.md").exists()

    publication = publish_lifecycle_report(report, output_dir=tmp_path / "publication")
    manifest = json.loads((publication / "publication.manifest.json").read_text(encoding="utf-8"))
    for name, expected_hash in manifest["files"].items():
        assert hashlib.sha256((publication / name).read_bytes()).hexdigest() == expected_hash
