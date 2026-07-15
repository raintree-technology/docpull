from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import docpull_bench.publication as publication_module
from docpull_bench.adapters import ReplayAdapter
from docpull_bench.publication import (
    publish_results,
    sign_publication,
    verify_publication,
)
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]


def _report_paths(tmp_path: Path, systems: tuple[str, ...] = ("system-a", "system-b")) -> list[Path]:
    reports: list[Path] = []
    for system in systems:
        _, run_dir = run_suite(
            ROOT / "cases" / "lifecycle-v2.yaml",
            ReplayAdapter(system=system, version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
            output_dir=tmp_path / "runs",
            progress=False,
        )
        reports.append(run_dir / "report.json")
    return reports


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


def test_publication_is_atomic_when_generation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "publication"

    def fail_methodology(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("injected generation failure")

    monkeypatch.setattr(publication_module, "_methodology", fail_methodology)

    with pytest.raises(RuntimeError, match="injected generation failure"):
        publish_results(
            ROOT / "cases" / "lifecycle-v2.yaml",
            _report_paths(tmp_path),
            output_dir=output,
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".publication.*"))


def test_publication_rejects_portable_filename_collisions(tmp_path: Path) -> None:
    output = tmp_path / "publication"

    with pytest.raises(ValueError, match="collide"):
        publish_results(
            ROOT / "cases" / "lifecycle-v2.yaml",
            _report_paths(tmp_path, ("System A", "system-a")),
            output_dir=output,
        )

    assert not output.exists()


def test_publication_verification_recomputes_manifest_and_generated_documents(tmp_path: Path) -> None:
    bundle = publish_results(
        ROOT / "cases" / "lifecycle-v2.yaml",
        _report_paths(tmp_path),
        output_dir=tmp_path / "publication",
    )
    manifest_path = bundle / "publication.manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_suite_name = manifest["suite_name"]
    manifest["suite_name"] = "forged suite name"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="suite_name"):
        verify_publication(bundle)

    manifest["suite_name"] = original_suite_name
    readme = bundle / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "forged narrative\n", encoding="utf-8")
    manifest["files"]["README.md"] = hashlib.sha256(readme.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="README"):
        verify_publication(bundle)


def test_publication_verification_rejects_ambiguous_json_and_symlinks(tmp_path: Path) -> None:
    bundle = publish_results(
        ROOT / "cases" / "lifecycle-v2.yaml",
        _report_paths(tmp_path),
        output_dir=tmp_path / "publication",
    )
    manifest_path = bundle / "publication.manifest.json"
    original = manifest_path.read_text(encoding="utf-8")
    manifest_path.write_text(
        original.replace('"schema_version": 3', '"schema_version": 3, "schema_version": 3', 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="manifest is missing or invalid"):
        verify_publication(bundle)

    manifest_path.write_text(original, encoding="utf-8")
    nested_sidecar = bundle / "reports" / "publication.manifest.json"
    nested_sidecar.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="file set mismatch"):
        verify_publication(bundle)
    nested_sidecar.unlink()

    readme = bundle / "README.md"
    original_readme = readme.read_bytes()
    readme.unlink()
    target = tmp_path / "readme-target.md"
    target.write_bytes(original_readme)
    readme.symlink_to(target)
    with pytest.raises(ValueError, match="symlinks"):
        verify_publication(bundle)


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
    assert verified["signer"] == "trusted"
    with pytest.raises(ValueError, match="trusted GPG fingerprint"):
        verify_publication(bundle, trusted_gpg_fingerprint="0" * 40)
    shutil.rmtree(gnupg_home, ignore_errors=True)


def test_signing_rejects_manifest_changed_during_gpg_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = publish_results(
        ROOT / "cases" / "lifecycle-v2.yaml",
        _report_paths(tmp_path),
        output_dir=tmp_path / "publication",
    )
    manifest = bundle / "publication.manifest.json"
    signature = bundle / "publication.manifest.json.asc"

    def mutate_while_signing(*_args: object, **_kwargs: object) -> SimpleNamespace:
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        signature.write_text("fixture signature", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(publication_module.subprocess, "run", mutate_while_signing)

    with pytest.raises(ValueError, match="changed while it was being signed"):
        sign_publication(bundle)

    assert not signature.exists()
