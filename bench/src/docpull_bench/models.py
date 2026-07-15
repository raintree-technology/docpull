"""Versioned, framework-neutral contracts for the DocPull evaluation lab."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from statistics import mean
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .serialization import strict_yaml_load

SCHEMA_VERSION = 3
MetricValue: TypeAlias = bool | int | float | str | None
ComparisonScope: TypeAlias = Literal["core", "boundary"]
BoundaryReason: TypeAlias = Literal["managed_access", "robots_policy", "browser_required", "auth_required"]


class StrictModel(BaseModel):
    """Reject unknown fields so benchmark protocol changes are deliberate."""

    model_config = ConfigDict(extra="forbid")


class Lane(str, Enum):
    EXTRACT = "extract"
    CRAWL = "crawl"
    PARSE = "parse"
    PACK = "pack"
    STRUCTURED = "structured"
    LIFECYCLE = "lifecycle"
    CHANGE = "change"
    RETRIEVAL = "retrieval"
    SEARCH = "search"
    RESEARCH = "research"
    POLICY = "policy"


class InputBase(StrictModel):
    case_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    timeout_seconds: float = Field(default=60.0, gt=0, le=1800)


class ExtractInput(InputBase):
    lane: Literal[Lane.EXTRACT]
    url: str
    include_domains: list[str] = Field(default_factory=list)
    cache_policy: Literal["disabled", "cold", "warm", "provider_managed"] = "disabled"


class CrawlInput(InputBase):
    lane: Literal[Lane.CRAWL]
    url: str
    include_domains: list[str] = Field(default_factory=list)
    include_path_prefixes: list[str] = Field(default_factory=list)
    exclude_path_prefixes: list[str] = Field(default_factory=list)
    max_pages: int = Field(default=10, ge=1, le=1000)
    max_depth: int = Field(default=1, ge=0, le=20)
    cache_policy: Literal["disabled", "cold", "warm", "provider_managed"] = "disabled"


class ParseInput(InputBase):
    lane: Literal[Lane.PARSE]
    path: str
    backend: Literal["auto", "builtin", "pypdf", "markitdown", "unstructured"] = "auto"


class PackInput(InputBase):
    lane: Literal[Lane.PACK]
    path: str
    contract_level: Literal["raw", "agent", "eval"] = "raw"
    action: Literal["validate", "prepare", "export"] = "validate"
    export_format: str | None = None


class StructuredInput(InputBase):
    lane: Literal[Lane.STRUCTURED]
    source_path: str
    schema_path: str


class LifecycleInput(InputBase):
    lane: Literal[Lane.LIFECYCLE]
    check: Literal[
        "raw_contract",
        "eval_prepare",
        "stable_identity",
        "exact_diff",
        "offline_search",
        "exports",
        "context_ci",
        "lock_drift",
        "credential_non_persistence",
        "zero_budget",
    ]


class ChangeInput(InputBase):
    lane: Literal[Lane.CHANGE]
    before_path: str
    after_path: str
    mode: Literal["pack_diff", "refresh", "monitor"] = "pack_diff"


class RetrievalInput(InputBase):
    lane: Literal[Lane.RETRIEVAL]
    pack_path: str
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=100)


class SearchInput(InputBase):
    lane: Literal[Lane.SEARCH]
    query: str = Field(min_length=1)
    max_results: int = Field(default=10, ge=1, le=100)
    include_domains: list[str] = Field(default_factory=list)


class ResearchInput(InputBase):
    lane: Literal[Lane.RESEARCH]
    corpus_path: str
    question: str = Field(min_length=1)
    max_claims: int = Field(default=10, ge=1, le=100)


class PolicyInput(InputBase):
    lane: Literal[Lane.POLICY]
    scenario: Literal[
        "private_target",
        "robots",
        "zero_budget",
        "credential_leak",
        "rights",
        "redirect",
        "artifact_escape",
        "malformed_config",
    ]
    target_url: str | None = None
    fixture_path: str | None = None


BenchmarkInput: TypeAlias = Annotated[
    ExtractInput
    | CrawlInput
    | ParseInput
    | PackInput
    | StructuredInput
    | LifecycleInput
    | ChangeInput
    | RetrievalInput
    | SearchInput
    | ResearchInput
    | PolicyInput,
    Field(discriminator="lane"),
]


class ExpectedBase(StrictModel):
    minimum_records: int = Field(default=0, ge=0)
    minimum_content_chars: int = Field(default=0, ge=0)
    maximum_content_chars: int | None = Field(default=None, ge=0)
    required_terms: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)
    required_ordered_terms: list[str] = Field(default_factory=list)
    maximum_long_token_rate: float | None = Field(default=None, ge=0, le=1)
    minimum_markdown_links: int = Field(default=0, ge=0)
    minimum_fenced_code_blocks: int = Field(default=0, ge=0)
    minimum_markdown_table_rows: int = Field(default=0, ge=0)


class ExtractExpected(ExpectedBase):
    lane: Literal[Lane.EXTRACT]
    required_urls: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    required_headings: list[str] = Field(default_factory=list)


class CrawlExpected(ExpectedBase):
    lane: Literal[Lane.CRAWL]
    required_urls: list[str] = Field(default_factory=list)
    allowed_domains: list[str] = Field(default_factory=list)
    required_headings: list[str] = Field(default_factory=list)
    maximum_duplicate_rate: float = Field(default=1.0, ge=0, le=1)


class ParseExpected(ExpectedBase):
    lane: Literal[Lane.PARSE]
    required_metadata: dict[str, str] = Field(default_factory=dict)
    expected_status: Literal["completed", "failed", "unsupported"] = "completed"


class PackExpected(ExpectedBase):
    lane: Literal[Lane.PACK]
    required_files: list[str] = Field(default_factory=list)
    required_contract_level: Literal["raw", "agent", "eval"] | None = None
    minimum_stable_identities: int = Field(default=0, ge=0)


class StructuredExpected(ExpectedBase):
    lane: Literal[Lane.STRUCTURED]
    expected_value: Any = None
    required_evidence_ids: list[str] = Field(default_factory=list)
    expected_status: Literal["completed", "failed", "unsupported"] = "completed"


class LifecycleExpected(ExpectedBase):
    lane: Literal[Lane.LIFECYCLE]
    required_details: dict[str, MetricValue] = Field(default_factory=dict)


class ExpectedChangeEvent(StrictModel):
    identity: str
    kind: Literal["added", "removed", "changed", "unchanged", "cosmetic"]
    category: str | None = None


class ChangeExpected(ExpectedBase):
    lane: Literal[Lane.CHANGE]
    events: list[ExpectedChangeEvent] = Field(default_factory=list)
    maximum_false_positives: int = Field(default=0, ge=0)


class RetrievalExpected(ExpectedBase):
    lane: Literal[Lane.RETRIEVAL]
    relevant_ids: list[str] = Field(default_factory=list)
    forbidden_ids: list[str] = Field(default_factory=list)
    expected_empty: bool = False


class SearchExpected(ExpectedBase):
    lane: Literal[Lane.SEARCH]
    relevant_urls: list[str] = Field(default_factory=list)
    relevant_domains: list[str] = Field(default_factory=list)
    required_identifiers: list[str] = Field(default_factory=list)


class ExpectedClaim(StrictModel):
    claim_id: str
    value: Any
    evidence_ids: list[str] = Field(default_factory=list)
    required_excerpt_terms: list[str] = Field(default_factory=list)


class ResearchExpected(ExpectedBase):
    lane: Literal[Lane.RESEARCH]
    claims: list[ExpectedClaim] = Field(default_factory=list)


class PolicyExpected(ExpectedBase):
    lane: Literal[Lane.POLICY]
    expected_status: Literal["completed", "failed", "unsupported", "budget_blocked"]
    required_error_terms: list[str] = Field(default_factory=list)
    maximum_request_count: int | None = Field(default=None, ge=0)
    forbidden_output_terms: list[str] = Field(default_factory=list)


ExpectedOutput: TypeAlias = Annotated[
    ExtractExpected
    | CrawlExpected
    | ParseExpected
    | PackExpected
    | StructuredExpected
    | LifecycleExpected
    | ChangeExpected
    | RetrievalExpected
    | SearchExpected
    | ResearchExpected
    | PolicyExpected,
    Field(discriminator="lane"),
]


class RightsMetadata(StrictModel):
    redistribution: Literal["allowed", "allowed_with_conditions", "unknown", "prohibited"]
    source: str
    notes: str | None = None


class CaseMetadata(StrictModel):
    description: str
    split: Literal["dev", "test"] = "dev"
    family: str = Field(default="general", min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    product_area: str = Field(default="benchmark", min_length=1)
    critical: bool = True
    live: bool = False
    tags: list[str] = Field(default_factory=list)
    reference_checked_at: str | None = None
    reference_expires_at: str | None = None
    comparison_scope: ComparisonScope = "core"
    boundary_reason: BoundaryReason | None = None
    rights: RightsMetadata

    @model_validator(mode="after")
    def validate_scope(self) -> CaseMetadata:
        if self.comparison_scope == "boundary" and self.boundary_reason is None:
            raise ValueError("boundary cases require boundary_reason")
        if self.comparison_scope == "core" and self.boundary_reason is not None:
            raise ValueError("core cases cannot declare boundary_reason")
        return self


class BenchmarkCase(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    input: BenchmarkInput
    expected: ExpectedOutput
    metadata: CaseMetadata

    @model_validator(mode="after")
    def validate_case(self) -> BenchmarkCase:
        if self.input.case_id != self.id:
            raise ValueError("input.case_id must match id")
        if self.input.lane != self.expected.lane:
            raise ValueError("input and expected lanes must match")
        if self.metadata.live and not (
            self.metadata.reference_checked_at and self.metadata.reference_expires_at
        ):
            raise ValueError("live cases require reference_checked_at and reference_expires_at")
        return self


class BenchmarkSuite(StrictModel):
    schema_version: Literal[2] = 2
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str
    fixture_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_case_ids(self) -> BenchmarkSuite:
        ids = [case.id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case ids must be unique")
        return self

    @classmethod
    def from_yaml(cls, path: Path) -> BenchmarkSuite:
        payload = strict_yaml_load(path.read_text(encoding="utf-8"))
        return cls.model_validate(payload)


class ArtifactRecord(StrictModel):
    url: str
    title: str = ""
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContentPayload(StrictModel):
    kind: Literal["content"] = "content"
    records: list[ArtifactRecord] = Field(default_factory=list)
    selected_urls: list[str] = Field(default_factory=list)


class PackPayload(StrictModel):
    kind: Literal["pack"] = "pack"
    records: list[ArtifactRecord] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    contract_level: Literal["raw", "agent", "eval"] | None = None
    stable_identities: list[str] = Field(default_factory=list)


class StructuredPayload(StrictModel):
    kind: Literal["structured"] = "structured"
    value: Any = None
    schema_valid: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class CheckPayload(StrictModel):
    kind: Literal["checks"] = "checks"
    details: dict[str, MetricValue] = Field(default_factory=dict)


class ChangeEvent(StrictModel):
    identity: str
    kind: Literal["added", "removed", "changed", "unchanged", "cosmetic"]
    category: str | None = None


class ChangePayload(StrictModel):
    kind: Literal["changes"] = "changes"
    events: list[ChangeEvent] = Field(default_factory=list)
    delay_seconds: float | None = Field(default=None, ge=0)


class RankedResult(StrictModel):
    identity: str
    url: str | None = None
    title: str = ""
    excerpt: str = ""
    score: float | None = None


class RetrievalPayload(StrictModel):
    kind: Literal["retrieval"] = "retrieval"
    results: list[RankedResult] = Field(default_factory=list)
    index_bytes: int | None = Field(default=None, ge=0)


class SearchPayload(StrictModel):
    kind: Literal["search"] = "search"
    results: list[RankedResult] = Field(default_factory=list)


class ResearchClaim(StrictModel):
    claim_id: str
    value: Any
    evidence_ids: list[str] = Field(default_factory=list)
    excerpts: list[str] = Field(default_factory=list)


class ResearchPayload(StrictModel):
    kind: Literal["research"] = "research"
    claims: list[ResearchClaim] = Field(default_factory=list)


ObservationPayload: TypeAlias = Annotated[
    ContentPayload
    | PackPayload
    | StructuredPayload
    | CheckPayload
    | ChangePayload
    | RetrievalPayload
    | SearchPayload
    | ResearchPayload,
    Field(discriminator="kind"),
]


class RunObservation(StrictModel):
    schema_version: Literal[2] = 2
    case_id: str
    system: str
    status: Literal["completed", "failed", "unsupported", "budget_blocked"]
    payload: ObservationPayload | None = None
    elapsed_seconds: float = Field(ge=0)
    peak_rss_bytes: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_kind: Literal["actual", "estimated", "upper_bound", "unknown"] = "unknown"
    cost_basis: str | None = None
    usage: dict[str, MetricValue] = Field(default_factory=dict)
    request_count: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(default=1, ge=0)
    adapter_version: str
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_observation(self) -> RunObservation:
        if self.status == "failed" and not self.error:
            raise ValueError("failed observations require an error")
        if self.cost_kind != "unknown" and self.cost_usd is None:
            raise ValueError("accounted costs require cost_usd")
        if self.status == "completed" and self.payload is None:
            raise ValueError("completed observations require a payload")
        return self


class ArtifactRecordSummary(StrictModel):
    url: str
    title_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_chars: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_record(cls, record: ArtifactRecord) -> ArtifactRecordSummary:
        return cls(
            url=record.url,
            title_sha256=hashlib.sha256(record.title.encode()).hexdigest(),
            content_chars=len(record.content),
            content_sha256=hashlib.sha256(record.content.encode()).hexdigest(),
        )


class ReportObservation(StrictModel):
    schema_version: Literal[2, 3] = 3
    case_id: str
    trial_index: int = Field(default=1, ge=1)
    lane: Lane
    split: Literal["dev", "test"]
    family: str
    critical: bool
    comparison_scope: ComparisonScope | None = None
    boundary_reason: BoundaryReason | None = None
    system: str
    status: Literal["completed", "failed", "unsupported", "budget_blocked"]
    payload_summary: dict[str, Any] = Field(default_factory=dict)
    records: list[ArtifactRecordSummary] = Field(default_factory=list)
    elapsed_seconds: float = Field(ge=0)
    peak_rss_bytes: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_kind: Literal["actual", "estimated", "upper_bound", "unknown"] = "unknown"
    cost_basis: str | None = None
    usage: dict[str, MetricValue] = Field(default_factory=dict)
    request_count: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(default=1, ge=0)
    adapter_version: str
    error: str | None = None
    artifacts: dict[str, str] = Field(default_factory=dict)
    normalized_output_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    evidence_ciphertext_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_scope(self) -> ReportObservation:
        if self.comparison_scope == "boundary" and self.boundary_reason is None:
            raise ValueError("boundary observations require boundary_reason")
        if self.comparison_scope in {"core", None} and self.boundary_reason is not None:
            raise ValueError("non-boundary observations cannot declare boundary_reason")
        return self


class AssertionResult(StrictModel):
    name: str
    passed: bool
    actual: MetricValue = None
    expected: MetricValue = None
    detail: str | None = None


class ScoreBase(StrictModel):
    case_id: str
    system: str
    trial_index: int = Field(default=1, ge=1)
    split: Literal["dev", "test"]
    family: str
    critical: bool
    completed: bool
    passed: bool
    required_check_rate: float = Field(ge=0, le=1)
    assertions: list[AssertionResult]
    metrics: dict[str, MetricValue] = Field(default_factory=dict)
    elapsed_seconds: float = Field(ge=0)
    peak_rss_bytes: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_kind: Literal["actual", "estimated", "upper_bound", "unknown"] = "unknown"
    status: Literal["completed", "failed", "unsupported", "budget_blocked"]

    @model_validator(mode="after")
    def validate_derived_score(self) -> ScoreBase:
        if not self.assertions:
            raise ValueError("scores require at least one assertion")
        expected_completed = self.status == "completed"
        expected_passed = all(assertion.passed for assertion in self.assertions)
        expected_rate = sum(assertion.passed for assertion in self.assertions) / len(self.assertions)
        if self.completed != expected_completed:
            raise ValueError("completed must be derived from score status")
        if self.passed != expected_passed:
            raise ValueError("passed must equal the conjunction of score assertions")
        if abs(self.required_check_rate - expected_rate) > 1e-12:
            raise ValueError("required_check_rate must be derived from score assertions")
        return self


class ExtractScore(ScoreBase):
    lane: Literal[Lane.EXTRACT]


class CrawlScore(ScoreBase):
    lane: Literal[Lane.CRAWL]


class ParseScore(ScoreBase):
    lane: Literal[Lane.PARSE]


class PackScore(ScoreBase):
    lane: Literal[Lane.PACK]


class StructuredScore(ScoreBase):
    lane: Literal[Lane.STRUCTURED]


class LifecycleScore(ScoreBase):
    lane: Literal[Lane.LIFECYCLE]


class ChangeScore(ScoreBase):
    lane: Literal[Lane.CHANGE]


class RetrievalScore(ScoreBase):
    lane: Literal[Lane.RETRIEVAL]


class SearchScore(ScoreBase):
    lane: Literal[Lane.SEARCH]


class ResearchScore(ScoreBase):
    lane: Literal[Lane.RESEARCH]


class PolicyScore(ScoreBase):
    lane: Literal[Lane.POLICY]


CaseScore: TypeAlias = Annotated[
    ExtractScore
    | CrawlScore
    | ParseScore
    | PackScore
    | StructuredScore
    | LifecycleScore
    | ChangeScore
    | RetrievalScore
    | SearchScore
    | ResearchScore
    | PolicyScore,
    Field(discriminator="lane"),
]


class SubjectIdentity(StrictModel):
    kind: Literal["wheel", "source-tree", "remote-service", "command", "replay"]
    artifact_basename: str | None = None
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    package_version: str | None = None
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,64}$")
    clean_build: bool | None = None
    public_request_profile: dict[str, Any] | None = None
    public_request_profile_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_identity(self) -> SubjectIdentity:
        if self.kind == "wheel" and not all(
            (
                self.artifact_basename,
                self.artifact_sha256,
                self.package_version,
                self.source_revision,
                self.clean_build is not None,
            )
        ):
            raise ValueError("wheel subjects require artifact, version, revision, and build cleanliness")
        if self.kind == "remote-service" and not (
            self.public_request_profile and self.public_request_profile_sha256
        ):
            raise ValueError("remote-service subjects require an exact public request profile and hash")
        if self.kind == "remote-service":
            expected = hashlib.sha256(
                json.dumps(
                    self.public_request_profile,
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            if self.public_request_profile_sha256 != expected:
                raise ValueError("remote-service request profile hash does not match its snapshot")
        if self.artifact_basename and (
            Path(self.artifact_basename).name != self.artifact_basename or "\\" in self.artifact_basename
        ):
            raise ValueError("subject artifact_basename cannot contain a path")
        if self.kind == "wheel" and not str(self.artifact_basename).endswith(".whl"):
            raise ValueError("wheel subject artifact_basename must name a wheel")
        return self


class RunManifest(StrictModel):
    schema_version: Literal[2, 3] = 3
    run_id: str
    created_at: str
    suite_name: str
    suite_version: str
    suite_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fixture_manifest_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    protocol_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scorer_version: str = "v2-unversioned"
    system: str
    adapter_version: str
    adapter_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_revision: str | None = None
    git_dirty: bool
    dependency_lock_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    python_version: str
    operating_system: str
    architecture: str
    environment_label: str
    network_isolation: Literal["enforced", "best_effort", "open"]
    cache_policy: str
    retry_policy: str
    pricing_snapshot: str | None = None
    repeat: int = Field(ge=1)
    max_concurrency: int = Field(ge=1)
    command: list[str]
    subject: SubjectIdentity | None = None

    @model_validator(mode="after")
    def validate_created_at(self) -> RunManifest:
        try:
            created = datetime.fromisoformat(self.created_at)
        except ValueError as error:
            raise ValueError("manifest created_at must be an ISO-8601 datetime") from error
        if created.tzinfo is None:
            raise ValueError("manifest created_at must include a timezone")
        return self

    @classmethod
    def now(cls, **kwargs: Any) -> RunManifest:
        return cls(created_at=datetime.now(timezone.utc).isoformat(), **kwargs)


class RunSummary(StrictModel):
    case_count: int = Field(ge=0)
    case_runs: int = Field(ge=0)
    repeat: int = Field(ge=1)
    completed: int = Field(ge=0)
    unsupported: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    trial_pass_rate: float = Field(ge=0, le=1)
    pass_all_trials_rate: float = Field(ge=0, le=1)
    pass_any_trial_rate: float = Field(ge=0, le=1)
    trial_stability_rate: float = Field(ge=0, le=1)
    mean_required_check_rate: float = Field(ge=0, le=1)
    mean_elapsed_seconds: float = Field(ge=0)
    observed_cost_usd: float = Field(ge=0)
    cost_observed_runs: int = Field(ge=0)
    cost_actual_runs: int = Field(ge=0)
    cost_estimated_runs: int = Field(ge=0)
    cost_upper_bound_runs: int = Field(ge=0)
    cost_unknown_runs: int = Field(ge=0)


def canonical_run_summary(
    observations: list[ReportObservation],
    scores: list[CaseScore],
    repeat: int,
) -> RunSummary:
    """Recompute every report aggregate from immutable per-trial facts."""
    if not scores:
        raise ValueError("reports require at least one score")
    passed_by_case: dict[str, list[bool]] = defaultdict(list)
    for score in scores:
        passed_by_case[score.case_id].append(score.passed)
    observed_costs = [item.cost_usd for item in observations if item.cost_usd is not None]
    case_ids = sorted(passed_by_case)
    return RunSummary(
        case_count=len(case_ids),
        case_runs=len(scores),
        repeat=repeat,
        completed=sum(score.completed for score in scores),
        unsupported=sum(score.status == "unsupported" for score in scores),
        completion_rate=mean(float(score.completed) for score in scores),
        trial_pass_rate=mean(float(score.passed) for score in scores),
        pass_all_trials_rate=mean(float(all(passed_by_case[case_id])) for case_id in case_ids),
        pass_any_trial_rate=mean(float(any(passed_by_case[case_id])) for case_id in case_ids),
        trial_stability_rate=mean(float(len(set(passed_by_case[case_id])) == 1) for case_id in case_ids),
        mean_required_check_rate=mean(score.required_check_rate for score in scores),
        mean_elapsed_seconds=mean(score.elapsed_seconds for score in scores),
        observed_cost_usd=sum(observed_costs),
        cost_observed_runs=len(observed_costs),
        cost_actual_runs=sum(item.cost_kind == "actual" for item in observations),
        cost_estimated_runs=sum(item.cost_kind == "estimated" for item in observations),
        cost_upper_bound_runs=sum(item.cost_kind == "upper_bound" for item in observations),
        cost_unknown_runs=sum(item.cost_kind == "unknown" for item in observations),
    )


class PortableReport(StrictModel):
    schema_version: Literal[2, 3] = 3
    evidence_status: Literal["legacy-v2", "integrity-checked-v3"] = "legacy-v2"
    manifest: RunManifest
    observations: list[ReportObservation]
    scores: list[CaseScore]
    summary: RunSummary

    @model_validator(mode="after")
    def validate_report_integrity(self) -> PortableReport:
        if self.schema_version == 2:
            if self.manifest.schema_version != 2 or self.evidence_status != "legacy-v2":
                raise ValueError("v2 reports must be labeled legacy-v2")
            return self
        if self.manifest.schema_version != 3 or self.evidence_status != "integrity-checked-v3":
            raise ValueError("v3 reports require matching manifest and integrity label")
        if self.manifest.subject is None:
            raise ValueError("v3 reports require subject provenance")
        observation_keys = [(item.case_id, item.trial_index) for item in self.observations]
        score_keys = [(item.case_id, item.trial_index) for item in self.scores]
        if len(observation_keys) != len(set(observation_keys)):
            raise ValueError("v3 report contains duplicate observation trials")
        if len(score_keys) != len(set(score_keys)):
            raise ValueError("v3 report contains duplicate score trials")
        if set(observation_keys) != set(score_keys):
            raise ValueError("v3 report observation and score trial keys must match exactly")
        expected_indices = set(range(1, self.manifest.repeat + 1))
        observed_indices: dict[str, set[int]] = defaultdict(set)
        for case_id, trial_index in observation_keys:
            observed_indices[case_id].add(trial_index)
        if any(indices != expected_indices for indices in observed_indices.values()):
            raise ValueError("v3 report trial indices must exactly match manifest.repeat")
        observations = {(item.case_id, item.trial_index): item for item in self.observations}
        for score in self.scores:
            observation = observations[(score.case_id, score.trial_index)]
            if observation.system != self.manifest.system or score.system != self.manifest.system:
                raise ValueError("v3 report contains conflicting system identity")
            if observation.adapter_version != self.manifest.adapter_version:
                raise ValueError("v3 report contains conflicting adapter version")
            if (
                observation.lane != score.lane
                or observation.split != score.split
                or observation.family != score.family
                or observation.critical != score.critical
                or observation.status != score.status
                or observation.elapsed_seconds != score.elapsed_seconds
                or observation.peak_rss_bytes != score.peak_rss_bytes
                or observation.cost_usd != score.cost_usd
                or observation.cost_kind != score.cost_kind
            ):
                raise ValueError("v3 observation and score trial facts conflict")
            if observation.comparison_scope is None:
                raise ValueError("v3 observations require predeclared comparison scope")
            if observation.normalized_output_sha256 is None:
                raise ValueError("v3 observations require normalized output commitments")
        canonical = canonical_run_summary(self.observations, self.scores, self.manifest.repeat)
        if self.summary != canonical:
            raise ValueError("v3 report summary does not match canonical trial facts")
        return self


class ComparisonRow(StrictModel):
    lane: Lane
    slice_type: Literal["overall", "scope", "split", "family"]
    slice_value: str
    system: str
    adapter_version: str
    case_count: int = Field(ge=0)
    trial_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    trial_pass_rate: float = Field(ge=0, le=1)
    pass_all_trials_rate: float = Field(ge=0, le=1)
    pass_all_ci95_low: float = Field(ge=0, le=1)
    pass_all_ci95_high: float = Field(ge=0, le=1)
    pass_any_trial_rate: float = Field(ge=0, le=1)
    mean_required_check_rate: float = Field(ge=0, le=1)
    macro_family_pass_all_rate: float = Field(ge=0, le=1)
    trial_stability_rate: float = Field(ge=0, le=1)
    median_elapsed_seconds: float = Field(ge=0)
    p95_elapsed_seconds: float = Field(ge=0)
    median_peak_rss_bytes: int | None = Field(default=None, ge=0)
    accounted_cost_usd: float = Field(ge=0)
    cost_per_passing_case_usd: float | None = Field(default=None, ge=0)
    latency_comparable: bool
    completion_ci95_low: float = Field(default=0, ge=0, le=1)
    completion_ci95_high: float = Field(default=0, ge=0, le=1)
    quality_eligible_trials: int = Field(default=0, ge=0)
    quality_pass_rate_completed: float = Field(default=0, ge=0, le=1)


class ComparisonCaseRow(StrictModel):
    case_id: str
    lane: Lane
    split: Literal["dev", "test"]
    family: str
    critical: bool
    comparison_scope: Literal["core", "boundary"] = "core"
    system: str
    status: str
    trial_count: int = Field(ge=1)
    completed_trials: int = Field(ge=0)
    passed_trials: int = Field(ge=0)
    pass_all_trials: bool
    mean_required_check_rate: float = Field(ge=0, le=1)
    mean_elapsed_seconds: float = Field(ge=0)
    accounted_cost_usd: float = Field(ge=0)


class PairwiseComparisonRow(StrictModel):
    lane: Lane
    slice_type: Literal["overall", "scope", "split", "family"]
    slice_value: str
    system_a: str
    system_b: str
    common_cases: int = Field(ge=1)
    both_pass: int = Field(ge=0)
    a_only_pass: int = Field(ge=0)
    b_only_pass: int = Field(ge=0)
    neither_pass: int = Field(ge=0)
    pass_rate_delta: float = Field(ge=-1, le=1)
    exact_mcnemar_p_value: float = Field(ge=0, le=1)
    holm_adjusted_p_value: float = Field(ge=0, le=1)
    verdict: Literal[
        "a_better",
        "b_better",
        "no_significant_difference",
        "insufficient_operational_conformance",
    ]
    operationally_comparable: bool = True
    pass_rate_delta_ci95_low: float = Field(default=0, ge=-1, le=1)
    pass_rate_delta_ci95_high: float = Field(default=0, ge=-1, le=1)
    discordant_cases: int = Field(default=0, ge=0)


class ComparisonReport(StrictModel):
    schema_version: Literal[3] = 3
    analysis_version: str = "v2-legacy"
    suite_name: str
    suite_version: str
    suite_sha256: str
    protocol_sha256: str
    scorer_version: str = "v2-unversioned"
    system_count: int = Field(ge=1)
    source_report_schema_versions: list[Literal[2, 3]] = Field(default_factory=list)
    boundary_cases: dict[str, list[str]] = Field(default_factory=dict)
    rows: list[ComparisonRow] = Field(min_length=1)
    case_rows: list[ComparisonCaseRow] = Field(min_length=1)
    pairwise: list[PairwiseComparisonRow] = Field(default_factory=list)


def hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")
