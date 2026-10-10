"""Archive integrity and path confinement for frozen installation."""

from __future__ import annotations

import hashlib
import stat
import zipfile
from pathlib import Path

import pytest

from docpull.frozen import FrozenArtifactError, create_run_artifact, stage_run_artifact


@pytest.mark.parametrize("name", ["../escape", "/absolute", "a/../../escape", "a\\escape", "a/./b", "C:/x"])
def test_artifact_rejects_unsafe_member_even_with_matching_digest(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "untrusted.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(name, "untrusted")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.rename(tmp_path / f"{digest}.zip")
    destination = tmp_path / "stage"
    destination.mkdir()
    with pytest.raises(FrozenArtifactError, match="Unsafe artifact entry"):
        stage_run_artifact({"schema_version": 1, "format": "zip", "sha256": digest}, tmp_path, destination)
    assert list(destination.iterdir()) == []


def test_artifact_rejects_symbolic_link_member(tmp_path: Path) -> None:
    archive = tmp_path / "untrusted.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, "../outside")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.rename(tmp_path / f"{digest}.zip")
    with pytest.raises(FrozenArtifactError, match="Unsafe artifact entry"):
        stage_run_artifact(
            {"schema_version": 1, "format": "zip", "sha256": digest}, tmp_path, tmp_path / "out"
        )
    assert not (tmp_path / "out").exists()


def test_snapshot_rejects_source_symlink(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "document").symlink_to(tmp_path / "outside")
    with pytest.raises(FrozenArtifactError, match="symbolic link"):
        create_run_artifact(run, tmp_path / "store")
    assert not list((tmp_path / "store").glob("*.zip"))


def test_snapshot_is_deterministic_and_preserves_acquisition_artifacts(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    (run / "documents.ndjson").write_text("任意の UTF-8 content\n")
    (run / "_fetch").mkdir()
    (run / "_fetch/raw.html").write_text("<p>retained source evidence</p>")
    first = create_run_artifact(run, tmp_path / "store")
    second = create_run_artifact(run, tmp_path / "store")
    assert first == second
    destination = tmp_path / "out"
    stage_run_artifact(first, tmp_path / "store", destination)
    assert (destination / "documents.ndjson").read_bytes() == (run / "documents.ndjson").read_bytes()
    assert (destination / "_fetch/raw.html").read_bytes() == (run / "_fetch/raw.html").read_bytes()
