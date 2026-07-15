from __future__ import annotations

import json
from pathlib import Path

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.publication import publish_results
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


def test_publication_is_content_free_data_without_generated_claims(tmp_path: Path) -> None:
    reports = []
    for system in ("system-a", "system-b"):
        _, run_dir = run_suite(
            ROOT / "cases" / "controlled-v2.yaml",
            ReplayAdapter(system=system, version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
            output_dir=tmp_path / "runs",
            progress=False,
        )
        reports.append(run_dir / "report.json")
    output = publish_results(
        ROOT / "cases" / "controlled-v2.yaml",
        reports,
        output_dir=tmp_path / "publication",
        unavailable=["system-c=no compatible adapter"],
    )
    manifest = json.loads((output / "publication.manifest.json").read_text())
    assert manifest["status"] == "data-only"
    assert len(manifest["source_report_set_sha256"]) == 64
    readme = (output / "README.md").read_text()
    assert "does not generate product claims" in readme
    assert "winner" in readme
    public_report = (output / "reports" / "system-a.report.json").read_text()
    assert "extract-marker-01" not in public_report
    assert '"artifacts": {}' in public_report


def test_provisional_publication_has_unmissable_watermark(tmp_path: Path) -> None:
    reports = []
    for system in ("a", "b"):
        _, run_dir = run_suite(
            ROOT / "cases" / "lifecycle-v2.yaml",
            ReplayAdapter(system=system, version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
            output_dir=tmp_path / "runs",
            progress=False,
        )
        reports.append(run_dir / "report.json")
    output = publish_results(
        ROOT / "cases" / "lifecycle-v2.yaml",
        reports,
        output_dir=tmp_path / "provisional",
        provisional=True,
    )
    assert "NOT CURRENT EVIDENCE" in (output / "README.md").read_text()
    assert "not-for-marketing" in (output / "publication.manifest.json").read_text()
