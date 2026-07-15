"""Pydantic-Evals orchestration and content-free portable report generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import subprocess
import sys
import time
import uuid
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic_evals import Case, Dataset

from .adapters import SystemAdapter
from .evaluators import EvaluationMetadata
from .models import (
    ArtifactRecord,
    ArtifactRecordSummary,
    BenchmarkInput,
    BenchmarkSuite,
    CaseScore,
    ChangePayload,
    CheckPayload,
    ContentPayload,
    PackPayload,
    PortableReport,
    ReportObservation,
    ResearchPayload,
    RetrievalPayload,
    RunManifest,
    RunObservation,
    RunSummary,
    SearchPayload,
    StructuredPayload,
)
from .sanitization import sanitize_url, scrub_secrets
from .scoring import SCORER_VERSION, score_observation


def run_suite(
    suite_path: Path,
    adapter: SystemAdapter,
    *,
    output_dir: Path,
    repeat: int = 1,
    max_concurrency: int = 1,
    case_ids: set[str] | None = None,
    progress: bool = True,
    command: list[str] | None = None,
    environment_label: str = "local",
    network_isolation: str = "best_effort",
    allow_stale_gold: bool = False,
) -> tuple[PortableReport, Path]:
    """Run one black-box system and persist portable, content-free artifacts."""
    suite = BenchmarkSuite.from_yaml(suite_path)
    _validate_freshness(suite, allow_stale=allow_stale_gold)
    cases = [case for case in suite.cases if case_ids is None or case.id in case_ids]
    if not cases:
        raise ValueError("suite selection contains no cases")
    unknown = (case_ids or set()) - {case.id for case in suite.cases}
    if unknown:
        raise ValueError(f"unknown case ids: {', '.join(sorted(unknown))}")
    adapter.preflight([case.input for case in cases], repeat=repeat)

    run_id = uuid.uuid4().hex
    run_dir = output_dir / run_id
    artifacts_dir = run_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=False)

    dataset = Dataset[BenchmarkInput, RunObservation, EvaluationMetadata](
        name=f"{suite.name}:{suite.version}",
        cases=[
            Case(name=case.id, inputs=case.input, metadata=EvaluationMetadata(case=case)) for case in cases
        ],
    )

    async def task(inputs: BenchmarkInput) -> RunObservation:
        started = time.perf_counter()
        try:
            return await asyncio.to_thread(adapter.run, inputs, artifacts_dir)
        except Exception as error:  # noqa: BLE001 - adapter failures are benchmark outcomes
            return RunObservation(
                case_id=inputs.case_id,
                system=adapter.system,
                status="failed",
                elapsed_seconds=time.perf_counter() - started,
                adapter_version=adapter.version,
                attempt_count=0,
                error=scrub_secrets(f"{type(error).__name__}: {error}"),
            )

    eval_report = asyncio.run(
        dataset.evaluate(
            task,
            name=f"{adapter.system}:{suite.name}:{suite.version}",
            max_concurrency=max_concurrency,
            progress=progress,
            repeat=repeat,
        )
    )
    observations = [result.output for result in eval_report.cases]
    case_by_id = {case.id: case for case in cases}
    trials_seen: dict[str, int] = defaultdict(int)
    report_observations: list[ReportObservation] = []
    scores: list[CaseScore] = []
    for observation in observations:
        case = case_by_id[observation.case_id]
        trials_seen[observation.case_id] += 1
        trial_index = trials_seen[observation.case_id]
        report_observations.append(_portable_observation(case, observation, trial_index))
        score = score_observation(case, observation)
        scores.append(_portable_score(score, trial_index))

    suite_bytes = suite_path.read_bytes()
    suite_hash = hashlib.sha256(suite_bytes).hexdigest()
    protocol_hash = _json_hash(
        {
            "schema_version": 2,
            "suite_sha256": suite_hash,
            "scorer_version": SCORER_VERSION,
            "repeat": repeat,
            "max_concurrency": max_concurrency,
            "case_ids": sorted(case.id for case in cases),
        }
    )
    git_revision, git_dirty = _git_state(suite_path.parent)
    manifest = RunManifest.now(
        run_id=run_id,
        suite_name=suite.name,
        suite_version=suite.version,
        suite_sha256=suite_hash,
        fixture_manifest_sha256=suite.fixture_manifest_sha256,
        protocol_sha256=protocol_hash,
        scorer_version=SCORER_VERSION,
        system=adapter.system,
        adapter_version=adapter.version,
        adapter_config_sha256=_adapter_config_hash(adapter),
        git_revision=git_revision,
        git_dirty=git_dirty,
        dependency_lock_sha256=_optional_file_hash(suite_path.parents[1] / "uv.lock"),
        python_version=platform.python_version(),
        operating_system=platform.system(),
        architecture=platform.machine(),
        environment_label=environment_label,
        network_isolation=network_isolation,
        cache_policy=str(getattr(adapter, "cache_policy", "not_applicable")),
        retry_policy=str(getattr(adapter, "retry_policy", "no_retries")),
        pricing_snapshot=getattr(adapter, "pricing_snapshot", None),
        repeat=repeat,
        max_concurrency=max_concurrency,
        command=_report_command(command or sys.argv),
    )
    report = PortableReport(
        manifest=manifest,
        observations=report_observations,
        scores=scores,
        summary=_summary(cases, observations, scores, repeat),
    )
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    _write_ndjson(
        run_dir / "observations.ndjson",
        [item.model_dump(mode="json") for item in report_observations],
    )
    _write_ndjson(run_dir / "scores.ndjson", [item.model_dump(mode="json") for item in scores])
    return report, run_dir


def _portable_observation(case: Any, observation: RunObservation, trial_index: int) -> ReportObservation:
    records: list[ArtifactRecordSummary] = []
    if isinstance(observation.payload, (ContentPayload, PackPayload)):
        records = [
            ArtifactRecordSummary.from_record(record).model_copy(update={"url": sanitize_url(record.url)})
            for record in observation.payload.records
        ]
    return ReportObservation(
        case_id=observation.case_id,
        trial_index=trial_index,
        lane=case.input.lane,
        split=case.metadata.split,
        family=case.metadata.family,
        critical=case.metadata.critical,
        system=observation.system,
        status=observation.status,
        payload_summary=_payload_summary(observation.payload),
        records=records,
        elapsed_seconds=observation.elapsed_seconds,
        peak_rss_bytes=observation.peak_rss_bytes,
        cost_usd=observation.cost_usd,
        cost_kind=observation.cost_kind,
        cost_basis=observation.cost_basis,
        usage=observation.usage,
        request_count=observation.request_count,
        attempt_count=observation.attempt_count,
        adapter_version=observation.adapter_version,
        error=scrub_secrets(observation.error) if observation.error else None,
        artifacts=_safe_artifacts(observation.artifacts),
    )


def _portable_score(score: CaseScore, trial_index: int) -> CaseScore:
    assertions = []
    for assertion in score.assertions:
        name, separator, sensitive = assertion.name.partition(":")
        if separator:
            name = f"{name}:sha256:{hashlib.sha256(sensitive.encode()).hexdigest()}"
        assertions.append(
            assertion.model_copy(
                update={
                    "name": name,
                    "actual": _portable_metric(assertion.actual),
                    "expected": _portable_metric(assertion.expected),
                    "detail": scrub_secrets(assertion.detail) if assertion.detail else None,
                }
            )
        )
    return score.model_copy(update={"trial_index": trial_index, "assertions": assertions})


def _portable_metric(value: Any) -> Any:
    if isinstance(value, str):
        safe = {"completed", "failed", "unsupported", "budget_blocked", "unknown"}
        return value if value in safe else f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
    return value


def _safe_artifacts(artifacts: dict[str, str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for key, value in artifacts.items():
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            continue
        output[scrub_secrets(key)] = scrub_secrets(path.as_posix())
    return output


def _record_summary(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "identity": hashlib.sha256(f"{record.url}\0{record.title}".encode()).hexdigest(),
        "url": sanitize_url(record.url),
        "content_chars": len(record.content),
        "content_sha256": hashlib.sha256(record.content.encode()).hexdigest(),
    }


def _payload_summary(payload: Any) -> dict[str, Any]:
    if payload is None:
        return {}
    if isinstance(payload, ContentPayload):
        return {
            "kind": payload.kind,
            "record_count": len(payload.records),
            "selected_urls": [sanitize_url(url) for url in payload.selected_urls],
        }
    if isinstance(payload, PackPayload):
        return {
            "kind": payload.kind,
            "record_count": len(payload.records),
            "files": payload.files,
            "contract_level": payload.contract_level,
            "stable_identity_count": len(payload.stable_identities),
            "stable_identity_sha256": _json_hash(sorted(payload.stable_identities)),
        }
    if isinstance(payload, StructuredPayload):
        serialized = json.dumps(payload.value, sort_keys=True, default=str)
        return {
            "kind": payload.kind,
            "schema_valid": payload.schema_valid,
            "value_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
            "evidence_ids": payload.evidence_ids,
        }
    if isinstance(payload, CheckPayload):
        return {
            "kind": payload.kind,
            "detail_keys": sorted(payload.details),
            "details_sha256": _json_hash(payload.details),
        }
    if isinstance(payload, ChangePayload):
        return {
            "kind": payload.kind,
            "events": [event.model_dump(mode="json") for event in payload.events],
            "delay_seconds": payload.delay_seconds,
        }
    if isinstance(payload, (RetrievalPayload, SearchPayload)):
        return {
            "kind": payload.kind,
            "results": [
                {
                    "identity": result.identity,
                    "url": sanitize_url(result.url) if result.url else None,
                    "excerpt_chars": len(result.excerpt),
                    "excerpt_sha256": hashlib.sha256(result.excerpt.encode()).hexdigest(),
                    "score": result.score,
                }
                for result in payload.results
            ],
            **({"index_bytes": payload.index_bytes} if isinstance(payload, RetrievalPayload) else {}),
        }
    if isinstance(payload, ResearchPayload):
        return {
            "kind": payload.kind,
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "value_sha256": _json_hash(claim.value),
                    "evidence_ids": claim.evidence_ids,
                    "excerpt_hashes": [hashlib.sha256(item.encode()).hexdigest() for item in claim.excerpts],
                }
                for claim in payload.claims
            ],
        }
    raise AssertionError(f"unhandled payload: {type(payload).__name__}")


def _summary(
    cases: list[Any], observations: list[RunObservation], scores: list[CaseScore], repeat: int
) -> RunSummary:
    passed_by_case: dict[str, list[bool]] = defaultdict(list)
    for score in scores:
        passed_by_case[score.case_id].append(score.passed)
    observed_costs = [item.cost_usd for item in observations if item.cost_usd is not None]
    stability = mean(float(len(set(passed_by_case[case.id])) == 1) for case in cases)
    return RunSummary(
        case_count=len(cases),
        case_runs=len(scores),
        repeat=repeat,
        completed=sum(score.completed for score in scores),
        unsupported=sum(score.status == "unsupported" for score in scores),
        completion_rate=mean(float(score.completed) for score in scores),
        trial_pass_rate=mean(float(score.passed) for score in scores),
        pass_all_trials_rate=mean(float(all(passed_by_case[case.id])) for case in cases),
        pass_any_trial_rate=mean(float(any(passed_by_case[case.id])) for case in cases),
        trial_stability_rate=stability,
        mean_required_check_rate=mean(score.required_check_rate for score in scores),
        mean_elapsed_seconds=mean(score.elapsed_seconds for score in scores),
        observed_cost_usd=sum(observed_costs),
        cost_observed_runs=len(observed_costs),
        cost_actual_runs=sum(item.cost_kind == "actual" for item in observations),
        cost_estimated_runs=sum(item.cost_kind == "estimated" for item in observations),
        cost_upper_bound_runs=sum(item.cost_kind == "upper_bound" for item in observations),
        cost_unknown_runs=sum(item.cost_kind == "unknown" for item in observations),
    )


def _validate_freshness(suite: BenchmarkSuite, *, allow_stale: bool) -> None:
    if allow_stale:
        return
    today = date.today()
    expired = [
        case.id
        for case in suite.cases
        if case.metadata.live
        and case.metadata.reference_expires_at
        and date.fromisoformat(case.metadata.reference_expires_at) < today
    ]
    if expired:
        raise ValueError(f"live gold is stale for: {', '.join(expired)}; recheck or pass --allow-stale-gold")


def _git_state(start: Path) -> tuple[str | None, bool]:
    try:
        root = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=start,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=start,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, False
    if root.returncode or revision.returncode:
        return None, False
    return revision.stdout.strip() or None, bool(status.stdout.strip())


def _adapter_config_hash(adapter: SystemAdapter) -> str:
    public = getattr(adapter, "public_config", None)
    if callable(public):
        payload = public()
    else:
        payload = {
            "system": adapter.system,
            "version": adapter.version,
            "capabilities": sorted(str(item) for item in getattr(adapter, "capabilities", [])),
        }
    return _json_hash(payload)


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _optional_file_hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _write_ndjson(path: Path, payloads: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n" for payload in payloads),
        encoding="utf-8",
    )


def _report_command(command: list[str]) -> list[str]:
    if not command:
        return []
    normalized = [Path(command[0]).name]
    redact_next = False
    for argument in command[1:]:
        if redact_next:
            normalized.append("[REDACTED_ADAPTER_COMMAND]")
            redact_next = False
        else:
            normalized.append(argument)
            redact_next = argument in {"--command", "--api-key"}
    return normalized
