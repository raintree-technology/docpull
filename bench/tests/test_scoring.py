from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from docpull_bench.models import (
    BenchmarkCase,
    BenchmarkSuite,
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
