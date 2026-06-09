"""Tests for the pass^k analyzer."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.passk import main as passk_main
from docpull.passk import pass_at_k


def _case(name: str, provider: str, runs: list[int]) -> dict:
    return {
        "name": name,
        "provider": provider,
        "pack_score": {"score": runs[len(runs) // 2], "score_runs": runs},
        "benchmark_score": {"score": runs[len(runs) // 2], "score_runs": runs},
    }


def test_pass_at_k_all_pass() -> None:
    cases = [_case("a/x", "alpha", [95, 96, 94]), _case("b/x", "alpha", [90, 92, 91])]
    result = pass_at_k(cases, score_key="benchmark_score", threshold=90)
    assert result["cases_total"] == 2
    assert result["cases_passed"] == 2
    assert result["rate"] == 1.0
    assert result["k"] == 3
    assert result["failures"] == []


def test_pass_at_k_one_failure() -> None:
    cases = [
        _case("a/x", "alpha", [95, 96, 94]),
        _case("b/x", "alpha", [89, 92, 91]),  # worst=89 fails @90
    ]
    result = pass_at_k(cases, score_key="benchmark_score", threshold=90)
    assert result["cases_passed"] == 1
    assert result["rate"] == 0.5
    assert len(result["failures"]) == 1
    assert result["failures"][0]["worst"] == 89


def test_pass_at_k_per_provider_breakdown() -> None:
    cases = [
        _case("a/x", "alpha", [95, 96, 94]),
        _case("b/x", "alpha", [80, 81, 82]),
        _case("c/x", "beta", [95, 95, 95]),
    ]
    result = pass_at_k(cases, score_key="benchmark_score", threshold=90)
    assert result["by_provider"]["alpha"] == {"total": 2, "passed": 1}
    assert result["by_provider"]["beta"] == {"total": 1, "passed": 1}


def test_pass_at_k_skips_cases_without_runs() -> None:
    cases = [
        _case("a/x", "alpha", [95, 96, 94]),
        {"name": "b/x", "provider": "alpha", "pack_score": None, "benchmark_score": None},
    ]
    result = pass_at_k(cases, score_key="benchmark_score", threshold=90)
    assert result["cases_total"] == 1


def test_pass_at_k_threshold_boundary_is_inclusive() -> None:
    cases = [_case("a/x", "alpha", [90, 95, 92])]  # worst==threshold passes
    result = pass_at_k(cases, score_key="benchmark_score", threshold=90)
    assert result["cases_passed"] == 1


def test_cli_emits_json(tmp_path: Path, capsys) -> None:
    report = {
        "schema_version": 2,
        "run_dir": str(tmp_path),
        "target_set": "x",
        "runs_per_case": 3,
        "cases": [_case("a/x", "alpha", [95, 96, 94]), _case("b/x", "alpha", [80, 81, 82])],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report))
    rc = passk_main([str(path), "--thresholds", "90", "--score", "benchmark", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["results"][0]["rate"] == 0.5
    assert out["results"][0]["cases_passed"] == 1
