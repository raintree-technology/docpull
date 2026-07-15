"""Content commitments and optional encrypted benchmark-output escrow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

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
    resolved = evidence_dir.expanduser().resolve()
    repository_root = repository_root.resolve()
    if resolved == repository_root or repository_root in resolved.parents:
        raise ValueError("--evidence-dir must be outside the repository")
    resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(resolved, 0o700)
    run_dir = resolved / run_id
    run_dir.mkdir(mode=0o700)
    return run_dir


def encrypt_output(
    observation: RunObservation,
    *,
    trial_index: int,
    run_dir: Path,
    recipient: str,
) -> tuple[str, str]:
    safe_case = _SAFE_NAME.sub("-", observation.case_id).strip("-") or "case"
    destination = run_dir / f"{safe_case}.{trial_index}.json.age"
    try:
        process = subprocess.run(
            [
                "age",
                "--encrypt",
                "--recipient",
                recipient,
                "--output",
                str(destination),
                "-",
            ],
            input=canonical_output(observation),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        destination.unlink(missing_ok=True)
        raise ValueError("age encryption failed before evidence was persisted") from error
    if process.returncode != 0 or not destination.is_file():
        destination.unlink(missing_ok=True)
        raise ValueError("age encryption failed before evidence was persisted")
    os.chmod(destination, 0o600)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return destination.name, digest
