"""Provider-neutral local parity workflows.

These helpers give DocPull competitor-shaped workflows without pretending that a
local CLI has a hosted search index, scheduler, webhook receiver, or research
agent. Each workflow writes durable artifacts that mirror the hosted API shape:
result JSON, Markdown report, lifecycle events, status, polling report, and a
sample webhook payload.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from .core.fetcher import Fetcher
from .discovery.contracts import (
    CandidateSourceRecord,
    read_candidate_records,
    records_from_sitemap_file,
    records_from_url_file,
    select_candidate_records,
    write_discovery_pack,
    write_selected_sources,
)
from .local_workflows import answer_pack
from .models.config import DocpullConfig, ProfileName
from .models.document import DocumentRecord
from .models.run import RunIdentity
from .pack_tools import (
    _artifact_ref,
    _brief_markdown,
    _entities_markdown,
    _write_json,
    build_citation_map,
    build_research_brief,
    extract_pack_entities,
)
from .policy import PolicyConfig
from .time_utils import utc_now_iso

PARITY_SCHEMA_VERSION = 1
DEFAULT_EXTRACT_OUTPUT_DIR = Path("packs/extract-pack")
DEFAULT_MAP_OUTPUT_DIR = Path("packs/map")
DEFAULT_CRAWL_OUTPUT_DIR = Path("packs/crawl-pack")


class ParityWorkflowError(RuntimeError):
    """User-facing provider-neutral workflow error."""


def extract_pack(
    url_file: Path,
    *,
    output_dir: Path = DEFAULT_EXTRACT_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    query: str | None = None,
    objective: str | None = None,
    max_results: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Extract known URLs into a local pack with hosted-provider-shaped artifacts."""
    policy = policy or PolicyConfig()
    records = records_from_url_file(
        url_file,
        query=query,
        expected_domains=policy.allowed_domains,
        source="extract-pack-url-file",
    )
    selected, skipped = _apply_policy_and_limit(records, policy=policy, max_results=max_results)
    urls = [record.url for record in selected]
    output_dir = output_dir.resolve()
    started_at = utc_now_iso()
    run_id = _new_run_id("extract")

    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        result = _workflow_result(
            workflow="extract-pack",
            run_id=run_id,
            output_dir=output_dir,
            started_at=started_at,
            status="dry_run",
            summary={
                "planned_url_count": len(urls),
                "skipped_count": len(skipped),
                "record_count": 0,
            },
            artifacts={},
            limits=[
                "Dry run did not fetch pages.",
                "Local extraction reads selected URLs only; it does not search a hosted web index.",
            ],
            extra={"planned_urls": urls, "skipped": skipped},
        )
        result_path = output_dir / "extract.result.json"
        report_path = output_dir / "EXTRACT_REPORT.md"
        result["artifacts"] = _result_artifacts(output_dir, result_path, report_path)
        _write_json(result_path, result)
        report_path.write_text(_generic_report_markdown(result), encoding="utf-8")
        _write_lifecycle_artifacts(output_dir, result, started_at=started_at)
        return result

    fetched = asyncio.run(_fetch_urls_to_local_pack(urls, output_dir, policy=policy, workflow="extract-pack"))
    result = _workflow_result(
        workflow="extract-pack",
        run_id=run_id,
        output_dir=output_dir,
        started_at=started_at,
        status="completed" if not fetched["errors"] else "completed_with_errors",
        summary={
            "planned_url_count": len(urls),
            "record_count": fetched["record_count"],
            "failed_count": len(fetched["errors"]),
            "skipped_count": len(skipped) + len(fetched["skips"]),
        },
        artifacts={
            "documents_ndjson": "documents.ndjson",
            "corpus_manifest": "corpus.manifest.json",
            "sources": "sources.md",
            "pack": "local.pack.json",
            "source_policy": "source_policy.json",
        },
        limits=[
            "Local extraction fetches known URLs only.",
            "JS-heavy pages require --render/agent-browser in the lower-level fetch path.",
        ],
        extra={"errors": fetched["errors"], "skipped": skipped + fetched["skips"]},
    )
    result_path = output_dir / "extract.result.json"
    report_path = output_dir / "EXTRACT_REPORT.md"
    result["artifacts"] = {**result["artifacts"], **_result_artifacts(output_dir, result_path, report_path)}
    _write_json(result_path, result)
    report_path.write_text(_generic_report_markdown(result), encoding="utf-8")
    _write_lifecycle_artifacts(output_dir, result, started_at=started_at)
    return result


