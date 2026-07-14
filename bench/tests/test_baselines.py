from __future__ import annotations

from pathlib import Path

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.baselines import check_baseline, update_baseline
from docpull_bench.models import PortableReport
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


def _report(tmp_path: Path) -> Path:
    _, run_dir = run_suite(
        ROOT / "cases" / "lifecycle-v2.yaml",
        ReplayAdapter(system="fixture", version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
        output_dir=tmp_path / "runs",
        progress=False,
    )
    return run_dir / "report.json"


def test_baseline_update_is_explicit_and_records_previous_hash(tmp_path: Path) -> None:
    report = _report(tmp_path)
    baseline = tmp_path / "baseline.json"
    first = update_baseline(report, baseline, reason="initial controlled baseline")
    second = update_baseline(report, baseline, reason="documented refresh")
    assert first["previous_sha256"] is None
    assert second["previous_sha256"]
    result, passed = check_baseline(report, baseline)
    assert passed
    assert not result["blocking_regression"]


def test_critical_pass_to_fail_blocks_but_performance_is_advisory(tmp_path: Path) -> None:
    report_path = _report(tmp_path)
    baseline = tmp_path / "baseline.json"
    update_baseline(report_path, baseline, reason="initial")
    report = PortableReport.model_validate_json(report_path.read_text())
    first = report.scores[0]
    changed_score = first.model_copy(update={"passed": False, "elapsed_seconds": first.elapsed_seconds + 1.0})
    changed = report.model_copy(update={"scores": [changed_score, *report.scores[1:]]})
    report_path.write_text(changed.model_dump_json(), encoding="utf-8")
    result, passed = check_baseline(report_path, baseline)
    assert not passed
    assert result["blocking_regression"]
    row = next(item for item in result["rows"] if item["case_id"] == first.case_id)
    assert row["classification"] == "regression"
    # Performance findings never flip the blocking decision on their own.
    assert all(not item["blocking"] for item in result["performance_advisories"])


def test_baseline_requires_reason(tmp_path: Path) -> None:
    report = _report(tmp_path)
    try:
        update_baseline(report, tmp_path / "baseline.json", reason="  ")
    except ValueError as error:
        assert "reason" in str(error)
    else:
        raise AssertionError("empty reason must fail")
