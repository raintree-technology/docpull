from __future__ import annotations

from pathlib import Path

import pytest

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.comparison import compare_reports, comparison_markdown, exact_mcnemar
from docpull_bench.models import PortableReport
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


def _reports(tmp_path: Path) -> list[Path]:
    paths = []
    for system in ("system-a", "system-b"):
        _, run_dir = run_suite(
            ROOT / "cases" / "controlled-v2.yaml",
            ReplayAdapter(system=system, version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
            output_dir=tmp_path / "runs",
            repeat=2,
            progress=False,
            environment_label="same",
        )
        paths.append(run_dir / "report.json")
    return paths


def test_comparison_is_lane_local_and_holm_corrected(tmp_path: Path) -> None:
    comparison = compare_reports(_reports(tmp_path))
    assert comparison.system_count == 2
    assert all(row.pass_all_trials_rate == 1 for row in comparison.rows)
    assert {row.lane.value for row in comparison.rows if row.slice_type == "overall"} == {
        "extract",
        "crawl",
        "parse",
        "pack",
        "structured",
        "lifecycle",
        "change",
        "retrieval",
        "research",
        "policy",
    }
    assert all(row.holm_adjusted_p_value >= row.exact_mcnemar_p_value for row in comparison.pairwise)
    assert "No cross-lane composite" in comparison_markdown(comparison)
    assert exact_mcnemar(0, 0) == 1


def test_comparison_requires_identical_protocol_hash(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    report = PortableReport.model_validate_json(paths[1].read_text())
    changed = report.model_copy(
        update={"manifest": report.manifest.model_copy(update={"protocol_sha256": "0" * 64})}
    )
    paths[1].write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="protocol hashes"):
        compare_reports(paths)


def test_latency_comparability_requires_environment_and_cache_match(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    report = PortableReport.model_validate_json(paths[1].read_text())
    changed = report.model_copy(
        update={"manifest": report.manifest.model_copy(update={"environment_label": "other"})}
    )
    paths[1].write_text(changed.model_dump_json(), encoding="utf-8")
    comparison = compare_reports(paths)
    assert not any(row.latency_comparable for row in comparison.rows)
