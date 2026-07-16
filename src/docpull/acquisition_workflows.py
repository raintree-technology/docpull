"""WorkflowRequest/WorkflowResult adapters for core fetch and crawl operations."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from .contracts import (
    ArtifactManifest,
    BudgetUsage,
    HashDigest,
    ReplayConfiguration,
    WorkflowFailure,
    WorkflowProgressEvent,
    WorkflowRequest,
    WorkflowResult,
    WorkflowWarning,
    artifact_entries,
    build_workflow_request,
    canonical_sha256,
    file_sha256,
    new_progress_event,
    stable_id,
    workflow_failure_from_mapping,
)
from .core.fetcher import Fetcher
from .models.config import BudgetConfig, CrawlConfig, DocpullConfig, NetworkConfig, OutputConfig, ProfileName
from .models.events import EventType, FetchEvent, FetchStats, SkipReason
from .pipeline.base import PageContext
from .time_utils import utc_now_iso


def execute_acquisition_workflow(request: WorkflowRequest, *, crawl: bool) -> dict[str, Any]:
    """Execute acquisition synchronously and always materialize a structured result."""

    return asyncio.run(_execute_acquisition_workflow(request, crawl=crawl))


async def _execute_acquisition_workflow(
    request: WorkflowRequest,
    *,
    crawl: bool,
) -> dict[str, Any]:
    output_dir = _output_dir(request)
    output_dir.mkdir(parents=True, exist_ok=True)
    value = _input_url(request)
    options = request.options
    try:
        profile = ProfileName(str(options.get("profile") or "rag"))
    except ValueError as err:
        raise ValueError(f"Unsupported acquisition profile: {options.get('profile')}") from err
    config = DocpullConfig(
        url=value,
        profile=profile,
        output=OutputConfig(
            directory=output_dir,
            format=str(options.get("format") or "ndjson"),  # type: ignore[arg-type]
            emit_chunks=bool(options.get("emit_chunks", False)),
        ),
        crawl=CrawlConfig(
            max_pages=int(options.get("max_pages") or (50 if crawl else 1)),
            max_depth=int(options.get("max_depth") or (3 if crawl else 1)),
        ),
        network=NetworkConfig(max_retries=int(options.get("max_retries", 3))),
        budget=BudgetConfig(maximum_paid_cost_usd=0),
    )
    started_at = utc_now_iso()
    events: list[FetchEvent] = []
    failures: list[WorkflowFailure] = []
    warnings: list[WorkflowWarning] = []
    try:
        async with Fetcher(config) as fetcher:
            if crawl:
                async for event in fetcher.run():
                    events.append(event)
                    failure = _failure_from_event(event, default_attempts=config.network.max_retries + 1)
                    if failure is not None:
                        failures.append(failure)
                    warning = _warning_from_event(event)
                    if warning is not None:
                        warnings.append(warning)
            else:
                context = await fetcher.fetch_one(value)
                failure = _failure_from_context(
                    context,
                    default_attempts=config.network.max_retries + 1,
                )
                if failure is not None:
                    failures.append(failure)
                warning = _warning_from_context(context)
                if warning is not None:
                    warnings.append(warning)
            stats = fetcher.stats
    except Exception as err:  # noqa: BLE001
        stats = FetchStats(pages_failed=1)
        failures.append(
            workflow_failure_from_mapping(
                {
                    "code": "workflow_error",
                    "stage": "workflow",
                    "error": str(err),
                    "retryable": False,
                    "url": value,
                }
            )
        )

    return write_acquisition_contracts(
        request=request,
        workflow="crawl" if crawl else "fetch",
        output_dir=output_dir,
        started_at=started_at,
        stats=stats,
        events=events,
        failures=failures,
        warnings=warnings,
        replay_configuration=request.replay,
    )


def write_acquisition_contracts(
    *,
    request: WorkflowRequest,
    workflow: str,
    output_dir: Path,
    started_at: str,
    stats: object,
    events: list[FetchEvent],
    failures: list[WorkflowFailure],
    warnings: list[WorkflowWarning],
    replay_configuration: ReplayConfiguration,
) -> dict[str, Any]:
    """Write run-scoped acquisition contracts without consulting stale output."""

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    finished_at = utc_now_iso()
    request_path = output_dir / "workflow.request.json"
    request_path.write_text(
        json.dumps(request.model_dump(mode="json", exclude_none=True), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    current_record_count = int(getattr(stats, "pages_fetched", 0))
    run_id = stable_id(
        "run",
        {
            "request_id": request.request_id,
            "workflow": workflow,
            "started_at": started_at,
        },
    )
    current_artifacts = _current_acquisition_artifacts(
        output_dir,
        started_at=started_at,
        include_records=current_record_count > 0,
    )
    current_run_manifest = {
        "contract_version": "acquisition.run.v1",
        "schema_version": 1,
        "run_id": run_id,
        "request_id": request.request_id,
        "workflow": workflow,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": _result_status(current_record_count, failures, warnings),
        "current_run_record_count": current_record_count,
        "stats": _stats_payload(stats),
        "artifacts": current_artifacts,
        "failure_count": len(failures),
        "warning_count": len(warnings),
    }
    run_manifest_path = output_dir / "current-run.manifest.json"
    run_manifest_path.write_text(
        json.dumps(current_run_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    artifact_map = {
        "workflow_request": "workflow.request.json",
        "current_run_manifest": "current-run.manifest.json",
        **{item["name"]: item["path"] for item in current_artifacts if item.get("name") and item.get("path")},
    }
    entries = artifact_entries(output_dir, artifact_map)
    aggregate = canonical_sha256([entry.model_dump(mode="json") for entry in entries])
    pack_id = stable_id("pack", {"workflow": workflow, "run_id": run_id, "aggregate": aggregate})
    manifest = ArtifactManifest(
        pack_id=pack_id,
        run_id=run_id,
        entries=entries,
        aggregate_sha256=aggregate,
    )
    artifact_manifest_path = output_dir / "artifact.manifest.json"
    artifact_manifest_path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    progress_events = _progress_events(
        events, started_at=started_at, finished_at=finished_at, workflow=workflow
    )
    result = WorkflowResult(
        request_id=request.request_id,
        workflow=workflow,
        status=cast(
            Literal["completed", "completed_with_warnings", "failed", "cancelled"],
            _result_status(current_record_count, failures, warnings),
        ),
        started_at=started_at,
        finished_at=finished_at,
        pack_identity={"pack_id": pack_id, "aggregate_sha256": aggregate, "workflow": workflow},
        run_identity={
            "run_id": run_id,
            "request_id": request.request_id,
            "scheduler": None,
            "current_run_manifest": "current-run.manifest.json",
        },
        summary={
            **_stats_payload(stats),
            "current_run_record_count": current_record_count,
            "usable_output": current_record_count > 0,
        },
        data={
            "current_run_manifest": current_run_manifest,
            "partial_success": current_record_count > 0 and bool(failures),
        },
        progress_events=progress_events,
        warnings=warnings,
        failures=_dedupe_failures(failures),
        budget_usage=BudgetUsage(
            limit_usd=0,
            estimated_usd=0,
            paid_request_count=0,
            http_request_count=int(getattr(stats, "pages_fetched", 0))
            + int(getattr(stats, "pages_failed", 0))
            + int(getattr(stats, "pages_skipped", 0)),
        ),
        hashes={
            "request": HashDigest(digest=file_sha256(request_path)),
            "artifact_manifest": HashDigest(digest=file_sha256(artifact_manifest_path)),
            "current_run_manifest": HashDigest(digest=file_sha256(run_manifest_path)),
            "pack": HashDigest(digest=aggregate),
        },
        replay_configuration=replay_configuration,
        compatibility_artifacts={
            key: value for key, value in artifact_map.items() if key != "workflow_request"
        },
    )
    result_path = output_dir / "workflow.result.json"
    payload = result.model_dump(mode="json", exclude_none=True)
    result_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def _failure_from_context(context: PageContext, *, default_attempts: int) -> WorkflowFailure | None:
    error = getattr(context, "error", None)
    should_skip = bool(getattr(context, "should_skip", False))
    skip_code = getattr(context, "skip_code", None)
    if not error and not (
        should_skip
        and skip_code
        in {
            SkipReason.HTTP_ERROR,
            SkipReason.ROBOTS_DISALLOWED,
            SkipReason.URL_VALIDATION_FAILED,
            SkipReason.INVALID_CONTENT_TYPE,
            SkipReason.NO_CONTENT_EXTRACTED,
            SkipReason.NO_CONTENT_TO_SAVE,
        }
    ):
        return None
    return workflow_failure_from_mapping(
        {
            "url": getattr(context, "url", None),
            "error": error or getattr(context, "skip_reason", None) or "Acquisition failed",
            "code": (
                f"http_{getattr(context, 'status_code', None)}"
                if getattr(context, "status_code", None)
                else (skip_code.value if skip_code else "fetch_error")
            ),
            "stage": "fetch",
            "http_status": getattr(context, "status_code", None),
            "attempts": getattr(context, "http_attempts", None) or default_attempts,
            "retry_after_seconds": getattr(context, "retry_after_seconds", None),
        },
        default_stage="fetch",
        default_attempts=default_attempts,
    )


def _failure_from_event(event: FetchEvent, *, default_attempts: int) -> WorkflowFailure | None:
    event_type = getattr(event, "type", None)
    skip_reason = getattr(event, "skip_reason", None)
    status_code = getattr(event, "status_code", None)
    is_failure_skip = event_type == EventType.FETCH_SKIPPED and skip_reason in {
        SkipReason.HTTP_ERROR,
        SkipReason.ROBOTS_DISALLOWED,
        SkipReason.URL_VALIDATION_FAILED,
        SkipReason.INVALID_CONTENT_TYPE,
        SkipReason.NO_CONTENT_EXTRACTED,
        SkipReason.NO_CONTENT_TO_SAVE,
    }
    if event_type != EventType.FETCH_FAILED and not is_failure_skip:
        return None
    return workflow_failure_from_mapping(
        {
            "url": getattr(event, "url", None),
            "error": getattr(event, "error", None) or getattr(event, "message", None) or "Acquisition failed",
            "code": getattr(event, "failure_code", None)
            or (f"http_{status_code}" if status_code else None)
            or (skip_reason.value if skip_reason else "fetch_error"),
            "stage": getattr(event, "failure_stage", None) or "fetch",
            "retryable": getattr(event, "retryable", None),
            "http_status": status_code,
            "attempts": getattr(event, "attempts", None) or default_attempts,
            "retry_after_seconds": getattr(event, "retry_after_seconds", None),
        },
        default_stage="fetch",
        default_attempts=default_attempts,
    )


def _warning_from_context(context: PageContext) -> WorkflowWarning | None:
    skip_code = getattr(context, "skip_code", None)
    if not getattr(context, "should_skip", False) or skip_code in {
        SkipReason.HTTP_ERROR,
        SkipReason.ROBOTS_DISALLOWED,
        SkipReason.URL_VALIDATION_FAILED,
        SkipReason.INVALID_CONTENT_TYPE,
        SkipReason.NO_CONTENT_EXTRACTED,
        SkipReason.NO_CONTENT_TO_SAVE,
    }:
        return None
    return WorkflowWarning(
        code=skip_code.value if skip_code else "fetch_skipped",
        message=getattr(context, "skip_reason", None) or "Source was skipped",
        metadata={"url": getattr(context, "url", None)},
    )


def _warning_from_event(event: FetchEvent) -> WorkflowWarning | None:
    skip_reason = getattr(event, "skip_reason", None)
    if getattr(event, "type", None) != EventType.FETCH_SKIPPED or skip_reason in {
        SkipReason.HTTP_ERROR,
        SkipReason.ROBOTS_DISALLOWED,
        SkipReason.URL_VALIDATION_FAILED,
        SkipReason.INVALID_CONTENT_TYPE,
        SkipReason.NO_CONTENT_EXTRACTED,
        SkipReason.NO_CONTENT_TO_SAVE,
    }:
        return None
    return WorkflowWarning(
        code=skip_reason.value if skip_reason else "fetch_skipped",
        message=getattr(event, "message", None) or "Source was skipped",
        metadata={"url": getattr(event, "url", None)},
    )


def _current_acquisition_artifacts(
    output_dir: Path,
    *,
    started_at: str,
    include_records: bool,
) -> list[dict[str, Any]]:
    if not include_records:
        return []

    started_epoch = datetime.fromisoformat(started_at.replace("Z", "+00:00")).timestamp()
    rows: list[dict[str, Any]] = []
    for name, filename in (
        ("documents_ndjson", "documents.ndjson"),
        ("corpus_manifest", "corpus.manifest.json"),
        ("sources", "sources.md"),
        ("acquisition_routes", "acquisition.routes.json"),
        ("rights_manifest", "rights.manifest.json"),
        ("provenance_graph", "provenance.graph.json"),
    ):
        path = output_dir / filename
        if not path.exists() or not path.is_file() or path.stat().st_mtime < started_epoch:
            continue
        rows.append(
            {
                "name": name,
                "path": filename,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    return rows


def _result_status(
    current_record_count: int,
    failures: list[WorkflowFailure],
    warnings: list[WorkflowWarning],
) -> str:
    if current_record_count == 0:
        return "failed"
    if failures or warnings:
        return "completed_with_warnings"
    return "completed"


def _stats_payload(stats: object) -> dict[str, Any]:
    if hasattr(stats, "to_dict"):
        payload = stats.to_dict()
        if isinstance(payload, dict):
            return payload
    return {
        key: getattr(stats, key, 0)
        for key in (
            "urls_discovered",
            "pages_fetched",
            "pages_skipped",
            "pages_failed",
            "files_saved",
            "bytes_downloaded",
            "duration_seconds",
        )
    }


def _progress_events(
    events: list[FetchEvent],
    *,
    started_at: str,
    finished_at: str,
    workflow: str,
) -> list[WorkflowProgressEvent]:
    rows = [
        new_progress_event(
            phase="run",
            status="started",
            timestamp=started_at,
            message=f"Started {workflow}",
        )
    ]
    for event in events:
        event_type = getattr(event, "type", None)
        if event_type not in {
            EventType.DISCOVERY_STARTED,
            EventType.DISCOVERY_COMPLETE,
            EventType.FETCH_PROGRESS,
            EventType.FETCH_FAILED,
            EventType.FETCH_SKIPPED,
        }:
            continue
        rows.append(
            new_progress_event(
                phase="discovery" if "discovery" in event_type.value else "fetch",
                status="failed"
                if event_type == EventType.FETCH_FAILED
                else "warning"
                if event_type == EventType.FETCH_SKIPPED
                else "progress",
                timestamp=(event.timestamp.isoformat() if getattr(event, "timestamp", None) else None),
                message=getattr(event, "message", None) or getattr(event, "error", None),
                current=getattr(event, "current", None),
                total=getattr(event, "total", None),
                metadata={"url": event.url} if getattr(event, "url", None) else {},
            )
        )
    rows.append(
        new_progress_event(
            phase="run",
            status="completed",
            timestamp=finished_at,
            message=f"Completed {workflow}",
        )
    )
    return [WorkflowProgressEvent.model_validate(item) for item in rows]


def _dedupe_failures(failures: list[WorkflowFailure]) -> list[WorkflowFailure]:
    rows: dict[str, WorkflowFailure] = {}
    for failure in failures:
        key = canonical_sha256(failure.model_dump(mode="json", exclude_none=True))
        rows[key] = failure
    return [rows[key] for key in sorted(rows)]


def _input_url(request: WorkflowRequest) -> str:
    for key in ("url", "value"):
        value = request.input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("Acquisition WorkflowRequest.input must include url or value")


def _output_dir(request: WorkflowRequest) -> Path:
    value = request.output.get("directory")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Acquisition WorkflowRequest.output.directory is required")
    return Path(value).expanduser().resolve()


def write_cli_acquisition_contracts(
    *,
    config: DocpullConfig,
    workflow: str,
    started_at: str,
    stats: object,
    events: list[FetchEvent] | None = None,
    contexts: list[PageContext] | None = None,
    extra_failures: list[WorkflowFailure] | None = None,
) -> dict[str, Any]:
    """Bridge the compatibility CLI onto the canonical acquisition contracts."""

    options = {
        "profile": config.profile.value,
        "format": config.output.format,
        "max_pages": config.crawl.max_pages,
        "max_depth": config.crawl.max_depth,
        "max_retries": config.network.max_retries,
        "emit_chunks": config.output.emit_chunks,
    }
    request = build_workflow_request(
        workflow=workflow,
        input_payload={"url": config.url},
        output_dir=config.output.directory,
        options=options,
        source_policy={
            "robots": "respect",
            "allowed_schemes": ["https"],
            "local_first": True,
        },
        budget={"maximum_paid_cost_usd": config.budget.maximum_paid_cost_usd},
        browser_enabled=config.render.enabled,
        paid_routes_enabled=config.render.enabled
        and config.render.backend in {"vercel-sandbox", "e2b-sandbox"},
    )
    event_rows = list(events or [])
    failures = list(extra_failures or [])
    warnings: list[WorkflowWarning] = []
    for event in event_rows:
        failure = _failure_from_event(event, default_attempts=config.network.max_retries + 1)
        if failure is not None:
            failures.append(failure)
        warning = _warning_from_event(event)
        if warning is not None:
            warnings.append(warning)
    for context in contexts or []:
        failure = _failure_from_context(context, default_attempts=config.network.max_retries + 1)
        if failure is not None:
            failures.append(failure)
        warning = _warning_from_context(context)
        if warning is not None:
            warnings.append(warning)
    return write_acquisition_contracts(
        request=request,
        workflow=workflow,
        output_dir=config.output.directory,
        started_at=started_at,
        stats=stats,
        events=event_rows,
        failures=failures,
        warnings=warnings,
        replay_configuration=request.replay,
    )


__all__ = [
    "execute_acquisition_workflow",
    "write_acquisition_contracts",
    "write_cli_acquisition_contracts",
]
