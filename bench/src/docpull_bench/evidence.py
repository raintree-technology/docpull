"""Content commitments and optional encrypted benchmark-output escrow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path

from .integrity import file_sha256
from .models import RunObservation

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def canonical_output(observation: RunObservation) -> bytes:
    payload = {
        "case_id": observation.case_id,
        "status": observation.status,
        "payload": observation.payload.model_dump(mode="json") if observation.payload else None,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def output_commitment(observation: RunObservation) -> str:
    return hashlib.sha256(canonical_output(observation)).hexdigest()


def prepare_evidence_directory(
    evidence_dir: Path | None,
    *,
    recipient: str | None,
    repository_root: Path,
    run_id: str,
) -> Path | None:
    if bool(evidence_dir) != bool(recipient):
        raise ValueError("--evidence-dir and --evidence-recipient must be provided together")
    if evidence_dir is None:
        return None
    unresolved = evidence_dir.expanduser()
    if unresolved.is_symlink():
        raise ValueError("--evidence-dir cannot be a symlink")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) or run_id in {".", ".."}:
        raise ValueError("evidence run id is not safe for a directory name")
    resolved = unresolved.resolve()
    repository_root = repository_root.resolve()
    if resolved == repository_root or repository_root in resolved.parents:
        raise ValueError("--evidence-dir must be outside the repository")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(resolved, 0o700)
    _require_private_directory(resolved)
    run_dir = resolved / run_id
    run_dir.mkdir(mode=0o700)
    _require_private_directory(run_dir)
    return run_dir


def encrypt_output(
    observation: RunObservation,
    *,
    trial_index: int,
    run_dir: Path,
    recipient: str,
) -> tuple[str, str]:
    _require_private_directory(run_dir)
    if not isinstance(trial_index, int) or isinstance(trial_index, bool) or trial_index < 1:
        raise ValueError("evidence trial index must be a positive integer")
    safe_case = _SAFE_NAME.sub("-", observation.case_id).strip("-") or "case"
    destination = run_dir / f"{safe_case}.{trial_index}.json.age"
    if destination.exists() or destination.is_symlink():
        raise ValueError("encrypted evidence destination already exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=run_dir)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as ciphertext:
            process = subprocess.run(
                ["age", "--encrypt", "--recipient", recipient, "-"],
                input=canonical_output(observation),
                stdout=ciphertext,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
            ciphertext.flush()
            os.fsync(ciphertext.fileno())
        if process.returncode != 0 or not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError("age encryption failed before evidence was persisted")
        os.chmod(temporary, 0o600)
        temporary.replace(destination)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("age encryption failed before evidence was persisted") from error
    finally:
        temporary.unlink(missing_ok=True)
    digest = file_sha256(destination)
    return destination.name, digest


def _require_private_directory(path: Path) -> None:
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode) or (os.name == "posix" and stat.S_IMODE(details.st_mode) != 0o700):
        raise ValueError("evidence directories must be private mode-0700 directories")
