from __future__ import annotations

import shutil
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from docpull_bench.challenges import (
    export_blinded_challenge,
    materialize_blinded_challenge,
    seal_blinded_gold,
)
from docpull_bench.models import BenchmarkSuite

ROOT = Path(__file__).resolve().parents[1]


def test_blinded_challenge_round_trip_keeps_gold_private(tmp_path: Path) -> None:
    source = ROOT / "cases" / "live-search-v2.yaml"
    private_draft = tmp_path / "draft.yaml"
    shutil.copyfile(source, private_draft)
    challenge = tmp_path / "public" / "challenge.yaml"
    gold = tmp_path / "private" / "gold.yaml"
    manifest = tmp_path / "public" / "manifest.json"
    created = export_blinded_challenge(
        private_draft,
        challenge_path=challenge,
        gold_path=gold,
        manifest_path=manifest,
        minimum_cases_per_lane=30,
        minimum_holdout_cases_per_lane=1,
    )
    assert created.case_count == 30
    original = BenchmarkSuite.from_yaml(private_draft)
    first_held = next(case for case in original.cases if case.metadata.split == "test")
    assert first_held.input.query not in challenge.read_text(encoding="utf-8")
    assert "expected:" in gold.read_text(encoding="utf-8")
    assert stat.S_IMODE(gold.stat().st_mode) == 0o600

    materialized_path = tmp_path / "private" / "materialized.yaml"
    materialized = materialize_blinded_challenge(challenge, gold, output_path=materialized_path)
    assert materialized == original
    assert stat.S_IMODE(materialized_path.stat().st_mode) == 0o600


def test_challenge_refuses_plaintext_gold_inside_repository(tmp_path: Path) -> None:
    source = ROOT / "cases" / "live-search-v2.yaml"
    private_draft = tmp_path / "draft.yaml"
    shutil.copyfile(source, private_draft)
    with pytest.raises(ValueError, match="plaintext gold must stay outside"):
        export_blinded_challenge(
            private_draft,
            challenge_path=tmp_path / "challenge.yaml",
            gold_path=ROOT / "do-not-create-gold.yaml",
            manifest_path=tmp_path / "manifest.json",
            minimum_cases_per_lane=30,
            minimum_holdout_cases_per_lane=1,
        )


def test_challenge_rejects_tampered_private_holdout(tmp_path: Path) -> None:
    source = ROOT / "cases" / "live-search-v2.yaml"
    private_draft = tmp_path / "draft.yaml"
    shutil.copyfile(source, private_draft)
    challenge = tmp_path / "public" / "challenge.yaml"
    gold = tmp_path / "private" / "gold.yaml"
    export_blinded_challenge(
        private_draft,
        challenge_path=challenge,
        gold_path=gold,
        manifest_path=tmp_path / "public" / "manifest.json",
        minimum_cases_per_lane=30,
        minimum_holdout_cases_per_lane=1,
    )
    payload = yaml.safe_load(gold.read_text(encoding="utf-8"))
    payload["held_cases"][0]["input"]["query"] = "tampered after sealing"
    gold.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="public commitment"):
        materialize_blinded_challenge(
            challenge,
            gold,
            output_path=tmp_path / "private" / "materialized.yaml",
        )


def test_seal_blinded_gold_uses_age_and_writes_hash_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gold = tmp_path / "private-gold.yaml"
    gold.write_text("private: gold\n", encoding="utf-8")
    ciphertext = tmp_path / "private-gold.age"
    manifest = tmp_path / "seal.json"
    monkeypatch.setattr("docpull_bench.challenges._git_root", lambda _path: None)
    monkeypatch.setattr("docpull_bench.challenges.shutil.which", lambda name: f"/usr/bin/{name}")

    def fake_run(command, **kwargs):
        output = Path(command[command.index("--output") + 1])
        output.write_bytes(b"age-encrypted-fixture")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("docpull_bench.challenges.subprocess.run", fake_run)

    artifact = seal_blinded_gold(
        gold,
        ciphertext_path=ciphertext,
        recipient="age1example",
        manifest_path=manifest,
    )

    assert artifact.status == "encrypted-requires-external-signature"
    assert len(artifact.ciphertext_sha256) == 64
    assert stat.S_IMODE(ciphertext.stat().st_mode) == 0o600
    assert manifest.is_file()
