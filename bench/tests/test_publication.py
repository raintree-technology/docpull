from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.publication import (
    publish_results,
    sign_publication,
    verify_publication,
)
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
    assert verify_publication(output)["status"] == "valid"

    extra = output / "unexpected.txt"
    extra.write_text("unexpected", encoding="utf-8")
    with pytest.raises(ValueError, match="file set mismatch"):
        verify_publication(output)
    extra.unlink()

    comparison = output / "comparison.json"
    original = comparison.read_bytes()
    comparison.write_bytes(original + b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_publication(output)
    comparison.write_bytes(original)
    assert verify_publication(output)["status"] == "valid"


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


def test_ephemeral_gpg_publication_sign_and_trusted_verify(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not shutil.which("gpg"):
        pytest.skip("gpg is unavailable")
    reports = []
    for system in ("signed-a", "signed-b"):
        _, run_dir = run_suite(
            ROOT / "cases" / "lifecycle-v2.yaml",
            ReplayAdapter(system=system, version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
            output_dir=tmp_path / "runs",
            progress=False,
        )
        reports.append(run_dir / "report.json")
    bundle = publish_results(
        ROOT / "cases" / "lifecycle-v2.yaml",
        reports,
        output_dir=tmp_path / "signed-publication",
    )

    gnupg_home = Path("/tmp") / f"docpull-gpg-{os.getpid()}-{tmp_path.name[-6:]}"
    shutil.rmtree(gnupg_home, ignore_errors=True)
    gnupg_home.mkdir(mode=0o700)
    os.chmod(gnupg_home, 0o700)
    monkeypatch.setenv("GNUPGHOME", str(gnupg_home))
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            "DocPull Ephemeral Test <benchmark@example.invalid>",
            "ed25519",
            "sign",
            "0",
        ],
        check=True,
        capture_output=True,
    )
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    fingerprint = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))

    signature = sign_publication(bundle, key=fingerprint)

    assert signature.is_file()
    verified = verify_publication(bundle, trusted_gpg_fingerprint=fingerprint)
    assert verified["signer"] == fingerprint
    with pytest.raises(ValueError, match="trusted GPG fingerprint"):
        verify_publication(bundle, trusted_gpg_fingerprint="0" * 40)
    shutil.rmtree(gnupg_home, ignore_errors=True)
