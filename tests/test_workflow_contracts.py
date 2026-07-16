"""Compatibility and contract tests for evidence acquisition workflows."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator

from docpull.change_events import build_change_events
from docpull.context_packs.policy_pack import build_policy_pack
from docpull.contracts import (
    CONTRACT_MODELS,
    ArtifactManifest,
    ChangeEvent,
    IntelligenceBundle,
    WorkflowRequest,
    WorkflowResult,
    bundled_schema_path,
)
from docpull.models.document import DocumentRecord
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack
from docpull.pack_tools import build_intelligence_bundle, prepare_pack
from docpull.project import add_source, init_project, load_project_config
from docpull.workflows import create_workflow_request, run_workflow
from tests.pack_fixtures import write_context_pack

FIXTURES = Path(__file__).with_name("fixtures") / "evidence_failure_modes.json"


class WorkflowFetcher:
    pages: dict[str, dict[str, str]] = {}

    def __init__(self, _config: object) -> None:
        pass

    async def __aenter__(self) -> WorkflowFetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
        assert save is False
        page = self.pages[url]
        return SimpleNamespace(
            error=None,
            should_skip=False,
            skip_reason=None,
            html=page["html"].encode(),
            markdown=page["markdown"],
            title=page.get("title", "Acme"),
            metadata={},
            extraction_info={},
            source_type="fixture",
        )


@pytest.fixture
def failure_modes() -> dict[str, dict[str, str]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def test_product_workflow_emits_generic_contracts_and_eval_grade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_modes: dict[str, dict[str, str]],
) -> None:
    pricing = failure_modes["pricing"]
    WorkflowFetcher.pages = {pricing["url"]: pricing}
    monkeypatch.setattr("docpull.context_packs.common.Fetcher", WorkflowFetcher)
    output = tmp_path / "product"

    request = create_workflow_request(
        "product-pack",
        pricing["url"],
        output_dir=output,
        options={"mode": "page", "max_pages": 1},
    )
    result_payload = run_workflow(request)
    legacy = json.loads((output / "products.result.json").read_text(encoding="utf-8"))
    manifest_payload = json.loads((output / "artifact.manifest.json").read_text(encoding="utf-8"))

    result = WorkflowResult.model_validate(result_payload)
    manifest = ArtifactManifest.model_validate(manifest_payload)
    stored_request = WorkflowRequest.model_validate_json(
        (output / "workflow.request.json").read_text(encoding="utf-8")
    )
    assert result.request_id == request.request_id == stored_request.request_id
    assert result.progress_events[0].phase == "run"
    assert result.budget_usage.estimated_usd == 0
    assert result.replay_configuration.local_first is True
    assert manifest.aggregate_sha256 == result.hashes["pack"].digest
    assert legacy["pricing_matrix"][0]["currency"] == "USD"
    assert legacy["pricing_matrix"][0]["billing_interval"] == {"unit": "month", "count": 1}
    assert legacy["pricing_matrix"][0]["trial"]["duration_days"] == 14
    assert legacy["pricing_matrix"][0]["price_source"]["medium"] == "page_text"
    assert all(row["price"] != 2_000_000 for row in legacy["pricing_matrix"])
    span = legacy["pricing_matrix"][0]["evidence"]["evidence_span"]
    assert pricing["markdown"][span["char_start"] : span["char_end"]] == span["exact_text"]

    assert validate_pack_contract(output, level="raw")["status"] == "pass"
    prepare_pack(output, graph=False, eval_grade=True, markdown=False)
    assert validate_pack_contract(output, level="agent")["status"] == "pass"
    assert validate_pack_contract(output, level="eval")["status"] == "pass"


def test_policy_pack_discovers_types_dates_stable_clauses_and_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_modes: dict[str, dict[str, str]],
) -> None:
    legal = failure_modes["legal"]
    home_url = "https://acme.test/"
    home = {
        "title": "Acme",
        "html": '<html><body><a href="/legal/privacy">Privacy</a></body></html>',
        "markdown": "[Privacy](/legal/privacy)",
    }
    WorkflowFetcher.pages = {home_url: home, legal["url"]: legal}
    monkeypatch.setattr("docpull.context_packs.common.Fetcher", WorkflowFetcher)
    baseline = tmp_path / "baseline"
    first = build_policy_pack("acme.test", output_dir=baseline)

    assert first["policies"][0]["document_type"] == "privacy"
    assert first["policies"][0]["effective_date"] == "July 1, 2026"
    assert len(first["clauses"]) == 3
    assert first["clauses"][1]["clause_id"] != first["clauses"][2]["clause_id"]
    assert first["clauses"][1]["evidence"]["evidence_span"]["char_start"] >= 0

    revised = dict(legal)
    revised["markdown"] = legal["markdown"].replace(
        "We retain billing records.", "We retain billing records for seven years."
    )
    WorkflowFetcher.pages = {home_url: home, legal["url"]: revised}
    current = build_policy_pack(
        "acme.test",
        output_dir=tmp_path / "current",
        baseline_pack=baseline,
    )
    assert current["summary"]["change_candidate_count"] == 1
    assert current["change_candidates"][0]["classification"] == "policy"
    assert current["change_candidates"][0]["status"] == "candidate"


def test_change_events_are_idempotent_and_separate_change_layers() -> None:
    old = DocumentRecord.from_page(
        url="https://acme.test/pricing",
        title="Pricing",
        content="# Pricing\nPro is $20 per month.",
    ).model_dump(mode="json", exclude_none=True)
    new = DocumentRecord.from_page(
        url="https://acme.test/pricing",
        title="Plans and pricing",
        content="# Plans\nPro is $29 per month with a 14-day trial.",
    ).model_dump(mode="json", exclude_none=True)

    first = build_change_events({old["url"]: [old]}, {new["url"]: [new]}, workflow="product-pack")
    second = build_change_events({old["url"]: [old]}, {new["url"]: [new]}, workflow="product-pack")

    assert first == second
    event = ChangeEvent.model_validate(first[0])
    assert event.old_document_id == old["document_id"]
    assert event.new_document_id == new["document_id"]
    assert event.structural_changes
    assert event.textual_changes
    assert event.semantic_candidates
    assert "pricing" in event.classifications
    assert event.replay_configuration.scheduler is None


def test_intelligence_bundle_is_deterministic_and_keeps_company_brain_alias(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    content = "Acme Pro pricing is $29 per month and includes cited product evidence."
    record = DocumentRecord.from_page(
        url="https://acme.test/pricing",
        title="Acme pricing",
        content=content,
        source_citation_id="S1",
        record_citation_id="S1.1",
    ).model_dump(mode="json", exclude_none=True)
    write_context_pack(pack, records=[record], include_domains=["acme.test"])

    first = build_intelligence_bundle(
        pack,
        objective="Track Acme pricing",
        market="Developer tools",
        search_queries=["pricing"],
    )
    first_bytes = (pack / "intelligence.bundle.v1.json").read_bytes()
    second = build_intelligence_bundle(
        pack,
        objective="Track Acme pricing",
        market="Developer tools",
        search_queries=["pricing"],
    )
    second_bytes = (pack / "intelligence.bundle.v1.json").read_bytes()

    parsed = IntelligenceBundle.model_validate(second)
    assert first["bundle_hash"] == second["bundle_hash"]
    assert first_bytes == second_bytes
    assert parsed.observations
    assert parsed.observations[0].status == "observation"
    assert parsed.observations[0].evidence[0].document_version == record["content_hash"]
    assert (pack / "company_brain.bundle.json").read_bytes() == second_bytes


def test_all_schemas_are_draft_2020_12_and_accept_model_examples(tmp_path: Path) -> None:
    for filename, model in CONTRACT_MODELS.items():
        schema = json.loads(bundled_schema_path(filename).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        assert schema["$id"].endswith(filename)
        assert model.model_json_schema(mode="serialization")["title"] == schema["title"]

    exported = tmp_path / "schemas"
    from docpull.contracts import write_contract_schemas

    assert len(write_contract_schemas(exported)) == len(CONTRACT_MODELS)


def test_declarative_project_accepts_knowledge_workflow_source_types(tmp_path: Path) -> None:
    init_project(name="tracker", root=tmp_path)
    for index, source_type in enumerate(("brand", "product", "styleguide", "visual", "policy")):
        add_source(
            f"https://{source_type}-{index}.example.test",
            name=source_type,
            source_type=source_type,
            root=tmp_path,
        )
    config = load_project_config(tmp_path)
    assert [source.type for source in config.sources] == [
        "brand",
        "product",
        "styleguide",
        "visual",
        "policy",
    ]


def test_pre_v6_pack_contract_remains_readable(tmp_path: Path) -> None:
    pack = tmp_path / "legacy"
    records = write_context_pack(pack)
    loaded = load_pack(pack)
    assert loaded.documents[0].document_id == records[0]["document_id"]
    assert hashlib.sha256(loaded.documents[0].content.encode()).hexdigest()
