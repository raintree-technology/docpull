"""Versioned cross-repository contracts for DocPull acquisition workflows.

The models in this module are intentionally transport-neutral.  They describe
what DocPull acquired and emitted without prescribing a scheduler, reviewer, or
downstream product model.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models.document import DocumentRecord
from .models.run import RunIdentity
from .time_utils import utc_now_iso

WORKFLOW_REQUEST_CONTRACT: Final[Literal["workflow.request.v1"]] = "workflow.request.v1"
WORKFLOW_RESULT_CONTRACT: Final[Literal["workflow.result.v1"]] = "workflow.result.v1"
ARTIFACT_MANIFEST_CONTRACT: Final[Literal["artifact.manifest.v1"]] = "artifact.manifest.v1"
INTELLIGENCE_BUNDLE_CONTRACT: Final[Literal["intelligence.bundle.v1"]] = "intelligence.bundle.v1"
CHANGE_EVENT_CONTRACT: Final[Literal["change.event.v1"]] = "change.event.v1"


class ContractModel(BaseModel):
    """Forward-compatible base for public wire contracts."""

    model_config = ConfigDict(extra="allow")


class HashDigest(ContractModel):
    algorithm: Literal["sha256"] = "sha256"
    digest: str


class BudgetUsage(ContractModel):
    limit_usd: float | None = None
    estimated_usd: float = 0.0
    actual_usd: float | None = None
    paid_request_count: int = 0
    http_request_count: int = 0
    cache_hit_count: int = 0
    local_browser_seconds: float = 0.0
    blocked_actions: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowProgressEvent(ContractModel):
    event_id: str
    phase: str
    status: Literal["started", "progress", "completed", "warning", "failed"]
    timestamp: str
    message: str | None = None
    current: int | None = None
    total: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowWarning(ContractModel):
    code: str
    message: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkflowFailure(ContractModel):
    code: str = "workflow_error"
    message: str
    retryable: bool = False
    source_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReplayConfiguration(ContractModel):
    local_first: bool = True
    browser_enabled: bool = False
    paid_routes_enabled: bool = False
    scheduler: None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class WorkflowRequest(ContractModel):
    contract_version: Literal["workflow.request.v1"] = WORKFLOW_REQUEST_CONTRACT
    schema_version: int = 1
    request_id: str
    workflow: str
    input: dict[str, Any]
    output: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    source_policy: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    replay: ReplayConfiguration = Field(default_factory=ReplayConfiguration)


class ArtifactEntry(ContractModel):
    name: str
    path: str
    role: str
    media_type: str | None = None
    bytes: int
    sha256: str


class ArtifactManifest(ContractModel):
    contract_version: Literal["artifact.manifest.v1"] = ARTIFACT_MANIFEST_CONTRACT
    schema_version: int = 1
    pack_id: str
    run_id: str
    hash_algorithm: Literal["sha256"] = "sha256"
    entries: list[ArtifactEntry]
    aggregate_sha256: str


class WorkflowResult(ContractModel):
    contract_version: Literal["workflow.result.v1"] = WORKFLOW_RESULT_CONTRACT
    schema_version: int = 1
    request_id: str
    workflow: str
    status: Literal["completed", "completed_with_warnings", "failed", "cancelled"]
    started_at: str
    finished_at: str
    pack_identity: dict[str, Any]
    run_identity: dict[str, Any]
    summary: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    progress_events: list[WorkflowProgressEvent] = Field(default_factory=list)
    warnings: list[WorkflowWarning] = Field(default_factory=list)
    failures: list[WorkflowFailure] = Field(default_factory=list)
    budget_usage: BudgetUsage = Field(default_factory=BudgetUsage)
    hashes: dict[str, HashDigest] = Field(default_factory=dict)
    replay_configuration: ReplayConfiguration = Field(default_factory=ReplayConfiguration)
    artifact_manifest: str = "artifact.manifest.json"
    compatibility_artifacts: dict[str, str] = Field(default_factory=dict)


class EvidenceSpan(ContractModel):
    citation_id: str
    record_citation_id: str | None = None
    document_id: str
    document_version: str
    url: str
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    exact_text: str
    exact_text_sha256: str


class SourceAuthority(ContractModel):
    role: Literal[
        "official_product",
        "legal",
        "documentation",
        "social",
        "marketplace",
        "third_party",
    ]
    tier: Literal["tier_1_authoritative", "tier_2_owned", "tier_3_distribution", "tier_4_external"]
    rationale: str


class Observation(ContractModel):
    observation_id: str
    type: str
    text: str
    status: Literal["observation"] = "observation"
    evidence_strength: Literal["strong", "moderate", "weak", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)
    source_authority: SourceAuthority
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceSnapshot(ContractModel):
    source_snapshot_id: str
    source_id: str
    url: str
    document_id: str | None = None
    document_version: str | None = None
    content_hash: str | None = None
    fetched_at: str | None = None
    authority: SourceAuthority


class ChangeCandidate(ContractModel):
    change_candidate_id: str
    classification: Literal["pricing", "positioning", "product", "security", "policy", "other"]
    status: Literal["candidate"] = "candidate"
    before: list[EvidenceSpan] = Field(default_factory=list)
    after: list[EvidenceSpan] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)


class IntelligenceBundle(ContractModel):
    contract_version: Literal["intelligence.bundle.v1"] = INTELLIGENCE_BUNDLE_CONTRACT
    schema_version: int = 1
    bundle_id: str
    bundle_hash: str
    pack_identity: dict[str, Any]
    run_identity: dict[str, Any]
    workspace: dict[str, Any]
    source_snapshots: list[SourceSnapshot] = Field(default_factory=list)
    document_versions: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    change_candidates: list[ChangeCandidate] = Field(default_factory=list)
    warnings: list[WorkflowWarning] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)


class ChangeEvent(ContractModel):
    contract_version: Literal["change.event.v1"] = CHANGE_EVENT_CONTRACT
    schema_version: int = 1
    event_id: str
    idempotency_key: str
    workflow: str
    url: str
    old_document_id: str | None = None
    new_document_id: str | None = None
    old_hash: str | None = None
    new_hash: str | None = None
    old_evidence: list[EvidenceSpan] = Field(default_factory=list)
    new_evidence: list[EvidenceSpan] = Field(default_factory=list)
    structural_changes: list[dict[str, Any]] = Field(default_factory=list)
    textual_changes: list[dict[str, Any]] = Field(default_factory=list)
    semantic_candidates: list[dict[str, Any]] = Field(default_factory=list)
    classifications: list[Literal["pricing", "positioning", "product", "security", "policy", "other"]] = (
        Field(default_factory=list)
    )
    replay_configuration: ReplayConfiguration = Field(default_factory=ReplayConfiguration)


class PackContractV3(ContractModel):
    """Frozen compatibility envelope for existing ``*.pack.json`` files."""

    schema_version: int
    provider: str
    workflow: str
    status: str | None = None
    artifacts: dict[str, Any] = Field(default_factory=dict)


class CitationMapContractV1(ContractModel):
    schema_version: int
    source_count: int
    sources: list[dict[str, Any]]


class RightsContractV1(ContractModel):
    status: str
    allowed_use: dict[str, str]
    obligations: list[Any] = Field(default_factory=list)
    basis: str


class ProvenanceContractV1(ContractModel):
    schema_version: int
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class BasisContractV2(ContractModel):
    schema_version: Literal[2] = 2
    basis_id: str
    claim_path: str
    claim: str
    evidence_state: Literal["supported", "partial", "insufficient"]
    confidence: Literal["high", "medium", "low"]
    citation_ids: list[str] = Field(default_factory=list)
    source_urls: list[str] = Field(default_factory=list)
    excerpts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    producer: str


CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "workflow-request.v1.schema.json": WorkflowRequest,
    "workflow-result.v1.schema.json": WorkflowResult,
    "artifact-manifest.v1.schema.json": ArtifactManifest,
    "intelligence-bundle.v1.schema.json": IntelligenceBundle,
    "change-event.v1.schema.json": ChangeEvent,
    "document.v3.schema.json": DocumentRecord,
    "run-identity.v1.schema.json": RunIdentity,
    "pack.v3.schema.json": PackContractV3,
    "citation-map.v1.schema.json": CitationMapContractV1,
    "rights.v1.schema.json": RightsContractV1,
    "provenance.v1.schema.json": ProvenanceContractV1,
    "basis.v2.schema.json": BasisContractV2,
}


def canonical_json(value: Any) -> str:
    """Serialize a contract value for stable cross-runtime hashing."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_id(prefix: str, value: Any, *, length: int = 24) -> str:
    return f"{prefix}_{canonical_sha256(value)[:length]}"


