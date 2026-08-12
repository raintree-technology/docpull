from __future__ import annotations

import json
from pathlib import Path

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.audit import audit_scorer
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


def test_scorer_audit_reports_human_agreement(tmp_path: Path) -> None:
    report, run_dir = run_suite(
        ROOT / "cases" / "controlled-v1.yaml",
        ReplayAdapter(system="fixture", version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
        output_dir=tmp_path / "runs",
        case_ids={"controlled.extract.article"},
        progress=False,
    )
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "labels": [
                    {
                        "case_id": report.scores[0].case_id,
                        "trial_index": report.scores[0].trial_index,
                        "passed": report.scores[0].passed,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = audit_scorer(run_dir / "report.json", labels)

    assert result["agreement"] == 1
    assert result["cohen_kappa"] == 1
