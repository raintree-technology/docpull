from __future__ import annotations

import hashlib
from pathlib import Path

from docpull_bench.claims import (
    BillingReconciliation,
    ClaimEvidence,
    ClaimPolicy,
    HoldoutSeal,
    ProtocolAttestation,
    ReviewAttestation,
    _role_trusted_fingerprints,
    check_claim_readiness,
    claim_readiness_markdown,
    gold_hash,
)
from docpull_bench.models import BenchmarkSuite, PortableReport

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "cases" / "live-search-v2.yaml"
REPORT = ROOT / "results" / "manual" / "2026-07-14-live-search-v2" / "reports" / "exa-search.report.json"
EVIDENCE_HASH = "a" * 64


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relaxed_policy() -> ClaimPolicy:
    return ClaimPolicy(
        name="test-policy",
        owner_organizations=["Raintree Technology", "DocPull"],
        minimum_systems=1,
        minimum_cases_per_lane=30,
        minimum_test_cases_per_lane=1,
        minimum_families_per_lane=5,
        minimum_distinct_domains_per_live_lane=10,
        maximum_family_share=0.2,
        minimum_unique_input_ratio=0.95,
        minimum_repeats=2,
        minimum_operational_success_rate=0.95,
        minimum_independent_reviewers=2,
        require_clean_repository=False,
        require_cryptographic_signature_verification=False,
    )


def _evidence() -> ClaimEvidence:
    suite = BenchmarkSuite.from_yaml(SUITE)
    report = PortableReport.model_validate_json(REPORT.read_text(encoding="utf-8"))
    suite_hash = _sha256(SUITE)
    expected_hash = gold_hash(suite)
    all_ids = [case.id for case in suite.cases]
    test_ids = [case.id for case in suite.cases if case.metadata.split == "test"]
    reviews = [
        ReviewAttestation(
            review_id=f"review-{index}",
            suite_sha256=suite_hash,
            gold_sha256=expected_hash,
            reviewer_identity_sha256=str(index) * 64,
            reviewer_organization=f"Independent Org {index}",
            independent=True,
            conflicts_disclosed="none",
            reviewed_case_ids=all_ids,
            reviewed_at="2026-07-14",
            expires_at="2099-12-31",
            signature_method="gpg",
            signature_reference=f"review-{index}.asc",
        )
        for index in (1, 2)
    ]
    return ClaimEvidence(
        holdout=HoldoutSeal(
            suite_sha256=suite_hash,
            gold_sha256=expected_hash,
            held_case_ids=test_ids,
            origin="never_published",
            encryption="external_vault",
            ciphertext_sha256=EVIDENCE_HASH,
            sealed_at="2026-07-14T00:00:00Z",
            custodian_identity_sha256="3" * 64,
            signature_method="gpg",
            signature_reference="holdout.asc",
        ),
        reviews=reviews,
        protocols=[
            ProtocolAttestation(
                system=report.manifest.system,
                adapter_config_sha256=report.manifest.adapter_config_sha256,
                request_schema_sha256=EVIDENCE_HASH,
                official_documentation=["https://exa.ai/docs/reference/search"],
                confirmation="first_party_documentation",
                reviewed_at="2026-07-14",
                expires_at="2099-12-31",
                reviewer_identity_sha256="4" * 64,
                signature_method="gpg",
                signature_reference="protocol.asc",
            )
        ],
        billing=[
            BillingReconciliation(
                system=report.manifest.system,
                report_sha256=_sha256(REPORT),
                actual_cost_usd=0.42,
                source="provider_api",
                evidence_sha256=EVIDENCE_HASH,
                account_id_sha256="5" * 64,
                captured_at="2026-07-14T00:00:00Z",
                attester_identity_sha256="6" * 64,
                signature_method="gpg",
                signature_reference="billing.asc",
            )
        ],
    )


def test_claim_gate_fails_closed_without_external_evidence() -> None:
    result = check_claim_readiness(
        SUITE,
        [REPORT],
        policy=ClaimPolicy(
            name="public",
            owner_organizations=["Raintree Technology", "DocPull"],
        ),
        evidence=ClaimEvidence(),
    )
    assert not result.ready
    failed = {check.id for check in result.checks if not check.passed}
    assert "sample_size.search" in failed
    assert "holdout.sealed" in failed
    assert "gold.independent_review" in failed
    assert "billing.exa-search" in failed
    assert "# Claim readiness: NOT READY" in claim_readiness_markdown(result)


def test_claim_gate_accepts_complete_matching_evidence_under_explicit_policy() -> None:
    result = check_claim_readiness(
        SUITE,
        [REPORT],
        policy=_relaxed_policy(),
        evidence=_evidence(),
    )
    assert result.ready
    assert all(check.passed for check in result.checks)
    assert len(result.policy_sha256) == 64
    assert len(result.evidence_sha256) == 64
    assert result.report_sha256s["exa-search"] == _sha256(REPORT)


def test_claim_gate_requires_distinct_verified_review_signers() -> None:
    evidence = _evidence()
    reviews = [review.model_copy(update={"signer_fingerprint": "A" * 40}) for review in evidence.reviews]
    result = check_claim_readiness(
        SUITE,
        [REPORT],
        policy=_relaxed_policy(),
        evidence=evidence.model_copy(update={"reviews": reviews}),
    )

    assert not result.ready
    check = next(item for item in result.checks if item.id == "gold.independent_review")
    assert not check.passed
    assert "verified_signers=1" in check.detail


def test_claim_gate_rejects_incomplete_trial_vectors(tmp_path: Path) -> None:
    report = PortableReport.model_validate_json(REPORT.read_text(encoding="utf-8"))
    changed = report.model_copy(update={"scores": report.scores[:-1]})
    path = tmp_path / "partial.report.json"
    path.write_text(changed.model_dump_json(), encoding="utf-8")
    evidence = _evidence()
    evidence = evidence.model_copy(
        update={
            "billing": [item.model_copy(update={"report_sha256": _sha256(path)}) for item in evidence.billing]
        }
    )

    result = check_claim_readiness(
        SUITE,
        [path],
        policy=_relaxed_policy(),
        evidence=evidence,
    )

    check = next(item for item in result.checks if item.id == "trial_coverage.exa-search")
    assert not check.passed


def test_v2_role_trust_does_not_fall_back_to_a_global_signer_list() -> None:
    policy = _relaxed_policy().model_copy(
        update={"schema_version": 2, "trusted_gpg_fingerprints": ["A" * 40]}
    )

    assert _role_trusted_fingerprints(policy, "reviewer") == set()


def test_v2_requires_referenced_protocol_and_billing_evidence(tmp_path: Path) -> None:
    policy = _relaxed_policy().model_copy(update={"schema_version": 2, "require_sealed_holdout": False})
    result = check_claim_readiness(
        SUITE,
        [REPORT],
        policy=policy,
        evidence=_evidence().model_copy(update={"schema_version": 2}),
        evidence_base=tmp_path,
    )

    assert not next(item for item in result.checks if item.id == "protocol.exa-search").passed
    assert not next(item for item in result.checks if item.id == "billing.exa-search").passed
