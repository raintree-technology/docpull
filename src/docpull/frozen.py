"""Content-addressed local archives for frozen project installation."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


class FrozenArtifactError(RuntimeError):
    """An artifact is missing, invalid, or unsafe to restore."""


def _digest(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def run_files(root: Path) -> list[Path]:
    """List every retained run file without following symbolic links."""
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise FrozenArtifactError(f"Artifact contains a symbolic link: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise FrozenArtifactError(f"Artifact contains a non-regular file: {path}")
    return files


def run_fingerprints(root: Path) -> dict[str, str]:
    """Hash evidence files without loading the corpus into memory."""
    result = {}
    for path in run_files(root):
        if path.name == "run.json" and path.parent == root:
            continue
        with path.open("rb") as stream:
            result[path.relative_to(root).as_posix()] = _digest(stream)
    return result


def create_run_artifact(run_dir: Path, artifact_dir: Path) -> dict[str, Any]:
    """Save the completed run as a ZIP addressed by its SHA-256 digest."""
    artifact_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=artifact_dir) as temporary:
        archive = Path(temporary) / "run.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in run_files(run_dir):
                info = zipfile.ZipInfo(path.relative_to(run_dir).as_posix())
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o600) << 16
                with path.open("rb") as source, bundle.open(info, "w", force_zip64=True) as target:
                    shutil.copyfileobj(source, target)
        archive.chmod(0o600)
        with archive.open("rb") as stream:
            digest = _digest(stream)
        destination = artifact_dir / f"{digest}.zip"
        if destination.exists():
            with destination.open("rb") as stream:
                if _digest(stream) != digest:
                    raise FrozenArtifactError(f"Artifact digest mismatch: {destination}")
        else:
            os.replace(archive, destination)
    return {"schema_version": 1, "format": "zip", "sha256": digest}


def stage_run_artifact(artifact: Any, artifact_dir: Path, destination: Path) -> None:
    """Verify pinned bytes before safely unpacking into a private staging directory."""
    if (
        not isinstance(artifact, dict)
        or artifact.get("schema_version") != 1
        or artifact.get("format") != "zip"
        or not isinstance(artifact.get("sha256"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
    ):
        raise FrozenArtifactError(
            "Lockfile has no supported frozen artifact. Run `docpull sync` on the source machine first."
        )
    digest = artifact["sha256"]
    archive = artifact_dir / f"{digest}.zip"
    try:
        with archive.open("rb") as stream:
            if _digest(stream) != digest:
                raise FrozenArtifactError(f"Artifact digest mismatch: {archive}")
            stream.seek(0)
            with zipfile.ZipFile(stream) as bundle:
                seen: set[str] = set()
                for info in bundle.infolist():
                    name = info.filename
                    path = PurePosixPath(name)
                    mode = info.external_attr >> 16
                    if (
                        not name
                        or info.orig_filename != name
                        or path.is_absolute()
                        or "\\" in name
                        or ":" in name
                        or any(part in {"", ".", ".."} for part in name.split("/"))
                        or name in seen
                        or (stat.S_IFMT(mode) not in {0, stat.S_IFREG})
                    ):
                        raise FrozenArtifactError(f"Unsafe artifact entry: {name}")
                    seen.add(name)
                    target = destination.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(info) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o600)
    except FileNotFoundError as err:
        raise FrozenArtifactError(f"Missing frozen artifact: {archive}") from err
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError, NotImplementedError) as err:
        if isinstance(err, FrozenArtifactError):
            raise
        raise FrozenArtifactError(f"Cannot restore frozen artifact {archive}: {err}") from err
