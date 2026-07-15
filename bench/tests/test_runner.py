from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.models import PortableReport
from docpull_bench.runner import _git_state, run_suite

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
    assert report.manifest.git_dirty is _git_state(ROOT)[1]
    assert report.manifest.dependency_lock_sha256
    assert report.manifest.fixture_manifest_sha256
    assert report.manifest.command[-1] == "[REDACTED_ADAPTER_COMMAND]"
    serialized = (run_dir / "report.json").read_text(encoding="utf-8")
    assert "extract-marker-01" not in serialized
    assert "deterministic evidence" not in serialized
    assert "secret invocation" not in serialized
    assert not any(observation.artifacts for observation in report.observations)
    assert report.schema_version == 3
    assert report.evidence_status == "integrity-checked-v3"
    assert report.manifest.subject is not None
    assert report.manifest.subject.kind == "replay"
    assert all(observation.normalized_output_sha256 for observation in report.observations)


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


def test_v3_report_rejects_summary_duplicate_missing_and_system_tampering(tmp_path: Path) -> None:
    _, run_dir = run_suite(
        ROOT / "cases" / "lifecycle-v2.yaml",
        ReplayAdapter(system="fixture", version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
        output_dir=tmp_path / "runs",
        repeat=2,
        progress=False,
    )
    payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))

    tampered_summary = json.loads(json.dumps(payload))
    tampered_summary["summary"]["completed"] -= 1
    with pytest.raises(ValidationError, match="summary does not match"):
        PortableReport.model_validate(tampered_summary)

    tampered_cost = json.loads(json.dumps(payload))
    tampered_cost["summary"]["observed_cost_usd"] = 999
    with pytest.raises(ValidationError, match="summary does not match"):
        PortableReport.model_validate(tampered_cost)

    forged_completion = json.loads(json.dumps(payload))
    forged_completion["observations"][0]["status"] = "unsupported"
    with pytest.raises(ValidationError, match="trial facts conflict"):
        PortableReport.model_validate(forged_completion)

    forged_pass = json.loads(json.dumps(payload))
    forged_pass["scores"][0]["passed"] = not forged_pass["scores"][0]["passed"]
    with pytest.raises(ValidationError, match="passed must equal"):
        PortableReport.model_validate(forged_pass)

    duplicate = json.loads(json.dumps(payload))
    duplicate["observations"].append(duplicate["observations"][0])
    with pytest.raises(ValidationError, match="duplicate observation"):
        PortableReport.model_validate(duplicate)

    missing = json.loads(json.dumps(payload))
    missing["scores"].pop()
    with pytest.raises(ValidationError, match="trial keys must match exactly"):
        PortableReport.model_validate(missing)

    conflicting_system = json.loads(json.dumps(payload))
    conflicting_system["observations"][0]["system"] = "forged"
    with pytest.raises(ValidationError, match="system identity"):
        PortableReport.model_validate(conflicting_system)


def test_age_evidence_escrow_round_trip_keeps_only_commitments_in_report(tmp_path: Path) -> None:
    if not shutil.which("age") or not shutil.which("age-keygen"):
        pytest.skip("age CLI is unavailable")
    identity = tmp_path / "identity.txt"
    subprocess.run(["age-keygen", "--output", str(identity)], check=True, capture_output=True)
    os.chmod(identity, 0o600)
    recipient = subprocess.run(
        ["age-keygen", "-y", str(identity)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    evidence_dir = tmp_path / "external-evidence"
    report, _ = run_suite(
        ROOT / "cases" / "controlled-v1.yaml",
        ReplayAdapter(system="fixture", version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
        output_dir=tmp_path / "runs",
        case_ids={"controlled.extract.article"},
        progress=False,
        evidence_dir=evidence_dir,
        evidence_recipient=recipient,
    )

    ciphertext = next(evidence_dir.rglob("*.age"))
    observation = report.observations[0]
    assert observation.evidence_ciphertext_sha256 == hashlib.sha256(ciphertext.read_bytes()).hexdigest()
    decrypted = subprocess.run(
        ["age", "--decrypt", "--identity", str(identity), str(ciphertext)],
        check=True,
        capture_output=True,
    ).stdout
    decrypted_payload = json.loads(decrypted)
    assert decrypted_payload["case_id"] == "controlled.extract.article"
    assert hashlib.sha256(decrypted).hexdigest() == observation.normalized_output_sha256
    serialized = report.model_dump_json()
    assert "extract-marker-01" not in serialized
    assert str(evidence_dir) not in serialized


def test_evidence_escrow_must_be_outside_repository(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside the repository"):
        run_suite(
            ROOT / "cases" / "controlled-v1.yaml",
            ReplayAdapter(
                system="fixture",
                version="2",
                replay_dir=ROOT / "replays" / "controlled-v2",
            ),
            output_dir=tmp_path / "runs",
            case_ids={"controlled.extract.article"},
            progress=False,
            evidence_dir=ROOT / "forbidden-evidence",
            evidence_recipient="age1invalid",
        )
