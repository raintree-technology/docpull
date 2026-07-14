from __future__ import annotations

import json
from pathlib import Path

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


def test_full_runner_writes_reproducible_content_free_manifest(tmp_path: Path) -> None:
    report, run_dir = run_suite(
        ROOT / "cases" / "controlled-v2.yaml",
        ReplayAdapter(system="fixture", version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
        output_dir=tmp_path / "runs",
        progress=False,
        command=["docpull-bench", "run", "--command", "secret invocation"],
        environment_label="ci-container",
        network_isolation="enforced",
    )
    assert report.summary.case_count == 212
    assert report.summary.trial_pass_rate == 1
    assert report.manifest.git_revision
    assert report.manifest.git_dirty
    assert report.manifest.dependency_lock_sha256
    assert report.manifest.fixture_manifest_sha256
    assert report.manifest.command[-1] == "[REDACTED_ADAPTER_COMMAND]"
    serialized = (run_dir / "report.json").read_text(encoding="utf-8")
    assert "extract-marker-01" not in serialized
    assert "deterministic evidence" not in serialized
    assert "secret invocation" not in serialized
    assert not any(observation.artifacts for observation in report.observations)


def test_report_urls_strip_secret_query_parameters(tmp_path: Path) -> None:
    replay = tmp_path / "replays"
    replay.mkdir()
    case_id = "controlled.extract.article"
    source = json.loads((ROOT / "replays" / "controlled-v2" / "extract.fixture.01.json").read_text())
    source["case_id"] = case_id
    source["payload"]["records"][0]["url"] = "https://example.com/a?token=secret&view=1#frag"
    source["payload"]["selected_urls"] = [source["payload"]["records"][0]["url"]]
    (replay / f"{case_id}.json").write_text(json.dumps(source), encoding="utf-8")
    # A single selected case is sufficient to inspect portable URL sanitation.
    report, _ = run_suite(
        ROOT / "cases" / "controlled-v1.yaml",
        ReplayAdapter(system="fixture", version="2", replay_dir=replay),
        output_dir=tmp_path / "runs",
        case_ids={case_id},
        progress=False,
    )
    assert "token=%5BREDACTED%5D" in report.observations[0].records[0].url
    assert "secret" not in report.model_dump_json()
