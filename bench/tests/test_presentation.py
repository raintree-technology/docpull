from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from docpull_bench.presentation import create_presentation, verify_legacy_publication, verify_presentation


def _source_bundle(root: Path) -> Path:
    bundle = root / "source"
    bundle.mkdir()
    (bundle / "README.md").write_text("# Fixed result\n\nA bounded [report](REPORT.md).\n", encoding="utf-8")
    (bundle / "REPORT.md").write_text("# Report\n", encoding="utf-8")
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in bundle.iterdir()
        if path.name != "publication.manifest.json"
    }
    (bundle / "publication.manifest.json").write_text(
        json.dumps({"schema_version": 2, "status": "data-only", "files": files}) + "\n",
        encoding="utf-8",
    )
    return bundle


def test_presentation_references_source_digest_without_changing_source(tmp_path: Path) -> None:
    bundle = _source_bundle(tmp_path)
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in bundle.iterdir()}
    output = create_presentation(bundle, output_dir=tmp_path / "presentation")

    assert verify_legacy_publication(bundle)["status"] == "valid"
    assert verify_presentation(output)["status"] == "valid"
    summary = (output / "SUMMARY.md").read_text(encoding="utf-8")
    assert "not new experimental evidence" in summary
    assert "## Fixed result" in summary
    assert "[report](../source/REPORT.md)" in summary
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in bundle.iterdir()}
    assert after == before

    create_presentation(bundle, output_dir=output)
    assert verify_presentation(output)["status"] == "valid"


def test_presentation_fails_when_source_manifest_changes(tmp_path: Path) -> None:
    bundle = _source_bundle(tmp_path)
    output = create_presentation(bundle, output_dir=tmp_path / "presentation")
    (bundle / "publication.manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="source publication manifest changed"):
        verify_presentation(output)


def test_legacy_verifier_fails_when_a_source_file_changes(tmp_path: Path) -> None:
    bundle = _source_bundle(tmp_path)
    (bundle / "REPORT.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(ValueError, match="historical publication file changed"):
        verify_legacy_publication(bundle)
