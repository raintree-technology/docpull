from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from docpull_bench.fixtures import verify_fixture_manifest

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_manifest_verifies_and_assets_regenerate_byte_for_byte(tmp_path: Path) -> None:
    manifest_path = ROOT / "fixtures" / "manifest.json"
    before = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    payload = verify_fixture_manifest(manifest_path)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_fixtures.py")], check=True)
    after = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert before == after
    assert any(entry["path"].endswith("03-document.docx") for entry in payload["files"])
    assert any(entry["path"].endswith("04-text.pdf") for entry in payload["files"])


def test_fixture_hash_mismatch_fails(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("content", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "files": [{"path": "file.txt", "bytes": 7, "sha256": "0" * 64}],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_fixture_manifest(path)