def build_workflow_request(
    *,
    workflow: str,
    input_payload: dict[str, Any],
    output_dir: Path,
    options: dict[str, Any],
    source_policy: dict[str, Any],
    budget: dict[str, Any],
    browser_enabled: bool = False,
    paid_routes_enabled: bool = False,
) -> WorkflowRequest:
    identity_payload = {
        "workflow": workflow,
        "input": input_payload,
        "output": {"directory": str(output_dir.resolve())},
        "options": options,
        "source_policy": _without_ephemeral(source_policy),
        "budget": budget,
        "replay": {
            "local_first": True,
            "browser_enabled": browser_enabled,
            "paid_routes_enabled": paid_routes_enabled,
            "scheduler": None,
            "configuration": options,
        },
    }
    return WorkflowRequest(
        request_id=stable_id("request", identity_payload),
        workflow=workflow,
        input=input_payload,
        output={"directory": str(output_dir.resolve())},
        options=options,
        source_policy=source_policy,
        budget=budget,
        replay=ReplayConfiguration(
            browser_enabled=browser_enabled,
            paid_routes_enabled=paid_routes_enabled,
            configuration=options,
        ),
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_entries(
    output_dir: Path,
    artifacts: dict[str, str],
    *,
    excluded: set[str] | None = None,
) -> list[ArtifactEntry]:
    entries: list[ArtifactEntry] = []
    for name, relative in sorted(artifacts.items()):
        if excluded and name in excluded:
            continue
        path = Path(relative)
        candidate = path if path.is_absolute() else output_dir / path
        if not candidate.exists() or not candidate.is_file():
            continue
        entries.append(
            ArtifactEntry(
                name=name,
                path=str(path),
                role=_artifact_role(name),
                media_type=_media_type(candidate),
                bytes=candidate.stat().st_size,
                sha256=file_sha256(candidate),
            )
        )
    return entries


def write_contract_schemas(output_dir: Path) -> list[Path]:
    """Write the canonical JSON Schemas shipped with the package."""

    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for filename, model in sorted(CONTRACT_MODELS.items()):
        payload = model.model_json_schema(mode="serialization")
        payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        payload["$id"] = f"https://docpull.dev/schemas/{filename}"
        path = output_dir / filename
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        written.append(path)
    return written


def bundled_schema_path(name: str) -> Path:
    filename = name if name.endswith(".schema.json") else f"{name}.schema.json"
    if filename not in CONTRACT_MODELS:
        raise KeyError(f"Unknown DocPull contract schema: {name}")
    return Path(__file__).with_name("schemas") / filename


def new_progress_event(
    *,
    phase: str,
    status: Literal["started", "progress", "completed", "warning", "failed"],
    timestamp: str | None = None,
    message: str | None = None,
    current: int | None = None,
    total: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "phase": phase,
        "status": status,
        "timestamp": timestamp or utc_now_iso(),
        "message": message,
        "current": current,
        "total": total,
        "metadata": metadata or {},
    }
    payload["event_id"] = stable_id("event", payload)
    return WorkflowProgressEvent.model_validate(payload).model_dump(mode="json", exclude_none=True)


def _artifact_role(name: str) -> str:
    if "citation" in name or "source" in name:
        return "evidence"
    if "manifest" in name or "pack" in name:
        return "manifest"
    if "account" in name:
        return "budget_usage"
    if "markdown" in name or "context" in name:
        return "human_readable"
    return "workflow_output"


def _without_ephemeral(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _without_ephemeral(item)
            for key, item in value.items()
            if key not in {"generated_at", "requested_at", "started_at", "finished_at"}
        }
    if isinstance(value, list):
        return [_without_ephemeral(item) for item in value]
    return value


def _media_type(path: Path) -> str | None:
    return {
        ".json": "application/json",
        ".jsonl": "application/x-ndjson",
        ".ndjson": "application/x-ndjson",
        ".md": "text/markdown",
        ".css": "text/css",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
    }.get(path.suffix.lower())


__all__ = [
    "ARTIFACT_MANIFEST_CONTRACT",
    "CHANGE_EVENT_CONTRACT",
    "CONTRACT_MODELS",
    "INTELLIGENCE_BUNDLE_CONTRACT",
    "WORKFLOW_REQUEST_CONTRACT",
    "WORKFLOW_RESULT_CONTRACT",
    "ArtifactEntry",
    "ArtifactManifest",
    "BasisContractV2",
    "BudgetUsage",
    "ChangeCandidate",
    "ChangeEvent",
    "CitationMapContractV1",
    "EvidenceSpan",
    "HashDigest",
    "IntelligenceBundle",
    "Observation",
    "PackContractV3",
    "ProvenanceContractV1",
    "ReplayConfiguration",
    "RightsContractV1",
    "SourceAuthority",
    "SourceSnapshot",
    "WorkflowFailure",
    "WorkflowProgressEvent",
    "WorkflowRequest",
    "WorkflowResult",
    "WorkflowWarning",
    "artifact_entries",
    "build_workflow_request",
    "bundled_schema_path",
    "canonical_json",
    "canonical_sha256",
    "file_sha256",
    "new_progress_event",
    "stable_id",
    "write_contract_schemas",
]
