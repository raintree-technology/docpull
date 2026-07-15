from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from docpull_bench.models import (
    BenchmarkInput,
    BenchmarkSuite,
    CaseMetadata,
    Lane,
    RightsMetadata,
    SubjectIdentity,
)

ROOT = Path(__file__).resolve().parents[1]


def test_full_controlled_corpus_has_required_lane_counts() -> None:
    suite = BenchmarkSuite.from_yaml(ROOT / "cases" / "controlled-v2.yaml")
    counts = {lane: sum(case.input.lane == lane for case in suite.cases) for lane in Lane}
    assert counts == {
        Lane.EXTRACT: 12,
        Lane.CRAWL: 6,
        Lane.PARSE: 10,
        Lane.PACK: 10,
        Lane.STRUCTURED: 12,
        Lane.LIFECYCLE: 10,
        Lane.CHANGE: 12,
        Lane.RETRIEVAL: 100,
        Lane.SEARCH: 0,
        Lane.RESEARCH: 20,
        Lane.POLICY: 20,
    }
    unanswerable = [
        case for case in suite.cases if case.input.lane is Lane.RETRIEVAL and case.expected.expected_empty
    ]
    assert len(unanswerable) == 20


def test_live_search_has_freshness_metadata_and_thirty_cases() -> None:
    suite = BenchmarkSuite.from_yaml(ROOT / "cases" / "live-search-v2.yaml")
    assert len(suite.cases) == 30
    assert all(case.metadata.reference_checked_at for case in suite.cases)
    assert all(case.metadata.reference_expires_at for case in suite.cases)


def test_discriminated_input_rejects_missing_lane_field() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BenchmarkInput).validate_python({"case_id": "missing-query", "lane": "search"})


def test_schema_version_fails_closed(tmp_path: Path) -> None:
    source = (ROOT / "cases" / "controlled-v2.yaml").read_text(encoding="utf-8")
    path = tmp_path / "future.yaml"
    path.write_text(source.replace("schema_version: 2", "schema_version: 3", 1), encoding="utf-8")
    with pytest.raises(ValidationError):
        BenchmarkSuite.from_yaml(path)


def test_suite_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    source = (ROOT / "cases" / "controlled-v2.yaml").read_text(encoding="utf-8")
    path = tmp_path / "ambiguous.yaml"
    path.write_text(source.replace("name:", "name: forged\nname:", 1), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate YAML key: name"):
        BenchmarkSuite.from_yaml(path)


def test_gold_never_appears_in_serialized_input() -> None:
    case = BenchmarkSuite.from_yaml(ROOT / "cases" / "controlled-v2.yaml").cases[0]
    serialized = case.input.model_dump_json()
    assert "extract-marker" not in serialized
    assert "required_terms" not in serialized


def test_boundary_scope_requires_a_predeclared_reason() -> None:
    rights = RightsMetadata(redistribution="allowed", source="fixture")
    with pytest.raises(ValidationError, match="boundary_reason"):
        CaseMetadata(description="boundary", comparison_scope="boundary", rights=rights)
    with pytest.raises(ValidationError, match="cannot declare"):
        CaseMetadata(
            description="core",
            comparison_scope="core",
            boundary_reason="robots_policy",
            rights=rights,
        )


def test_remote_subject_profile_hash_is_derived_from_snapshot() -> None:
    profile = {"endpoint": "/extract", "mode": "full"}

    with pytest.raises(ValidationError, match="profile hash does not match"):
        SubjectIdentity(
            kind="remote-service",
            public_request_profile=profile,
            public_request_profile_sha256="0" * 64,
        )
