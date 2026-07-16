from __future__ import annotations

from pathlib import Path

import pytest

from docpull.context_packs.common import PageSnapshot
from docpull.context_packs.relationship import (
    build_relationship_pack,
    extract_relationship_candidates_from_records,
)
from docpull.contracts import IntelligenceBundle, RelationshipPack, WorkflowResult
from docpull.models.document import DocumentRecord
from docpull.pack_tools import build_intelligence_bundle
from docpull.workflows import create_workflow_request, run_workflow
from tests.pack_fixtures import write_context_pack


def _page(name: str, content: str, index: int) -> PageSnapshot:
    return PageSnapshot(
        url=f"https://brand-{index}.example.test/about",
        title=name,
        html="",
        markdown=content,
        metadata={"entity_name": name, "official_domains": [f"brand-{index}.example.test"]},
        extraction={},
        source_type="fixture",
    )


@pytest.mark.parametrize(
    ("text", "predicate", "expected_subject", "expected_object"),
    [
        ("Bagel Brand is owned by Parent Company.", "owned_by", "Bagel Brand", "Parent Company"),
        (
            "Bagel Brand is operated by Hospitality Group.",
            "operated_by",
            "Bagel Brand",
            "Hospitality Group",
        ),
        (
            "Bagel Brand was acquired by Acquisition Group.",
            "acquired_by",
            "Bagel Brand",
            "Acquisition Group",
        ),
        (
            "Bagel Brand is a franchise of Franchise Group.",
            "franchised_by",
            "Bagel Brand",
            "Franchise Group",
        ),
        (
            "Investor Group invested in Bagel Brand.",
            "invested_in",
            "Investor Group",
            "Bagel Brand",
        ),
    ],
)
def test_relationship_predicates_preserve_direction_and_evidence(
    text: str,
    predicate: str,
    expected_subject: str,
    expected_object: str,
) -> None:
    record = DocumentRecord.from_page(
        url="https://bagel.example/about",
        title="Bagel Brand",
        content=text,
        metadata={"entity_name": "Bagel Brand"},
    ).model_dump(mode="json", exclude_none=True)

    candidate = extract_relationship_candidates_from_records([record])[0]

    assert candidate["predicate"] == predicate
    assert candidate["subject"]["name"] == expected_subject
    assert candidate["object"]["name"] == expected_object
    span = candidate["evidence"][0]
    assert text[span["char_start"] : span["char_end"]] == span["exact_text"]


def test_relationship_pack_emits_exactly_one_coverage_result_for_64_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = [
        {
            "input_id": f"bagel-{index:02d}",
            "name": f"Bagel Brand {index}",
            "url": f"https://brand-{index}.example.test/",
            "location_scope": f"Chicago storefront {index}",
        }
        for index in range(64)
    ]

    def fake_pages(spec, *, run, max_pages):
        del max_pages
        index = int(str(spec["input_id"]).rsplit("-", 1)[1])
        if index % 4 == 0:
            return [_page(spec["name"], f"{spec['name']} is owned by Parent Company {index}.", index)]
        if index % 4 == 1:
            return [_page(spec["name"], f"{spec['name']} serves bagels and coffee.", index)]
        if index % 4 == 2:
            run.errors.append(
                {
                    "url": spec["url"],
                    "error": "HTTP 429",
                    "http_status": 429,
                    "attempts": 4,
                    "retry_after_seconds": 60,
                    "stage": "fetch",
                }
            )
            return []
        run.errors.append(
            {
                "url": spec["url"],
                "error": "robots_disallowed",
                "code": "robots_disallowed",
                "stage": "policy",
                "blocked": True,
            }
        )
        return []

    monkeypatch.setattr("docpull.context_packs.relationship._pages_for_source", fake_pages)
    result = build_relationship_pack(inputs, output_dir=tmp_path / "relationships")
    contract = RelationshipPack.model_validate_json(
        (tmp_path / "relationships" / "relationship.pack.v1.json").read_text(encoding="utf-8")
    )

    assert result["summary"]["input_count"] == 64
    assert len(contract.coverage) == 64
    assert {item.input_id for item in contract.coverage} == {item["input_id"] for item in inputs}
    assert {item.status for item in contract.coverage} == {
        "candidate_found",
        "acquired_no_candidate",
        "retryable_failure",
        "blocked",
    }
    assert all(item.coverage_gap == (item.status != "candidate_found") for item in contract.coverage)
    assert all(candidate.status == "observation" for candidate in contract.candidates)
    assert all(candidate.evidence for candidate in contract.candidates)
    assert all(
        evidence.exact_text_sha256 and evidence.char_end > evidence.char_start
        for candidate in contract.candidates
        for evidence in candidate.evidence
    )
    assert not any(candidate.object.name.casefold() == "independent" for candidate in contract.candidates)
    retryable = next(item for item in contract.coverage if item.status == "retryable_failure")
    expected_failure = {
        "code": "http_429",
        "stage": "fetch",
        "retryable": True,
        "http_status": 429,
        "attempts": 4,
        "retry_after_seconds": 60.0,
    }
    actual_failure = retryable.failures[0].model_dump(exclude_none=True)
    assert {key: actual_failure[key] for key in expected_failure} == expected_failure


def test_relationship_workflow_registry_emits_workflow_result(tmp_path: Path) -> None:
    pack = tmp_path / "source-pack"
    record = DocumentRecord.from_page(
        url="https://bagel.example/about",
        title="Bagel Brand",
        content="Bagel Brand is operated by Example Hospitality Group.",
        metadata={"entity_name": "Bagel Brand", "official_domains": ["bagel.example"]},
    ).model_dump(mode="json", exclude_none=True)
    write_context_pack(pack, records=[record], include_domains=["bagel.example"])
    output = tmp_path / "result"
    request = create_workflow_request("relationship-pack", str(pack), output_dir=output)

    payload = run_workflow(request)

    result = WorkflowResult.model_validate(payload)
    assert result.workflow == "relationship-pack"
    assert result.data["relationship_candidates"][0]["predicate"] == "operated_by"


def test_intelligence_bundle_extracts_relationships_and_classifies_each_official_domain(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "multi-company"
    records = [
        DocumentRecord.from_page(
            url="https://first.example/about",
            title="First Brand",
            content="First Brand is owned by First Parent.",
            metadata={"entity_name": "First Brand", "official_domains": ["first.example"]},
            source_citation_id="S1",
            record_citation_id="S1.1",
        ).model_dump(mode="json", exclude_none=True),
        DocumentRecord.from_page(
            url="https://second.example/company",
            title="Second Brand",
            content="Second Brand was acquired by Second Parent.",
            metadata={"entity_name": "Second Brand", "official_domains": ["second.example"]},
            source_citation_id="S2",
            record_citation_id="S2.1",
        ).model_dump(mode="json", exclude_none=True),
    ]
    write_context_pack(pack, records=records, include_domains=["first.example", "second.example"])

    payload = build_intelligence_bundle(pack, default_search=False)
    parsed = IntelligenceBundle.model_validate(payload)

    assert {candidate.predicate for candidate in parsed.relationship_candidates} == {
        "owned_by",
        "acquired_by",
    }
    assert all(candidate.status == "observation" for candidate in parsed.relationship_candidates)
    by_url = {snapshot.url: snapshot for snapshot in parsed.source_snapshots}
    assert by_url["https://first.example/about"].authority.role == "official_corporate"
    assert by_url["https://second.example/company"].authority.role == "official_corporate"
    assert by_url["https://second.example/company"].official_domains == ["second.example"]
