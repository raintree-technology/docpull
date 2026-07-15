"""Blinded challenge packaging for never-published benchmark holdouts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, model_validator

from .claims import gold_hash
from .models import BenchmarkCase, BenchmarkSuite, Lane, StrictModel


class BenchmarkChallenge(StrictModel):
    """Public development set plus commitments to entirely hidden test cases."""

    challenge_schema_version: Literal[1] = 1
    name: str
    version: str
    description: str
    fixture_manifest_sha256: str | None = None
    development_cases: list[BenchmarkCase] = Field(min_length=1)
    held_case_ids: list[str] = Field(min_length=1)
    held_case_commitment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_order: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_partition(self) -> BenchmarkChallenge:
        development_ids = [case.id for case in self.development_cases]
        all_ids = development_ids + self.held_case_ids
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("challenge case ids must be unique")
        if set(all_ids) != set(self.case_order):
            raise ValueError("case_order must cover development and held cases")
        if any(case.metadata.split != "dev" for case in self.development_cases):
            raise ValueError("public challenge cases must use the development split")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> BenchmarkChallenge:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class BenchmarkGold(StrictModel):
    """Private bundle containing both inputs and gold for every held case."""

    gold_schema_version: Literal[1] = 1
    challenge_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str
    version: str
    held_cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def only_held_cases(self) -> BenchmarkGold:
        if any(case.metadata.split != "test" for case in self.held_cases):
            raise ValueError("private holdout may contain only test cases")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> BenchmarkGold:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class UnsignedChallengeManifest(StrictModel):
    schema_version: Literal[1] = 1
    status: Literal["unsigned-unencrypted-not-claim-evidence"]
    generated_at: str
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    challenge_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gold_file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    gold_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int = Field(ge=1)
    held_case_ids: list[str] = Field(min_length=1)


def export_blinded_challenge(
    suite_path: Path,
    *,
    challenge_path: Path,
    gold_path: Path,
    manifest_path: Path,
    minimum_cases_per_lane: int = 100,
    minimum_holdout_cases_per_lane: int = 30,
) -> UnsignedChallengeManifest:
    """Split a private draft into public development data and a hidden holdout.

    The draft and holdout must be outside the repository. Encryption and an
    external signature happen after this step; the generated manifest is
    intentionally unacceptable to the public-claim gate.
    """
    repository = _git_root(Path.cwd())
    _require_outside_repository(suite_path, repository, "draft suite")
    _require_outside_repository(gold_path, repository, "plaintext gold")
    suite = BenchmarkSuite.from_yaml(suite_path)
    counts: dict[Lane, Counter[str]] = defaultdict(Counter)
    for case in suite.cases:
        counts[case.input.lane].update([case.metadata.split])
    failures = [
        f"{lane.value}: total={sum(split.values())}, test={split['test']}"
        for lane, split in counts.items()
        if sum(split.values()) < minimum_cases_per_lane or split["test"] < minimum_holdout_cases_per_lane
    ]
    if failures:
        raise ValueError("challenge is below minimum sample policy: " + "; ".join(failures))

    held_cases = [case for case in suite.cases if case.metadata.split == "test"]
    challenge = BenchmarkChallenge(
        name=suite.name,
        version=suite.version,
        description=suite.description,
        fixture_manifest_sha256=suite.fixture_manifest_sha256,
        development_cases=[case for case in suite.cases if case.metadata.split == "dev"],
        held_case_ids=sorted(case.id for case in held_cases),
        held_case_commitment_sha256=_json_hash(sorted(held_cases, key=lambda case: case.id)),
        case_order=[case.id for case in suite.cases],
    )
    challenge_path.parent.mkdir(parents=True, exist_ok=True)
    challenge_path.write_text(
        yaml.safe_dump(challenge.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    challenge_sha256 = _file_sha256(challenge_path)
    gold = BenchmarkGold(
        challenge_sha256=challenge_sha256,
        name=suite.name,
        version=suite.version,
        held_cases=held_cases,
    )
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.write_text(
        yaml.safe_dump(gold.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(gold_path, 0o600)
    manifest = UnsignedChallengeManifest(
        status="unsigned-unencrypted-not-claim-evidence",
        generated_at=datetime.now(timezone.utc).isoformat(),
        suite_sha256=_file_sha256(suite_path),
        challenge_sha256=challenge_sha256,
        gold_file_sha256=_file_sha256(gold_path),
        gold_sha256=gold_hash(suite),
        case_count=len(suite.cases),
        held_case_ids=sorted(case.id for case in held_cases),
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest


def materialize_blinded_challenge(
    challenge_path: Path, gold_path: Path, *, output_path: Path
) -> BenchmarkSuite:
    """Recombine public development data and decrypted holdout for a private run."""
    repository = _git_root(Path.cwd())
    _require_outside_repository(gold_path, repository, "plaintext gold")
    _require_outside_repository(output_path, repository, "materialized private suite")
    challenge = BenchmarkChallenge.from_yaml(challenge_path)
    gold = BenchmarkGold.from_yaml(gold_path)
    if gold.challenge_sha256 != _file_sha256(challenge_path):
        raise ValueError("gold does not match the challenge hash")
    held_by_id = {case.id: case for case in gold.held_cases}
    if set(held_by_id) != set(challenge.held_case_ids):
        raise ValueError("challenge and private holdout case ids differ")
    held_commitment = _json_hash([held_by_id[case_id] for case_id in sorted(held_by_id)])
    if held_commitment != challenge.held_case_commitment_sha256:
        raise ValueError("private holdout does not match its public commitment")
    development_by_id = {case.id: case for case in challenge.development_cases}
    all_cases = {**development_by_id, **held_by_id}
    suite = BenchmarkSuite(
        name=challenge.name,
        version=challenge.version,
        description=challenge.description,
        fixture_manifest_sha256=challenge.fixture_manifest_sha256,
        cases=[all_cases[case_id] for case_id in challenge.case_order],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(suite.model_dump(mode="json", exclude_none=True), sort_keys=False),
        encoding="utf-8",
    )
    os.chmod(output_path, 0o600)
    return suite


def protocol_hash(challenge: BenchmarkChallenge, gold: BenchmarkGold) -> str:
    """Stable helper for external custodians comparing a challenge and gold."""
    payload: dict[str, Any] = {
        "challenge": challenge.model_dump(mode="json"),
        "gold": gold.model_dump(mode="json"),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _git_root(start: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return Path(result.stdout.strip()).resolve() if result.returncode == 0 else None


def _require_outside_repository(path: Path, repository: Path | None, label: str) -> None:
    if repository is None:
        return
    resolved = path.resolve()
    if resolved == repository or repository in resolved.parents:
        raise ValueError(f"{label} must stay outside the repository")


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_hash(value: Any) -> str:
    if isinstance(value, list):
        value = [item.model_dump(mode="json") for item in value]
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
