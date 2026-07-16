from __future__ import annotations

from pathlib import Path

import pytest

from docpull.contracts import WorkflowResult
from docpull.models.events import EventType, FetchEvent, FetchStats, SkipReason
from docpull.workflows import create_workflow_request, run_workflow


class ContractFetcher:
    events: list[FetchEvent] = []
    stats = FetchStats()

    def __init__(self, _config: object) -> None:
        self.stats = self.__class__.stats

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def run(self):
        for event in self.events:
            yield event


def test_empty_crawl_always_emits_structured_run_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ContractFetcher.events = [
        FetchEvent(
            type=EventType.FETCH_SKIPPED,
            url="https://example.test/",
            message="Robots disallowed",
            skip_reason=SkipReason.ROBOTS_DISALLOWED,
        )
    ]
    ContractFetcher.stats = FetchStats(urls_discovered=1, pages_skipped=1)
    monkeypatch.setattr("docpull.acquisition_workflows.Fetcher", ContractFetcher)
    output = tmp_path / "crawl"
    request = create_workflow_request("crawl", "https://example.test/", output_dir=output)

    payload = run_workflow(request)
    result = WorkflowResult.model_validate(payload)

    assert result.status == "failed"
    assert result.summary["current_run_record_count"] == 0
    assert result.run_identity["current_run_manifest"] == "current-run.manifest.json"
    assert (output / "current-run.manifest.json").exists()
    assert result.failures[0].code == "robots_disallowed"
    assert result.failures[0].retryable is False


def test_partial_crawl_populates_typed_retryable_http_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ContractFetcher.events = [
        FetchEvent(
            type=EventType.FETCH_FAILED,
            url="https://example.test/rate-limited",
            error="HTTP 429",
            status_code=429,
            attempts=4,
            retry_after_seconds=60,
            retryable=True,
            failure_code="http_429",
            failure_stage="fetch",
        )
    ]
    ContractFetcher.stats = FetchStats(
        urls_discovered=2,
        pages_fetched=1,
        pages_failed=1,
        files_saved=1,
    )
    monkeypatch.setattr("docpull.acquisition_workflows.Fetcher", ContractFetcher)
    output = tmp_path / "partial"
    request = create_workflow_request("crawl", "https://example.test/", output_dir=output)

    result = WorkflowResult.model_validate(run_workflow(request))

    assert result.status == "completed_with_warnings"
    assert result.data["partial_success"] is True
    failure = result.failures[0]
    assert failure.model_dump(exclude_none=True) | {} == {
        "code": "http_429",
        "message": "HTTP 429",
        "stage": "fetch",
        "retryable": True,
        "source_url": "https://example.test/rate-limited",
        "http_status": 429,
        "attempts": 4,
        "retry_after_seconds": 60.0,
        "metadata": {},
    }
