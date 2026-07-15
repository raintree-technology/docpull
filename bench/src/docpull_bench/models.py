"""Versioned, framework-neutral contracts for the DocPull evaluation lab."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 2
MetricValue: TypeAlias = bool | int | float | str | None


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
    backend: Literal["auto", "builtin", "markitdown", "unstructured"] = "auto"


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
    required_terms: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)


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
    required_ordered_terms: list[str] = Field(default_factory=list)
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
    rights: RightsMetadata


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
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    schema_version: Literal[2] = 2
    case_id: str
    trial_index: int = Field(default=1, ge=1)
    lane: Lane
    split: Literal["dev", "test"]
    family: str
    critical: bool
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


class RunManifest(StrictModel):
    schema_version: Literal[2] = 2
    run_id: str
    created_at: str
    suite_name: str
    suite_version: str
    suite_sha256: str
    fixture_manifest_sha256: str | None = None
    protocol_sha256: str
    system: str
    adapter_version: str
    adapter_config_sha256: str
    git_revision: str | None = None
    git_dirty: bool
    dependency_lock_sha256: str | None = None
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


class PortableReport(StrictModel):
    schema_version: Literal[2] = 2
    manifest: RunManifest
    observations: list[ReportObservation]
    scores: list[CaseScore]
    summary: RunSummary


class ComparisonRow(StrictModel):
    lane: Lane
    slice_type: Literal["overall", "split", "family"]
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
    slice_type: Literal["overall", "split", "family"]
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
    verdict: Literal["a_better", "b_better", "no_significant_difference"]
    pass_rate_delta_ci95_low: float = Field(default=0, ge=-1, le=1)
    pass_rate_delta_ci95_high: float = Field(default=0, ge=-1, le=1)
    discordant_cases: int = Field(default=0, ge=0)


class ComparisonReport(StrictModel):
    schema_version: Literal[2] = 2
    analysis_version: str = "v2-legacy"
    suite_name: str
    suite_version: str
    suite_sha256: str
    protocol_sha256: str
    system_count: int = Field(ge=1)
    rows: list[ComparisonRow] = Field(min_length=1)
    case_rows: list[ComparisonCaseRow] = Field(min_length=1)
    pairwise: list[PairwiseComparisonRow] = Field(default_factory=list)


def hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".")
