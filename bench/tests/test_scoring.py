from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docpull_bench.models import (
    ArtifactRecord,
    BenchmarkCase,
    BenchmarkSuite,
    ContentPayload,
    Lane,
    RankedResult,
    RunObservation,
    SearchPayload,
)
from docpull_bench.scoring import score_observation

ROOT = Path(__file__).resolve().parents[1]


def _cases() -> list[BenchmarkCase]:
    controlled = BenchmarkSuite.from_yaml(ROOT / "cases" / "controlled-v2.yaml")
    selected: list[BenchmarkCase] = []
    for lane in Lane:
        if lane is Lane.SEARCH:
            selected.append(BenchmarkSuite.from_yaml(ROOT / "cases" / "live-search-v2.yaml").cases[0])
            continue
        candidates = [case for case in controlled.cases if case.input.lane is lane]
        if lane in {Lane.PARSE, Lane.STRUCTURED}:
            candidates = [case for case in candidates if case.expected.expected_status == "completed"]
        if lane is Lane.POLICY:
            candidates = [case for case in candidates if case.expected.expected_status == "completed"]
        selected.append(candidates[0])
    return selected


def _passing_observation(case: BenchmarkCase) -> RunObservation:
    if case.input.lane is Lane.SEARCH:
        expected = case.expected
        result = RankedResult(
            identity=expected.relevant_urls[0],
            url=expected.relevant_urls[0],
            title=" ".join(expected.required_identifiers),
            excerpt=" ".join(expected.required_identifiers),
        )
        return RunObservation(
            case_id=case.id,
            system="fixture",
            status="completed",
            payload=SearchPayload(results=[result]),
            elapsed_seconds=0.1,
            adapter_version="2",
        )
    payload = json.loads((ROOT / "replays" / "controlled-v2" / f"{case.id}.json").read_text())
    return RunObservation.model_validate(payload)


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.input.lane.value)
def test_every_lane_canonical_scorer_full_pass(case: BenchmarkCase) -> None:
    score = score_observation(case, _passing_observation(case))
    assert score.passed
    assert score.required_check_rate == 1
    assert score.lane is case.input.lane


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case.input.lane.value)
def test_every_lane_rejects_adapter_failure_or_timeout(case: BenchmarkCase) -> None:
    observation = RunObservation(
        case_id=case.id,
        system="fixture",
        status="failed",
        elapsed_seconds=case.input.timeout_seconds,
        adapter_version="2",
        error="timeout or adapter failure",
    )
    assert not score_observation(case, observation).passed


def test_partial_output_and_missing_evidence_fail() -> None:
    case = _cases()[0]
    observation = _passing_observation(case).model_copy(update={"payload": SearchPayload(results=[])})
    score = score_observation(case, observation)
    assert not score.passed
    assert score.required_check_rate < 1


def test_unsupported_capability_is_explicit_not_fabricated_failure() -> None:
    case = _cases()[0]
    observation = RunObservation(
        case_id=case.id,
        system="fixture",
        status="unsupported",
        elapsed_seconds=0,
        attempt_count=0,
        adapter_version="2",
    )
    score = score_observation(case, observation)
    assert score.status == "unsupported"
    assert not score.passed


def test_malformed_observation_fails_schema_validation() -> None:
    with pytest.raises(ValidationError):
        RunObservation.model_validate(
            {
                "schema_version": 2,
                "case_id": "bad",
                "system": "fixture",
                "status": "completed",
                "elapsed_seconds": -1,
                "adapter_version": "2",
            }
        )


def test_extract_term_matching_tolerates_punctuation_but_rejects_fused_terms() -> None:
    suite = BenchmarkSuite.from_yaml(ROOT / "cases" / "live-neutral-extract-v1.yaml")
    case = next(item for item in suite.cases if item.id == "test.pdf.ray-paper")
    observation = RunObservation(
        case_id=case.id,
        system="docpull",
        status="completed",
        payload=ContentPayload(
            records=[
                ArtifactRecord(
                    url=case.input.url,
                    content=(
                        "Ray: A Distributed Framework. "
                        "Reinforcementlearning workloads use distributedsystems."
                        + (" supporting evidence" * 700)
                    ),
                )
            ],
            selected_urls=[case.input.url],
        ),
        elapsed_seconds=0.1,
        adapter_version="test",
    )

    assert not score_observation(case, observation).passed
    separated = observation.model_copy(
        update={
            "payload": ContentPayload(
                records=[
                    ArtifactRecord(
                        url=case.input.url,
                        content=(
                            "Ray: A Distributed Framework. Reinforcement learning workloads use "
                            "distributed-systems." + (" supporting evidence" * 700)
                        ),
                    )
                ],
                selected_urls=[case.input.url],
            )
        }
    )
    assert score_observation(case, separated).passed


def test_explicit_line_break_hyphenation_is_repaired() -> None:
    suite = BenchmarkSuite.from_yaml(ROOT / "cases" / "live-neutral-extract-v1.yaml")
    case = next(item for item in suite.cases if item.id == "test.pdf.ray-paper")
    observation = RunObservation(
        case_id=case.id,
        system="docpull",
        status="completed",
        payload=ContentPayload(
            records=[
                ArtifactRecord(
                    url=case.input.url,
                    content=(
                        "Ray: A Distributed Framework. Reinforce-\nment learning workloads use "
                        "distributed systems." + (" supporting evidence" * 700)
                    ),
                )
            ],
            selected_urls=[case.input.url],
        ),
        elapsed_seconds=0.1,
        adapter_version="test",
    )

    assert score_observation(case, observation).passed


def test_optional_content_quality_assertions_are_deterministic() -> None:
    original = next(case for case in _cases() if case.input.lane is Lane.EXTRACT)
    expected = original.expected.model_copy(
        update={
            "minimum_records": 1,
            "minimum_content_chars": 20,
            "maximum_content_chars": 500,
            "required_terms": ["openapi: 3.0.0"],
            "forbidden_terms": ["secret"],
            "required_ordered_terms": ["openapi", "value"],
            "maximum_long_token_rate": 0.0,
            "minimum_markdown_links": 1,
            "minimum_fenced_code_blocks": 1,
            "minimum_markdown_table_rows": 2,
            "required_urls": [],
            "allowed_domains": [],
            "required_headings": [],
        }
    )
    case = original.model_copy(update={"expected": expected})
    content = (
        "OpenAPI: 3.0.0\n\n[Specification](https://example.com/spec)\n\n"
        '```json\n{"ok": true}\n```\n\n| Name | Value |\n| --- | --- |\n'
    )
    observation = RunObservation(
        case_id=case.id,
        system="fixture",
        status="completed",
        payload=ContentPayload(records=[ArtifactRecord(url=case.input.url, content=content)]),
        elapsed_seconds=0.1,
        adapter_version="test",
    )

    score = score_observation(case, observation)

    assert score.passed
    assert score.metrics["markdown_links"] == 1
    assert score.metrics["fenced_code_blocks"] == 1
    assert score.metrics["markdown_table_rows"] == 2
    assert score.metrics["long_token_rate"] == 0.0
