"""Tests for deterministic release artifact orchestration."""

from __future__ import annotations

import importlib.util
import io
import os
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_release",
        ROOT / "scripts" / "build_release.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_epoch_prefers_explicit_value_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")

    assert module._source_date_epoch() == 1700000000
    assert module._source_date_epoch(1800000000) == 1800000000


def test_release_epoch_uses_content_independent_default(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)

    assert module._source_date_epoch() == module.MINIMUM_ZIP_EPOCH


@pytest.mark.parametrize("value", ["invalid", "1", str(2**32)])
def test_release_epoch_rejects_invalid_or_pre_zip_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    module = _load_module()
    monkeypatch.setenv("SOURCE_DATE_EPOCH", value)

    with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
        module._source_date_epoch()


def test_release_build_fails_closed_on_nonempty_output(tmp_path: Path) -> None:
    module = _load_module()
    output = tmp_path / "dist"
    output.mkdir()
    (output / "stale.whl").write_bytes(b"stale")

    with pytest.raises(ValueError, match="must be empty"):
        module._build(ROOT, output, epoch=1700000000)


def test_release_clean_removes_only_recognized_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    output = tmp_path / "dist"
    output.mkdir()
    (output / "docpull-6.0.1-py3-none-any.whl").write_bytes(b"wheel")
    (output / "docpull-6.0.1.tar.gz").write_bytes(b"sdist")

    module._clean_release_artifacts(output)

    assert list(output.iterdir()) == []


@pytest.mark.parametrize("unexpected", ["notes.txt", "other-1.0.whl", "docpull-cache"])
def test_release_clean_refuses_unknown_entries_transactionally(
    tmp_path: Path,
    unexpected: str,
) -> None:
    module = _load_module()
    output = tmp_path / "dist"
    output.mkdir()
    artifact = output / "docpull-6.0.1.tar.gz"
    artifact.write_bytes(b"sdist")
    unknown = output / unexpected
    if unexpected == "docpull-cache":
        unknown.mkdir()
    else:
        unknown.write_bytes(b"user data")

    with pytest.raises(ValueError, match="refusing to clean"):
        module._clean_release_artifacts(output)

    assert artifact.exists()
    assert unknown.exists()


def test_sdist_canonicalization_removes_host_metadata(tmp_path: Path) -> None:
    module = _load_module()

    def create(name: str, *, mtime: int, uid: int) -> Path:
        archive = tmp_path / name
        with tarfile.open(archive, "w:gz") as output:
            member = tarfile.TarInfo("docpull-6.1.0/example.txt")
            member.size = 7
            member.mtime = mtime
            member.uid = uid
            member.gid = uid
            member.uname = f"user-{uid}"
            member.gname = f"group-{uid}"
            output.addfile(member, io.BytesIO(b"example"))
        return archive

    first = create("first.tar.gz", mtime=1_700_000_001, uid=501)
    second = create("second.tar.gz", mtime=1_800_000_002, uid=1000)
    module._canonicalize_sdist(first, epoch=1_900_000_000)
    module._canonicalize_sdist(second, epoch=1_900_000_000)

    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first, "r:gz") as archive:
        member = archive.getmember("docpull-6.1.0/example.txt")
    assert member.mtime == 1_900_000_000
    assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")


@pytest.mark.parametrize(
    ("name", "kind"),
    [("../escape.txt", "file"), ("/absolute.txt", "file"), ("safe/link", "symlink")],
)
def test_sdist_canonicalization_rejects_unsafe_members(
    tmp_path: Path,
    name: str,
    kind: str,
) -> None:
    module = _load_module()
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            output.addfile(member)
        else:
            member.size = 1
            output.addfile(member, io.BytesIO(b"x"))

    original = archive.read_bytes()
    with pytest.raises(RuntimeError, match="unsafe path|unsupported link"):
        module._canonicalize_sdist(archive, epoch=1_900_000_000)

    assert archive.read_bytes() == original


def test_release_paths_use_reproducibility_verification() -> None:
    required = "scripts/build_release.py --verify-reproducible"
    for path in (
        ROOT / ".github" / "workflows" / "context-benchmark-lab.yml",
        ROOT / "docs" / "release.md",
    ):
        assert required in path.read_text(encoding="utf-8"), path

    release_gate = (ROOT / "scripts" / "release_a_plus_check.py").read_text(encoding="utf-8")
    assert '"scripts/build_release.py", "--verify-reproducible"' in release_gate


def test_release_script_does_not_mutate_parent_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_module()
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    before = os.environ.copy()

    assert module._source_date_epoch(1700000000) == 1700000000
    assert os.environ == before
