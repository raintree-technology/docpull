"""Fail-closed evidence gates for public benchmark claims.

The ordinary benchmark commands deliberately remain useful for internal product
work.  This module adds a stricter, separate gate for evidence that may support
an external comparative claim.  It cannot manufacture independent review; it
only verifies signed, content-free attestations supplied by other parties.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import AfterValidator, Field, model_validator

from .models import BenchmarkSuite, Lane, PortableReport, StrictModel, hostname

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _nonzero_sha256(value: str) -> str:
    if value == "0" * 64:
        raise ValueError("placeholder SHA-256 values are not evidence")
    return value


EvidenceSha256 = Annotated[
    str,
    Field(pattern=_SHA256_PATTERN),
    AfterValidator(_nonzero_sha256),
]


def _signature_methods() -> list[Literal["gpg", "sigstore"]]:
    return ["gpg"]


class ClaimPolicy(StrictModel):
    """Minimum evidence policy for a lane-local public comparison."""

    schema_version: Literal[1, 2] = 1
    name: str = Field(min_length=1)
    owner_organizations: list[str] = Field(min_length=1)
    minimum_systems: int = Field(default=2, ge=1)
    minimum_cases_per_lane: int = Field(default=100, ge=1)
    minimum_test_cases_per_lane: int = Field(default=30, ge=1)
    minimum_families_per_lane: int = Field(default=5, ge=1)
    minimum_distinct_domains_per_live_lane: int = Field(default=10, ge=1)
    maximum_family_share: float = Field(default=0.25, gt=0, le=1)
    minimum_unique_input_ratio: float = Field(default=0.95, gt=0, le=1)
    minimum_repeats: int = Field(default=5, ge=1)
    minimum_operational_success_rate: float = Field(default=0.95, ge=0, le=1)
    minimum_independent_reviewers: int = Field(default=2, ge=1)
    minimum_discordant_cases_per_pair: int = Field(default=20, ge=1)
    require_clean_repository: bool = True
    require_sealed_holdout: bool = True
    require_protocol_attestations: bool = True
    require_actual_cost_reconciliation: bool = True
    require_latency_comparability: bool = False
    require_cryptographic_signature_verification: bool = True
    trusted_gpg_fingerprints: list[str] = Field(default_factory=list)
    trusted_reviewer_gpg_fingerprints: list[str] = Field(default_factory=list)
    trusted_custodian_gpg_fingerprints: list[str] = Field(default_factory=list)
    trusted_protocol_gpg_fingerprints: list[str] = Field(default_factory=list)
    trusted_billing_gpg_fingerprints: list[str] = Field(default_factory=list)
    allowed_signature_methods: list[Literal["gpg", "sigstore"]] = Field(default_factory=_signature_methods)

    @classmethod
    def from_yaml(cls, path: Path) -> ClaimPolicy:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class ReviewAttestation(StrictModel):
    review_id: str = Field(min_length=1)
    suite_sha256: EvidenceSha256
    gold_sha256: EvidenceSha256
    reviewer_identity_sha256: EvidenceSha256
    reviewer_organization: str = Field(min_length=1)
    independent: bool
    conflicts_disclosed: str = Field(min_length=1)
    reviewed_case_ids: list[str] = Field(min_length=1)
    reviewed_at: str
    expires_at: str
    signature_method: Literal["gpg", "sigstore"]
    signature_reference: str = Field(min_length=1)
    signer_fingerprint: str | None = None


class ProtocolAttestation(StrictModel):
    system: str = Field(min_length=1)
    adapter_config_sha256: EvidenceSha256
    request_schema_sha256: EvidenceSha256
    request_schema_reference: str | None = None
    official_documentation: list[str] = Field(min_length=1)
    confirmation: Literal["first_party_documentation", "provider_written_confirmation"]
    reviewed_at: str
    expires_at: str
    reviewer_identity_sha256: EvidenceSha256
    signature_method: Literal["gpg", "sigstore"]
    signature_reference: str = Field(min_length=1)
    signer_fingerprint: str | None = None


class BillingReconciliation(StrictModel):
    system: str = Field(min_length=1)
    report_sha256: EvidenceSha256
    actual_cost_usd: float = Field(ge=0)
    source: Literal["provider_api", "provider_invoice", "provider_dashboard"]
    evidence_sha256: EvidenceSha256
    evidence_reference: str | None = None
    account_id_sha256: EvidenceSha256
    captured_at: str
    attester_identity_sha256: EvidenceSha256
    signature_method: Literal["gpg", "sigstore"]
    signature_reference: str = Field(min_length=1)
    signer_fingerprint: str | None = None


class HoldoutSeal(StrictModel):
    suite_sha256: EvidenceSha256
    gold_sha256: EvidenceSha256
    held_case_ids: list[str] = Field(min_length=1)
    origin: Literal["never_published"]
    encryption: Literal["age", "sops", "external_vault"]
    ciphertext_sha256: EvidenceSha256
    sealed_at: str
    opened_at: str | None = None
    released_to_runner_at: str | None = None
    evaluation_completed_at: str | None = None
    publicly_disclosed_at: str | None = None
    runner_identity_sha256: EvidenceSha256 | None = None
    evaluation_report_sha256s: list[EvidenceSha256] = Field(default_factory=list)
    ciphertext_reference: str | None = None
    custodian_identity_sha256: EvidenceSha256
    signature_method: Literal["gpg", "sigstore"]
    signature_reference: str = Field(min_length=1)
    signer_fingerprint: str | None = None


class ClaimEvidence(StrictModel):
    schema_version: Literal[1, 2] = 1
    holdout: HoldoutSeal | None = None
    reviews: list[ReviewAttestation] = Field(default_factory=list)
    protocols: list[ProtocolAttestation] = Field(default_factory=list)
    billing: list[BillingReconciliation] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path | None) -> ClaimEvidence:
        if path is None:
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


class ClaimCheck(StrictModel):
    id: str
    passed: bool
    detail: str


class ClaimReadinessReport(StrictModel):
    schema_version: Literal[2] = 2
    generated_at: str
    policy: str
    policy_sha256: str = Field(pattern=_SHA256_PATTERN)
    evidence_sha256: str = Field(pattern=_SHA256_PATTERN)
    suite_name: str
    suite_sha256: str = Field(pattern=_SHA256_PATTERN)
    gold_sha256: str = Field(pattern=_SHA256_PATTERN)
    protocol_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    systems: list[str]
    report_sha256s: dict[str, str]
    ready: bool
    checks: list[ClaimCheck]

    @model_validator(mode="after")
    def ready_matches_checks(self) -> ClaimReadinessReport:
        if self.ready != all(check.passed for check in self.checks):
            raise ValueError("ready must equal the conjunction of claim checks")
        return self


def check_claim_readiness(
    suite_path: Path,
    report_paths: list[Path],
    *,
    policy: ClaimPolicy,
    evidence: ClaimEvidence,
    evidence_base: Path | None = None,
    policy_sha256: str | None = None,
    evidence_sha256: str | None = None,
) -> ClaimReadinessReport:
    """Return a content-free readiness report; never infer missing evidence."""
    if not report_paths:
        raise ValueError("claim readiness requires at least one report")
    suite = BenchmarkSuite.from_yaml(suite_path)
    evidence_base = (evidence_base or Path.cwd()).resolve()
    suite_sha256 = _file_sha256(suite_path)
    gold_sha256 = gold_hash(suite)
    reports = [PortableReport.model_validate_json(path.read_text(encoding="utf-8")) for path in report_paths]
    report_hashes = {_file_sha256(path): report for path, report in zip(report_paths, reports, strict=True)}
    policy_sha256 = policy_sha256 or _json_hash(policy.model_dump(mode="json"))
    evidence_sha256 = evidence_sha256 or _json_hash(evidence.model_dump(mode="json"))
    systems = [report.manifest.system for report in reports]
    checks: list[ClaimCheck] = []

    def add(check_id: str, passed: bool, detail: str) -> None:
        checks.append(ClaimCheck(id=check_id, passed=passed, detail=detail))

    add(
        "reports.minimum_systems",
        len(systems) >= policy.minimum_systems,
        f"observed={len(systems)} minimum={policy.minimum_systems}",
    )
    add("reports.unique_systems", len(systems) == len(set(systems)), f"systems={','.join(sorted(systems))}")
    add(
        "evidence.unique_reviews",
        len({item.review_id for item in evidence.reviews}) == len(evidence.reviews),
        f"observed={len(evidence.reviews)} unique={len({item.review_id for item in evidence.reviews})}",
    )
    add(
        "evidence.unique_protocols",
        len({item.system for item in evidence.protocols}) == len(evidence.protocols),
        f"observed={len(evidence.protocols)} unique={len({item.system for item in evidence.protocols})}",
    )
    add(
        "evidence.unique_billing",
        len({item.report_sha256 for item in evidence.billing}) == len(evidence.billing),
        f"observed={len(evidence.billing)} unique={len({item.report_sha256 for item in evidence.billing})}",
    )
    matching_suite = all(report.manifest.suite_sha256 == suite_sha256 for report in reports)
    add("reports.suite_hash", matching_suite, f"expected={suite_sha256}")
    protocols = {report.manifest.protocol_sha256 for report in reports}
    protocol_sha256 = next(iter(protocols)) if len(protocols) == 1 else None
    add("reports.protocol_hash", protocol_sha256 is not None, f"distinct={len(protocols)}")

    expected_case_ids = {case.id for case in suite.cases}
    for report in reports:
        observed_ids = {score.case_id for score in report.scores}
        add(
            f"coverage.{report.manifest.system}",
            observed_ids == expected_case_ids,
            f"observed={len(observed_ids)} expected={len(expected_case_ids)}",
        )
        add(
            f"trials.{report.manifest.system}",
            report.manifest.repeat >= policy.minimum_repeats,
            f"observed={report.manifest.repeat} minimum={policy.minimum_repeats}",
        )
        trial_counts = Counter(score.case_id for score in report.scores)
        trial_indices: dict[str, set[int]] = defaultdict(set)
        for score in report.scores:
            trial_indices[score.case_id].add(score.trial_index)
        exact_trials = all(
            trial_counts.get(case_id) == report.manifest.repeat
            and trial_indices.get(case_id) == set(range(1, report.manifest.repeat + 1))
            for case_id in expected_case_ids
        )
        add(
            f"trial_coverage.{report.manifest.system}",
            exact_trials,
            f"expected_per_case={report.manifest.repeat} complete={exact_trials}",
        )
        add(
            f"operations.{report.manifest.system}",
            report.summary.completion_rate >= policy.minimum_operational_success_rate,
            f"observed={report.summary.completion_rate:.3f} "
            f"minimum={policy.minimum_operational_success_rate:.3f}",
        )
        if policy.require_clean_repository:
            add(
                f"repository_clean.{report.manifest.system}",
                not report.manifest.git_dirty,
                f"git_dirty={report.manifest.git_dirty}",
            )

    outcomes: dict[str, dict[str, bool]] = {}
    for report in reports:
        by_case: dict[str, list[bool]] = defaultdict(list)
        for score in report.scores:
            by_case[score.case_id].append(score.passed)
        outcomes[report.manifest.system] = {case_id: all(trials) for case_id, trials in by_case.items()}
    lane_by_case = {case.id: case.input.lane for case in suite.cases}
    for system_a, system_b in combinations(sorted(outcomes), 2):
        for lane in sorted({case.input.lane for case in suite.cases}, key=lambda item: item.value):
            common = {
                case_id
                for case_id in set(outcomes[system_a]) & set(outcomes[system_b])
                if lane_by_case[case_id] == lane
            }
            discordant = sum(outcomes[system_a][case_id] != outcomes[system_b][case_id] for case_id in common)
            add(
                f"paired_information.{lane.value}.{system_a}.{system_b}",
                discordant >= policy.minimum_discordant_cases_per_pair,
                f"discordant={discordant} minimum={policy.minimum_discordant_cases_per_pair}",
            )

    cases_by_lane: dict[Lane, list[Any]] = defaultdict(list)
    for case in suite.cases:
        cases_by_lane[case.input.lane].append(case)
    for lane, cases in sorted(cases_by_lane.items(), key=lambda item: item[0].value):
        test_count = sum(case.metadata.split == "test" for case in cases)
        family_counts = Counter(case.metadata.family for case in cases)
        fingerprints = {_input_fingerprint(case.input.model_dump(mode="json")) for case in cases}
        add(
            f"sample_size.{lane.value}",
            len(cases) >= policy.minimum_cases_per_lane,
            f"observed={len(cases)} minimum={policy.minimum_cases_per_lane}",
        )
        add(
            f"holdout_size.{lane.value}",
            test_count >= policy.minimum_test_cases_per_lane,
            f"observed={test_count} minimum={policy.minimum_test_cases_per_lane}",
        )
        add(
            f"families.{lane.value}",
            len(family_counts) >= policy.minimum_families_per_lane,
            f"observed={len(family_counts)} minimum={policy.minimum_families_per_lane}",
        )
        largest_share = max(family_counts.values()) / len(cases)
        add(
            f"family_balance.{lane.value}",
            largest_share <= policy.maximum_family_share,
            f"largest_share={largest_share:.3f} maximum={policy.maximum_family_share:.3f}",
        )
        unique_ratio = len(fingerprints) / len(cases)
        add(
            f"unique_inputs.{lane.value}",
            unique_ratio >= policy.minimum_unique_input_ratio,
            f"observed={unique_ratio:.3f} minimum={policy.minimum_unique_input_ratio:.3f}",
        )
        if any(case.metadata.live for case in cases):
            domains = _case_domains(cases)
            add(
                f"domain_diversity.{lane.value}",
                len(domains) >= policy.minimum_distinct_domains_per_live_lane,
                f"observed={len(domains)} minimum={policy.minimum_distinct_domains_per_live_lane}",
            )

    today = date.today()
    stale = [
        case.id
        for case in suite.cases
        if case.metadata.live
        and case.metadata.reference_expires_at
        and date.fromisoformat(case.metadata.reference_expires_at) < today
    ]
    add("gold.freshness", not stale, f"stale_cases={len(stale)}")

    test_ids = {case.id for case in suite.cases if case.metadata.split == "test"}
    seal = evidence.holdout
    seal_signer = _signature_fingerprint(seal, policy, evidence_base, role="custodian") if seal else None
    legacy_holdout_ok = bool(
        seal
        and seal.suite_sha256 == suite_sha256
        and seal.gold_sha256 == gold_sha256
        and set(seal.held_case_ids) == test_ids
        and seal.opened_at is None
        and seal.origin == "never_published"
        and seal.signature_method in policy.allowed_signature_methods
        and seal_signer
    )
    holdout_ok = legacy_holdout_ok
    if policy.schema_version >= 2:
        report_sha256s = set(report_hashes)
        ciphertext_ok = bool(
            seal
            and seal.ciphertext_reference
            and _referenced_hash_matches(
                evidence_base,
                seal.ciphertext_reference,
                seal.ciphertext_sha256,
            )
        )
        custody_times_ok = bool(
            seal
            and seal.released_to_runner_at
            and seal.evaluation_completed_at
            and seal.publicly_disclosed_at is None
            and seal.runner_identity_sha256
            and _ordered_timestamps(
                seal.sealed_at,
                seal.released_to_runner_at,
                seal.evaluation_completed_at,
            )
            and _timestamp_is_not_future(seal.evaluation_completed_at)
            and all(
                _timestamp_between(
                    report.manifest.created_at,
                    seal.released_to_runner_at,
                    seal.evaluation_completed_at,
                )
                for report in reports
            )
        )
        holdout_ok = bool(
            legacy_holdout_ok
            and seal
            and ciphertext_ok
            and custody_times_ok
            and set(seal.evaluation_report_sha256s) == report_sha256s
        )
    add(
        "holdout.sealed",
        holdout_ok if policy.require_sealed_holdout else True,
        (
            "valid encrypted never-published holdout with signed evaluation custody required"
            if policy.schema_version >= 2
            else "valid unopened never-published seal required"
        )
        if policy.require_sealed_holdout
        else "not required",
    )

    valid_reviews = [
        (review, signer)
        for review in evidence.reviews
        if (signer := _signature_fingerprint(review, policy, evidence_base, role="reviewer"))
        if review.suite_sha256 == suite_sha256
        and review.gold_sha256 == gold_sha256
        and review.independent
        and review.reviewer_organization.casefold()
        not in {owner.casefold() for owner in policy.owner_organizations}
        and set(review.reviewed_case_ids) == expected_case_ids
        and review.signature_method in policy.allowed_signature_methods
        and _date_is_current(review.expires_at)
        and _date_is_not_future(review.reviewed_at)
    ]
    distinct_reviewers = {review.reviewer_identity_sha256 for review, _ in valid_reviews}
    distinct_review_orgs = {review.reviewer_organization.casefold() for review, _ in valid_reviews}
    distinct_review_signers = {signer for _, signer in valid_reviews}
    reviews_ok = (
        len(distinct_reviewers) >= policy.minimum_independent_reviewers
        and len(distinct_review_orgs) >= policy.minimum_independent_reviewers
        and len(distinct_review_signers) >= policy.minimum_independent_reviewers
    )
    add(
        "gold.independent_review",
        reviews_ok,
        f"reviewers={len(distinct_reviewers)} organizations={len(distinct_review_orgs)} "
        f"verified_signers={len(distinct_review_signers)} "
        f"minimum={policy.minimum_independent_reviewers}",
    )

    protocol_by_system = {item.system: item for item in evidence.protocols}
    for report in reports:
        attestation = protocol_by_system.get(report.manifest.system)
        protocol_signer = (
            _signature_fingerprint(attestation, policy, evidence_base, role="protocol")
            if attestation
            else None
        )
        protocol_ok = bool(
            attestation
            and attestation.adapter_config_sha256 == report.manifest.adapter_config_sha256
            and attestation.signature_method in policy.allowed_signature_methods
            and protocol_signer
            and _date_is_current(attestation.expires_at)
            and _date_is_not_future(attestation.reviewed_at)
            and all(url.startswith("https://") for url in attestation.official_documentation)
            and (
                policy.schema_version < 2
                or bool(
                    attestation.request_schema_reference
                    and _referenced_hash_matches(
                        evidence_base,
                        attestation.request_schema_reference,
                        attestation.request_schema_sha256,
                    )
                )
            )
        )
        add(
            f"protocol.{report.manifest.system}",
            protocol_ok if policy.require_protocol_attestations else True,
            "matching signed first-party protocol attestation required",
        )

    billing_by_report = {item.report_sha256: item for item in evidence.billing}
    for report_sha256, report in report_hashes.items():
        has_provider_cost = report.summary.observed_cost_usd > 0
        reconciliation = billing_by_report.get(report_sha256)
        billing_signer = (
            _signature_fingerprint(reconciliation, policy, evidence_base, role="billing")
            if reconciliation
            else None
        )
        billing_ok = not has_provider_cost or bool(
            reconciliation
            and reconciliation.system == report.manifest.system
            and reconciliation.signature_method in policy.allowed_signature_methods
            and billing_signer
            and _timestamp_is_not_future(reconciliation.captured_at)
            and abs(reconciliation.actual_cost_usd - report.summary.observed_cost_usd) <= 0.01
            and (
                policy.schema_version < 2
                or bool(
                    reconciliation.evidence_reference
                    and _referenced_hash_matches(
                        evidence_base,
                        reconciliation.evidence_reference,
                        reconciliation.evidence_sha256,
                    )
                )
            )
        )
        add(
            f"billing.{report.manifest.system}",
            billing_ok if policy.require_actual_cost_reconciliation else True,
            "signed provider total required" if has_provider_cost else "zero provider cost",
        )

    if policy.require_latency_comparability:
        classes = {(report.manifest.environment_label, report.manifest.cache_policy) for report in reports}
        add("latency.comparable", len(classes) == 1, f"environment_cache_classes={len(classes)}")

    return ClaimReadinessReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        policy=policy.name,
        policy_sha256=policy_sha256,
        evidence_sha256=evidence_sha256,
        suite_name=suite.name,
        suite_sha256=suite_sha256,
        gold_sha256=gold_sha256,
        protocol_sha256=protocol_sha256,
        systems=sorted(systems),
        report_sha256s={
            report.manifest.system: report_sha256 for report_sha256, report in report_hashes.items()
        },
        ready=all(check.passed for check in checks),
        checks=checks,
    )


def claim_readiness_markdown(report: ClaimReadinessReport) -> str:
    status = "READY" if report.ready else "NOT READY"
    lines = [
        f"# Claim readiness: {status}",
        "",
        f"Suite: `{report.suite_name}`",
        f"Suite SHA-256: `{report.suite_sha256}`",
        f"Policy: `{report.policy}`",
        f"Policy SHA-256: `{report.policy_sha256}`",
        f"Evidence SHA-256: `{report.evidence_sha256}`",
        "Report SHA-256 values: "
        + ", ".join(f"`{system}={value}`" for system, value in sorted(report.report_sha256s.items())),
        "",
        "| Gate | Status | Detail |",
        "| --- | --- | --- |",
    ]
    lines.extend(
        f"| {check.id} | {'pass' if check.passed else 'FAIL'} | {check.detail} |" for check in report.checks
    )
    lines.extend(
        [
            "",
            "A passing gate permits human review of a lane-local claim; it does not generate or "
            "approve claim language.",
        ]
    )
    return "\n".join(lines) + "\n"


def gold_hash(suite: BenchmarkSuite) -> str:
    payload = {
        case.id: case.expected.model_dump(mode="json", exclude_none=False)
        for case in sorted(suite.cases, key=lambda item: item.id)
    }
    return _json_hash(payload)


def _case_domains(cases: list[Any]) -> set[str]:
    output: set[str] = set()
    for case in cases:
        url = getattr(case.input, "url", None)
        if isinstance(url, str) and hostname(url):
            output.add(hostname(url))
        output.update(
            domain.casefold().rstrip(".") for domain in getattr(case.input, "include_domains", []) if domain
        )
    return output


def _input_fingerprint(payload: dict[str, Any]) -> str:
    payload = dict(payload)
    payload.pop("case_id", None)
    return _json_hash(payload)


def _date_is_current(value: str) -> bool:
    try:
        return date.fromisoformat(value) >= date.today()
    except ValueError:
        return False


def _date_is_not_future(value: str) -> bool:
    try:
        return date.fromisoformat(value) <= date.today()
    except ValueError:
        return False


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_is_not_future(value: str) -> bool:
    parsed = _parse_timestamp(value)
    return bool(parsed and parsed <= datetime.now(timezone.utc))


def _ordered_timestamps(*values: str) -> bool:
    parsed = [_parse_timestamp(value) for value in values]
    return bool(all(parsed) and all(a <= b for a, b in zip(parsed, parsed[1:], strict=False)))


def _timestamp_between(value: str, lower: str, upper: str) -> bool:
    parsed = _parse_timestamp(value)
    lower_parsed = _parse_timestamp(lower)
    upper_parsed = _parse_timestamp(upper)
    return bool(parsed and lower_parsed and upper_parsed and lower_parsed <= parsed <= upper_parsed)


def _normalized_fingerprint(value: str) -> str:
    return value.replace(" ", "").upper()


def _role_trusted_fingerprints(policy: ClaimPolicy, role: str) -> set[str]:
    role_values = {
        "reviewer": policy.trusted_reviewer_gpg_fingerprints,
        "custodian": policy.trusted_custodian_gpg_fingerprints,
        "protocol": policy.trusted_protocol_gpg_fingerprints,
        "billing": policy.trusted_billing_gpg_fingerprints,
    }.get(role, [])
    values = role_values if policy.schema_version >= 2 else role_values or policy.trusted_gpg_fingerprints
    return {_normalized_fingerprint(value) for value in values}


def _signature_fingerprint(
    attestation: StrictModel | None,
    policy: ClaimPolicy,
    evidence_base: Path,
    *,
    role: str,
) -> str | None:
    if attestation is None:
        return None
    if not policy.require_cryptographic_signature_verification:
        declared = getattr(attestation, "signer_fingerprint", None)
        if isinstance(declared, str) and declared:
            return _normalized_fingerprint(declared)
        for field in (
            "reviewer_identity_sha256",
            "custodian_identity_sha256",
            "attester_identity_sha256",
        ):
            fallback = getattr(attestation, field, None)
            if isinstance(fallback, str) and fallback:
                return fallback
        return None
    method = getattr(attestation, "signature_method", None)
    reference = getattr(attestation, "signature_reference", "")
    trusted = _role_trusted_fingerprints(policy, role)
    if method != "gpg" or not reference or not trusted:
        return None
    signature_path = (evidence_base / str(reference)).resolve()
    if evidence_base not in signature_path.parents or not signature_path.is_file():
        return None
    payload = attestation.model_dump(mode="json", exclude={"signature_method", "signature_reference"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--status-fd", "1", "--verify", str(signature_path), "-"],
            input=canonical,
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    valid = {
        line.split()[2].upper()
        for line in result.stdout.decode(errors="replace").splitlines()
        if line.startswith("[GNUPG:] VALIDSIG ") and len(line.split()) >= 3
    }
    matches = valid & trusted
    if result.returncode != 0 or not matches:
        return None
    declared = getattr(attestation, "signer_fingerprint", None)
    if policy.schema_version >= 2:
        if not isinstance(declared, str) or _normalized_fingerprint(declared) not in matches:
            return None
        return _normalized_fingerprint(declared)
    return sorted(matches)[0]


def _signature_is_valid(attestation: StrictModel, policy: ClaimPolicy, evidence_base: Path) -> bool:
    """Compatibility wrapper for callers using the v1 boolean helper."""
    return bool(_signature_fingerprint(attestation, policy, evidence_base, role="reviewer"))


def _referenced_hash_matches(base: Path, reference: str, expected_sha256: str) -> bool:
    path = (base / reference).resolve()
    return bool(base in path.parents and path.is_file() and _file_sha256(path) == expected_sha256)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()
