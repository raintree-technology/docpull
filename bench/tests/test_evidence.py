from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull_bench.evidence import encrypt_output, prepare_evidence_directory
from docpull_bench.models import RunObservation


def _observation() -> RunObservation:
    return RunObservation(
        case_id="case.one",
        system="system",
        status="failed",
        elapsed_seconds=0,
        adapter_version="1",
        error="expected fixture failure",
    )


def test_evidence_directory_rejects_symlink_and_unsafe_run_id(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "evidence-link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        prepare_evidence_directory(
            link,
            recipient="age1fixture",
            repository_root=repository,
            run_id="run-1",
        )
    with pytest.raises(ValueError, match="run id"):
        prepare_evidence_directory(
            tmp_path / "evidence",
            recipient="age1fixture",
            repository_root=repository,
            run_id="../escape",
        )
    assert not (tmp_path / "evidence").exists()


def test_failed_age_encryption_leaves_no_partial_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_dir = tmp_path / "evidence" / "run-1"
    run_dir.mkdir(parents=True, mode=0o700)
    run_dir.chmod(0o700)
    monkeypatch.setattr(
        "docpull_bench.evidence.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    with pytest.raises(ValueError, match="age encryption failed"):
        encrypt_output(
            _observation(),
            trial_index=1,
            run_dir=run_dir,
            recipient="age1fixture",
        )

    assert list(run_dir.iterdir()) == []
