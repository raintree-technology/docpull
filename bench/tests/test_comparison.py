from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pytest

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.comparison import (
    _holm_adjust,
    compare_reports,
    comparison_markdown,
    exact_mcnemar,
    paired_bootstrap_interval,
)
from docpull_bench.models import Lane, PairwiseComparisonRow, PortableReport
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
    assert "Provider spend" in comparison_markdown(comparison)
    assert "conditional on successful acquisition" in comparison_markdown(comparison)
    assert "(k=2)" in comparison_markdown(comparison)
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


def test_comparison_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    original = paths[1].read_text(encoding="utf-8")
    paths[1].write_text(
        original.replace('"schema_version": 3', '"schema_version": 3, "schema_version": 3', 1),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate JSON key"):
        compare_reports(paths)


def test_comparison_requires_identical_scorer_version(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    report = PortableReport.model_validate_json(paths[1].read_text())
    changed = report.model_copy(
        update={"manifest": report.manifest.model_copy(update={"scorer_version": "future-scorer"})}
    )
    paths[1].write_text(changed.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="scorer versions"):
        compare_reports(paths)


def test_comparison_rejects_conflicting_predeclared_scope(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    payload = json.loads(paths[1].read_text(encoding="utf-8"))
    case_id = payload["observations"][0]["case_id"]
    for observation in payload["observations"]:
        if observation["case_id"] == case_id:
            observation["comparison_scope"] = "boundary"
            observation["boundary_reason"] = "robots_policy"
    paths[1].write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="conflicting predeclared scope"):
        compare_reports(paths)


def test_v3_runtime_error_text_cannot_change_core_scope(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["observations"][0]["error"] = "blocked by robots.txt"
        path.write_text(json.dumps(payload), encoding="utf-8")

    comparison = compare_reports(paths)

    assert comparison.boundary_cases == {}


def test_latency_comparability_requires_environment_and_cache_match(tmp_path: Path) -> None:
    paths = _reports(tmp_path)
    report = PortableReport.model_validate_json(paths[1].read_text())
    changed = report.model_copy(
        update={"manifest": report.manifest.model_copy(update={"environment_label": "other"})}
    )
    paths[1].write_text(changed.model_dump_json(), encoding="utf-8")
    comparison = compare_reports(paths)
    assert not any(row.latency_comparable for row in comparison.rows)


def _pair(
    slice_type: Literal["overall", "scope", "split", "family"],
    slice_value: str,
    p_value: float,
) -> PairwiseComparisonRow:
    return PairwiseComparisonRow(
        lane=Lane.EXTRACT,
        slice_type=slice_type,
        slice_value=slice_value,
        system_a="a",
        system_b="b",
        common_cases=100,
        both_pass=70,
        a_only_pass=20,
        b_only_pass=5,
        neither_pass=5,
        pass_rate_delta=0.15,
        exact_mcnemar_p_value=p_value,
        holm_adjusted_p_value=p_value,
        verdict="no_significant_difference",
    )


def test_holm_correction_does_not_mix_exploratory_slices_into_overall_claims() -> None:
    rows = [_pair("overall", "all", 0.01), _pair("overall", "all", 0.02)]
    rows.extend(_pair("family", f"family-{index}", 0.001) for index in range(20))
    adjusted = _holm_adjust(rows)
    assert adjusted[0].holm_adjusted_p_value == pytest.approx(0.02)
    assert adjusted[1].holm_adjusted_p_value == pytest.approx(0.02)
    assert all(row.holm_adjusted_p_value == 0.001 for row in adjusted[2:])


def test_paired_bootstrap_interval_is_deterministic_and_contains_effect() -> None:
    outcomes = [(True, False)] * 20 + [(False, True)] * 5 + [(True, True)] * 75
    first = paired_bootstrap_interval(outcomes, seed="fixed")
    second = paired_bootstrap_interval(outcomes, seed="fixed")
    assert first == second
    assert first[0] <= 0.15 <= first[1]


def test_comparison_separates_operational_success_from_completed_output_quality() -> None:
    base = ROOT / "results" / "manual" / "2026-07-14-live-search-v2" / "reports"
    comparison = compare_reports([base / "exa-search.report.json", base / "firecrawl-search.report.json"])
    row = next(
        item for item in comparison.rows if item.slice_type == "overall" and item.system == "firecrawl-search"
    )
    assert row.completion_rate == pytest.approx(59 / 60)
    assert row.quality_eligible_trials == 59
    assert row.quality_pass_rate_completed > row.trial_pass_rate


def test_pairwise_quality_verdict_requires_operational_conformance() -> None:
    base = ROOT / "results" / "manual" / "2026-07-14-live-neutral-crawl-v2" / "reports"
    comparison = compare_reports([base / "docpull.report.json", base / "firecrawl-crawl.report.json"])
    pair = next(item for item in comparison.pairwise if item.slice_type == "overall")

    assert not pair.operationally_comparable
    assert pair.verdict == "insufficient_operational_conformance"
    markdown = comparison_markdown(comparison)
    assert "insufficient operational conformance" in markdown
    assert "| crawl | firecrawl-crawl | 8 | 0.0% | N/A |" in markdown


def test_legacy_comparison_never_infers_scope_from_runtime_errors() -> None:
    base = ROOT / "results" / "manual" / "2026-07-14-live-neutral-extract-v2" / "reports"
    comparison = compare_reports([base / "docpull.report.json", base / "firecrawl.report.json"])

    assert comparison.source_report_schema_versions == [2, 2]
    assert comparison.boundary_cases == {}
    assert not any(row.slice_type == "scope" for row in comparison.rows)