def map_sources(
    input_path: Path,
    *,
    source_type: str,
    output_dir: Path = DEFAULT_MAP_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    query: str | None = None,
    objective: str | None = None,
    base_url: str | None = None,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Create a URL-only provider-neutral map/discovery pack."""
    policy = policy or PolicyConfig()
    if source_type == "urls":
        records = records_from_url_file(
            input_path,
            query=query,
            expected_domains=policy.allowed_domains,
            source="map-url-file",
        )
        source_label = "local-map-url-file"
    elif source_type == "sitemap":
        records = records_from_sitemap_file(
            input_path,
            base_url=base_url,
            query=query,
            expected_domains=policy.allowed_domains,
        )
        source_label = "local-map-sitemap"
    else:
        raise ParityWorkflowError("source_type must be 'urls' or 'sitemap'.")

    started_at = utc_now_iso()
    report = write_discovery_pack(
        output_dir,
        records,
        policy=policy,
        objective=objective,
        query=query,
        source=source_label,
        source_path=input_path,
        max_results=max_results,
    )
    result = _workflow_result(
        workflow="map",
        run_id=_new_run_id("map"),
        output_dir=output_dir.resolve(),
        started_at=started_at,
        status="completed",
        summary={
            "candidate_count": report["candidate_count"],
            "skipped_count": report["skipped_count"],
            "source_type": source_type,
        },
        artifacts=report["artifacts"],
        limits=[
            "Local map creates deterministic URL candidates from files or sitemaps.",
            "Natural-language crawl instructions and hosted ranking require a provider-backed workflow.",
        ],
        extra={"discovery": report},
    )
    result_path = output_dir / "map.result.json"
    report_path = output_dir / "MAP_REPORT.md"
    result["artifacts"] = {**result["artifacts"], **_result_artifacts(output_dir, result_path, report_path)}
    _write_json(result_path, result)
    report_path.write_text(_generic_report_markdown(result), encoding="utf-8")
    _write_lifecycle_artifacts(output_dir, result, started_at=started_at)
    return result


def crawl_pack(
    input_path: Path,
    *,
    output_dir: Path = DEFAULT_CRAWL_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    selectors: list[str] | None = None,
    manual_file: Path | None = None,
    max_results: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Select mapped candidates and fetch them into a local crawl pack."""
    policy = policy or PolicyConfig()
    candidates = _candidate_records_from_path(input_path, policy=policy)
    selected = select_candidate_records(candidates, selectors or ["top:10"], manual_file=manual_file)
    selected, skipped = _apply_policy_and_limit(selected, policy=policy, max_results=max_results)
    output_dir = output_dir.resolve()
    started_at = utc_now_iso()
    selection_report = write_selected_sources(
        output_dir,
        selected,
        source_pack=input_path,
        policy=policy,
    )
    urls = [record.url for record in selected]

    if dry_run:
        result = _workflow_result(
            workflow="crawl-pack",
            run_id=_new_run_id("crawl"),
            output_dir=output_dir,
            started_at=started_at,
            status="dry_run",
            summary={
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "planned_url_count": len(urls),
                "record_count": 0,
                "skipped_count": len(skipped),
            },
            artifacts=selection_report["artifacts"],
            limits=["Dry run selected sources but did not fetch pages."],
            extra={"selected_urls": urls, "skipped": skipped},
        )
    else:
        fetched = asyncio.run(
            _fetch_urls_to_local_pack(urls, output_dir, policy=policy, workflow="crawl-pack")
        )
        result = _workflow_result(
            workflow="crawl-pack",
            run_id=_new_run_id("crawl"),
            output_dir=output_dir,
            started_at=started_at,
            status="completed" if not fetched["errors"] else "completed_with_errors",
            summary={
                "candidate_count": len(candidates),
                "selected_count": len(selected),
                "planned_url_count": len(urls),
                "record_count": fetched["record_count"],
                "failed_count": len(fetched["errors"]),
                "skipped_count": len(skipped) + len(fetched["skips"]),
            },
            artifacts={
                **selection_report["artifacts"],
                "documents_ndjson": "documents.ndjson",
                "corpus_manifest": "corpus.manifest.json",
                "sources": "sources.md",
                "pack": "local.pack.json",
                "source_policy": "source_policy.json",
            },
            limits=[
                "Local crawl-pack fetches selected URL candidates.",
                "It does not run a hosted crawler or natural-language browsing agent.",
            ],
            extra={"errors": fetched["errors"], "skipped": skipped + fetched["skips"]},
        )

    result_path = output_dir / "crawl.result.json"
    report_path = output_dir / "CRAWL_REPORT.md"
    result["artifacts"] = {**result["artifacts"], **_result_artifacts(output_dir, result_path, report_path)}
    _write_json(result_path, result)
    report_path.write_text(_generic_report_markdown(result), encoding="utf-8")
    _write_lifecycle_artifacts(output_dir, result, started_at=started_at)
    return result


def research_pack(
    pack_dir: Path,
    *,
    objective: str,
    output_dir: Path | None = None,
    schema_path: Path | None = None,
    required_domains: list[str] | None = None,
    max_excerpts: int = 8,
    entity_limit: int = 20,
) -> dict[str, Any]:
    """Write a local research-pack result with citations, basis, lifecycle, and schema validation."""
    if not objective.strip():
        raise ParityWorkflowError("objective must be non-empty.")
    pack_dir = pack_dir.resolve()
    output_dir = (output_dir or pack_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now_iso()
    answer = answer_pack(
        pack_dir,
        objective,
        limit=max(max_excerpts, 1),
        required_domains=required_domains,
        json_path=output_dir / "answer.result.json",
        markdown_path=output_dir / "answer.report.md",
    )
    brief = build_research_brief(
        pack_dir,
        objective=objective,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
    )
    citations = answer.get("answer", {}).get("citations", [])
    structured = _build_structured_output(
        schema_path=schema_path,
        objective=objective,
        answer=answer,
        brief=brief,
        citations=citations if isinstance(citations, list) else [],
    )
    status = str(answer.get("answer", {}).get("status") or "answered_from_local_pack")
    result = _workflow_result(
        workflow="research-pack",
        run_id=_new_run_id("research"),
        output_dir=output_dir,
        started_at=started_at,
        status=status,
        summary={
            "source_count": brief["summary"]["source_count"],
            "record_count": brief["summary"]["record_count"],
            "citation_count": len(citations) if isinstance(citations, list) else 0,
            "basis_count": len(brief.get("key_excerpts", [])),
            "structured_output_valid": structured["validation"]["valid"],
        },
        artifacts={
            "answer_json": "answer.result.json",
            "answer_markdown": "answer.report.md",
            "brief_json": "research.brief.json",
            "brief_markdown": "RESEARCH_BRIEF.md",
        },
        limits=[
            "Local research-pack answers only from the supplied pack.",
            "It refuses or marks insufficient evidence when local citations do not support an answer.",
            "Web-scale multi-hop research requires a provider-backed workflow.",
        ],
        extra={
            "objective": objective,
            "answer": answer.get("answer"),
            "citations": citations,
            "basis": brief.get("key_excerpts", []),
            "structured_output": structured,
        },
    )
    brief_path = output_dir / "research.brief.json"
    brief_md_path = output_dir / "RESEARCH_BRIEF.md"
    result_path = output_dir / "research.result.json"
    report_path = output_dir / "research.report.md"
    _write_json(brief_path, brief)
    brief_md_path.write_text(_brief_markdown(brief), encoding="utf-8")
    result["artifacts"] = {**result["artifacts"], **_result_artifacts(output_dir, result_path, report_path)}
    _write_json(result_path, result)
    report_path.write_text(_research_report_markdown(result), encoding="utf-8")
    _write_lifecycle_artifacts(output_dir, result, started_at=started_at)
    return result


def entities_pack(
    pack_dir: Path,
    *,
    output_dir: Path | None = None,
    required_domains: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Write a provider-neutral entity/list-building pack from local evidence."""
    pack_dir = pack_dir.resolve()
    output_dir = (output_dir or pack_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started_at = utc_now_iso()
    entities = extract_pack_entities(pack_dir, required_domains=required_domains, limit=limit)
    citations = build_citation_map(pack_dir, required_domains=required_domains)
    result = _workflow_result(
        workflow="entities-pack",
        run_id=_new_run_id("entities"),
        output_dir=output_dir,
        started_at=started_at,
        status="completed",
        summary={
            "entity_count": entities["entity_count"],
            "record_count": entities["record_count"],
            "source_count": entities["source_count"],
            "citation_count": citations["source_count"],
        },
        artifacts={
            "entities_json": "entities.result.json",
            "entities_markdown": "ENTITIES.md",
            "citations": "citations.json",
        },
        limits=[
            "Local entities-pack extracts entities from existing pack content.",
            "It does not discover verified people or company datasets at web scale.",
        ],
        extra={"entities": entities, "citations": citations},
    )
    entities_path = output_dir / "entities.result.json"
    entities_md_path = output_dir / "ENTITIES.md"
    citations_path = output_dir / "citations.json"
    result_path = output_dir / "entities-pack.result.json"
    report_path = output_dir / "ENTITIES_PACK.md"
    _write_json(entities_path, entities)
    entities_md_path.write_text(_entities_markdown(entities), encoding="utf-8")
    _write_json(citations_path, citations)
    result["artifacts"] = {**result["artifacts"], **_result_artifacts(output_dir, result_path, report_path)}
    _write_json(result_path, result)
    report_path.write_text(_generic_report_markdown(result), encoding="utf-8")
    _write_lifecycle_artifacts(output_dir, result, started_at=started_at)
    return result


def load_output_schema(path: Path) -> dict[str, Any]:
    """Load a JSON Schema file used for local structured-output validation."""
    if not path.exists():
        raise ParityWorkflowError(f"Output schema does not exist: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ParityWorkflowError(f"Output schema is not valid JSON: {err}") from err
    if not isinstance(data, dict):
        raise ParityWorkflowError("Output schema must be a JSON object.")
    return data


def validate_structured_output(payload: Any, schema: dict[str, Any]) -> dict[str, Any]:
    """Validate a small, dependency-free JSON Schema subset."""
    errors: list[str] = []
    _validate_schema_node(payload, schema, "$", errors)
    return {
        "schema_version": PARITY_SCHEMA_VERSION,
        "valid": not errors,
        "errors": errors,
        "supported_subset": [
            "type",
            "required",
            "properties",
            "additionalProperties",
            "items",
            "enum",
            "minimum",
            "maximum",
        ],
    }


async def _fetch_urls_to_local_pack(
    urls: list[str],
    output_dir: Path,
    *,
    policy: PolicyConfig,
    workflow: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[DocumentRecord] = []
    source_entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    if not urls:
        _write_local_pack(output_dir, records, source_entries, policy=policy, workflow=workflow)
        return {"record_count": 0, "errors": errors, "skips": skips}

    run_identity = RunIdentity.from_config(DocpullConfig(url=urls[0], profile=ProfileName.CUSTOM))
    async with Fetcher(DocpullConfig(url=urls[0], profile=ProfileName.CUSTOM)) as fetcher:
        for index, url in enumerate(urls, start=1):
            allowed, reason = policy.allows_url(url)
            if not allowed:
                skips.append({"url": url, "reason": reason or "policy_denied"})
                continue
            ctx = await fetcher.fetch_one(url, save=False)
            if ctx.error:
                errors.append({"url": url, "error": ctx.error})
                continue
            if ctx.should_skip:
                skips.append({"url": url, "reason": str(ctx.skip_reason or "skipped")})
                continue
            content = str(ctx.markdown or "")
            if not content.strip():
                skips.append({"url": url, "reason": "empty_content"})
                continue
            record = DocumentRecord.from_page(
                url=url,
                title=ctx.title,
                content=content,
                metadata=ctx.metadata,
                extraction=ctx.extraction_info,
                source_type=ctx.source_type or workflow,
                run_identity=run_identity,
            )
            records.append(record)
            source_path = output_dir / "sources" / f"{index:03d}.md"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(content, encoding="utf-8")
            source_entries.append(
                {
                    "index": len(source_entries) + 1,
                    "url": url,
                    "title": ctx.title or url,
                    "path": _artifact_ref(output_dir, source_path),
                }
            )

    _write_local_pack(output_dir, records, source_entries, policy=policy, workflow=workflow)
    return {"record_count": len(records), "errors": errors, "skips": skips}


def _write_local_pack(
    output_dir: Path,
    records: list[DocumentRecord],
    sources: list[dict[str, Any]],
    *,
    policy: PolicyConfig,
    workflow: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "documents.ndjson").write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    _write_json(
        output_dir / "corpus.manifest.json",
        {
            "schema_version": 1,
            "generated_at": utc_now_iso(),
            "output_format": "ndjson",
            "document_count": len({record.document_id for record in records}),
            "record_count": len(records),
            "records": [
                {
                    "document_id": record.document_id,
                    "url": record.url,
                    "title": record.title,
                    "content_hash": record.content_hash,
                    "source_type": record.source_type,
                    "output_path": sources[index]["path"] if index < len(sources) else None,
                }
                for index, record in enumerate(records)
            ],
        },
    )
    source_policy = policy.to_source_policy_payload(
        source=workflow,
        url=records[0].url if records else None,
        metadata={"workflow": workflow, "record_count": len(records)},
    )
    _write_json(output_dir / "source_policy.json", source_policy)
    _write_json(
        output_dir / "local.pack.json",
        {
            "schema_version": PARITY_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "provider": "local",
            "workflow": workflow,
            "request_options": {"source_policy": source_policy},
            "record_count": len(records),
            "sources": sources,
            "artifacts": {
                "documents_ndjson": "documents.ndjson",
                "corpus_manifest": "corpus.manifest.json",
                "sources": "sources.md",
                "source_policy": "source_policy.json",
            },
        },
    )
    (output_dir / "sources.md").write_text(_sources_markdown(sources), encoding="utf-8")


def _candidate_records_from_path(path: Path, *, policy: PolicyConfig) -> list[CandidateSourceRecord]:
    if path.is_dir() or path.name == "candidate_sources.ndjson":
        return read_candidate_records(path)
    return records_from_url_file(
        path,
        expected_domains=policy.allowed_domains,
        source="crawl-pack-url-file",
    )


def _apply_policy_and_limit(
    records: list[CandidateSourceRecord],
    *,
    policy: PolicyConfig,
    max_results: int | None,
) -> tuple[list[CandidateSourceRecord], list[dict[str, Any]]]:
    selected: list[CandidateSourceRecord] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if record.url in seen:
            skipped.append({"url": record.url, "reason": "duplicate"})
            continue
        seen.add(record.url)
        allowed, reason = policy.allows_url(record.url)
        if not allowed:
            skipped.append({"url": record.url, "reason": reason})
            continue
        selected.append(record)
        if max_results is not None and len(selected) >= max_results:
            break
    return selected, skipped


def _build_structured_output(
    *,
    schema_path: Path | None,
    objective: str,
    answer: dict[str, Any],
    brief: dict[str, Any],
    citations: list[Any],
) -> dict[str, Any]:
    raw_answer = answer.get("answer")
    answer_data: dict[str, Any] = raw_answer if isinstance(raw_answer, dict) else {}
    answer_text = str(answer_data.get("text") or "")
    base_output = {
        "objective": objective,
        "status": answer_data.get("status"),
        "answer": answer_text,
        "summary": answer_text,
        "report": answer_text,
        "citations": citations,
        "sources": citations,
        "basis": brief.get("key_excerpts", []),
        "generated_at": utc_now_iso(),
    }
    if schema_path is None:
        return {
            "schema_path": None,
            "data": base_output,
            "validation": {"schema_version": PARITY_SCHEMA_VERSION, "valid": True, "errors": []},
        }

    schema = load_output_schema(schema_path)
    data = _shape_output_for_schema(base_output, schema)
    validation = validate_structured_output(data, schema)
    return {
        "schema_path": str(schema_path.resolve()),
        "schema": schema,
        "data": data,
        "validation": validation,
        "mapping_note": (
            "Local structured output is filled only from cited local answer fields. "
            "Unknown required schema fields are left absent and reported as validation errors."
        ),
    }


def _shape_output_for_schema(base_output: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") != "object" or not isinstance(schema.get("properties"), dict):
        return base_output
    shaped: dict[str, Any] = {}
    properties = schema["properties"]
    for key, prop in properties.items():
        if not isinstance(key, str) or not isinstance(prop, dict):
            continue
        mapped_key = _structured_field_alias(key)
        if mapped_key in base_output:
            shaped[key] = base_output[mapped_key]
    return shaped


def _structured_field_alias(key: str) -> str:
    normalized = key.strip().lower().replace("-", "_")
    aliases = {
        "text": "answer",
        "response": "answer",
        "final_answer": "answer",
        "summary": "summary",
        "report": "report",
        "sources": "sources",
        "evidence": "basis",
        "basis": "basis",
        "citations": "citations",
        "status": "status",
        "objective": "objective",
        "generated_at": "generated_at",
    }
    return aliases.get(normalized, normalized)


def _validate_schema_node(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_json_type(value, expected_type):
        errors.append(f"{path}: expected {expected_type}, got {_json_type(value)}")
        return
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: value is not in enum")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: value is below minimum {minimum}")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"{path}: value is above maximum {maximum}")
    if isinstance(value, dict):
        raw_properties = schema.get("properties")
        properties: dict[str, Any] = raw_properties if isinstance(raw_properties, dict) else {}
        raw_required = schema.get("required")
        required: list[Any] = raw_required if isinstance(raw_required, list) else []
        for key in required:
            if isinstance(key, str) and key not in value:
                errors.append(f"{path}.{key}: required property is missing")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: additional property is not allowed")
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                _validate_schema_node(value[key], child_schema, f"{path}.{key}", errors)
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_schema_node(item, item_schema, f"{path}[{index}]", errors)


def _matches_json_type(value: Any, expected_type: Any) -> bool:
    if isinstance(expected_type, list):
        return any(_matches_json_type(value, item) for item in expected_type)
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    return True


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def _workflow_result(
    *,
    workflow: str,
    run_id: str,
    output_dir: Path,
    started_at: str,
    status: str,
    summary: dict[str, Any],
    artifacts: dict[str, Any],
    limits: list[str],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "schema_version": PARITY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "started_at": started_at,
        "completed_at": utc_now_iso(),
        "provider": "local",
        "workflow": workflow,
        "run_id": run_id,
        "status": status,
        "output_dir": str(output_dir),
        "summary": summary,
        "artifacts": artifacts,
        "local_first_limits": limits,
    }
    if extra:
        result.update(extra)
    return result


def _write_lifecycle_artifacts(output_dir: Path, result: dict[str, Any], *, started_at: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    completed_at = str(result.get("completed_at") or utc_now_iso())
    events = [
        {
            "schema_version": PARITY_SCHEMA_VERSION,
            "event": "started",
            "workflow": result["workflow"],
            "run_id": result["run_id"],
            "status": "running",
            "created_at": started_at,
        },
        {
            "schema_version": PARITY_SCHEMA_VERSION,
            "event": "completed" if result["status"] != "dry_run" else "dry_run_completed",
            "workflow": result["workflow"],
            "run_id": result["run_id"],
            "status": result["status"],
            "created_at": completed_at,
            "summary": result.get("summary", {}),
        },
    ]
    events_path = output_dir / "events.ndjson"
    events_path.write_text(
        "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events),
        encoding="utf-8",
    )
    status_payload = {
        "schema_version": PARITY_SCHEMA_VERSION,
        "run_id": result["run_id"],
        "workflow": result["workflow"],
        "provider": "local",
        "status": result["status"],
        "started_at": started_at,
        "completed_at": completed_at,
        "summary": result.get("summary", {}),
        "result_artifacts": result.get("artifacts", {}),
    }
    _write_json(output_dir / "status.json", status_payload)
    _write_json(
        output_dir / "poll.report.json",
        {
            "schema_version": PARITY_SCHEMA_VERSION,
            "run_id": result["run_id"],
            "workflow": result["workflow"],
            "polling": "not_required_for_local_sync_workflow",
            "status": result["status"],
            "poll_count": 1,
            "last_polled_at": completed_at,
        },
    )
    _write_json(
        output_dir / "webhook.sample.json",
        {
            "schema_version": PARITY_SCHEMA_VERSION,
            "event": f"docpull.{result['workflow']}.{result['status']}",
            "created_at": completed_at,
            "data": status_payload,
            "delivery": "sample_only_local_docpull_does_not_host_public_webhook_receivers",
        },
    )


def _result_artifacts(output_dir: Path, result_path: Path, report_path: Path) -> dict[str, str]:
    return {
        "result_json": _artifact_ref(output_dir, result_path),
        "report_markdown": _artifact_ref(output_dir, report_path),
        "events": "events.ndjson",
        "status": "status.json",
        "poll_report": "poll.report.json",
        "webhook_sample": "webhook.sample.json",
    }


def _sources_markdown(sources: list[dict[str, Any]]) -> str:
    lines = ["# Sources", ""]
    for source in sources:
        title = source.get("title") or source.get("url")
        lines.append(f"- {source.get('index')}. [{title}]({source.get('url')})")
    if not sources:
        lines.append("No sources fetched.")
    return "\n".join(lines).rstrip() + "\n"


def _generic_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# {str(payload.get('workflow', 'Workflow')).replace('-', ' ').title()}",
        "",
        f"Status: {payload.get('status')}",
        f"Run ID: `{payload.get('run_id')}`",
        "",
        "## Summary",
        "",
    ]
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key, value in summary.items():
            lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    limits = payload.get("local_first_limits")
    if isinstance(limits, list) and limits:
        lines.extend(["", "## Local Limits", ""])
        for item in limits:
            lines.append(f"- {item}")
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, dict):
        lines.extend(["", "## Artifacts", ""])
        for key, value in artifacts.items():
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).rstrip() + "\n"


def _research_report_markdown(payload: dict[str, Any]) -> str:
    lines = [_generic_report_markdown(payload), "", "## Answer", ""]
    answer = payload.get("answer")
    if isinstance(answer, dict):
        lines.append(str(answer.get("text") or ""))
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        validation = structured.get("validation")
        lines.extend(["", "## Structured Output", ""])
        if isinstance(validation, dict):
            lines.append(f"Valid: {validation.get('valid')}")
            for error in validation.get("errors") or []:
                lines.append(f"- {error}")
    basis = payload.get("basis")
    if isinstance(basis, list) and basis:
        lines.extend(["", "## Basis", ""])
        for item in basis[:10]:
            if isinstance(item, dict):
                lines.append(f"- {item.get('citation_id')}: {item.get('excerpt')}")
    return "\n".join(lines).rstrip() + "\n"


def _new_run_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


__all__ = [
    "PARITY_SCHEMA_VERSION",
    "ParityWorkflowError",
    "extract_pack",
    "map_sources",
    "crawl_pack",
    "research_pack",
    "entities_pack",
    "load_output_schema",
    "validate_structured_output",
]
