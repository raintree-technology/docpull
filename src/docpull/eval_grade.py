"""Eval-grade context pack artifacts and eval generation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .time_utils import utc_now_iso

EVAL_GRADE_SCHEMA_VERSION = 3
DEFAULT_EVAL_TYPES = ("current-context-qa", "version-drift", "citation", "coverage-aware")
LEGACY_EVAL_TYPE_ALIASES = {"current-docs-qa": "current-context-qa"}
_KNOWN_EVAL_TYPES = frozenset((*DEFAULT_EVAL_TYPES, *LEGACY_EVAL_TYPE_ALIASES))
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{2,}")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SENSITIVE_RE = re.compile(
    r"\b(?:api[_ -]?key|secret|password|private[_ -]?key|bearer\s+[A-Za-z0-9._-]+|ssn)\b",
    re.IGNORECASE,
)
_DEPRECATION_RE = re.compile(
    r"\b(?:deprecated|deprecation|sunset|end[- ]of[- ]life|no longer supported|removed|obsolete)\b",
    re.IGNORECASE,
)
_BREAKING_RE = re.compile(
    r"\b(?:breaking change|required|must|migration|migrate|renamed|removed|no longer|incompatible)\b",
    re.IGNORECASE,
)
_FEATURE_RE = re.compile(
    r"\b(?:new|added|introducing|released|now supports|available|feature|supports)\b",
    re.IGNORECASE,
)
_AUTH_SECURITY_RE = re.compile(
    r"\b(?:auth|oauth|token|permission|scope|security|encrypt|secret|signing|cors|csrf|credential)\b",
    re.IGNORECASE,
)
_PRICING_LIMIT_RE = re.compile(
    r"\b(?:pricing|price|billing|quota|rate limit|limit|usage|cost|plan|metered)\b",
    re.IGNORECASE,
)


class EvalGradeError(RuntimeError):
    """User-facing eval-grade artifact error."""


def prepare_eval_grade_pack(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    markdown: bool = True,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write eval-grade trust artifacts for a local context pack."""
    pack_dir = pack_dir.resolve()
    _require_pack(pack_dir)
    records = _read_pack_records(pack_dir)
    pack = _read_pack_metadata(pack_dir)

    from .pack_tools import build_citation_map, score_pack

    citations_payload = build_citation_map(pack_dir, required_domains=required_domains)
    score_payload = score_pack(pack_dir, required_domains=required_domains)
    rights = build_rights_manifest(
        pack_dir,
        records=records,
        pack=pack,
        citations_payload=citations_payload,
    )
    provenance = build_provenance_graph(
        pack_dir,
        records=records,
        pack=pack,
        citations_payload=citations_payload,
    )
    citation_index = build_citation_index(
        pack_dir,
        records=records,
        citations_payload=citations_payload,
    )
    card = build_pack_card(
        pack_dir,
        pack=pack,
        records=records,
        score_payload=score_payload,
        citations_payload=citations_payload,
        rights_payload=rights,
    )

    output_artifacts = artifacts if artifacts is not None else {}
    paths: dict[str, Path] = {
        "rights_manifest": pack_dir / "rights.manifest.json",
        "provenance_graph": pack_dir / "provenance.graph.json",
        "citation_index": pack_dir / "citation.index.json",
    }
    _write_json(paths["rights_manifest"], rights)
    _write_json(paths["provenance_graph"], provenance)
    _write_json(paths["citation_index"], citation_index)
    # PACK_CARD.md is mandatory at eval grade even when optional prepare
    # summaries are requested as JSON-only.
    card_path = pack_dir / "PACK_CARD.md"
    card_path.write_text(card, encoding="utf-8")
    output_artifacts["pack_card"] = _artifact_ref(pack_dir, card_path)
    for key, path in paths.items():
        output_artifacts[key] = _artifact_ref(pack_dir, path)

    return {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "summary": {
            "record_count": len(records),
            "source_count": citations_payload.get("source_count", 0),
            "coverage_confidence": _coverage_confidence(pack_dir),
            "rights_status": rights["summary"]["rights_status"],
            "citation_index_entry_count": citation_index["summary"]["entry_count"],
            "provenance_source_count": provenance["summary"]["source_count"],
        },
        "artifacts": {
            key: output_artifacts[key]
            for key in (
                "rights_manifest",
                "provenance_graph",
                "citation_index",
                "pack_card",
            )
            if key in output_artifacts
        },
    }


def build_rights_manifest(
    pack_dir: Path,
    *,
    records: list[dict[str, Any]] | None = None,
    pack: dict[str, Any] | None = None,
    citations_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = records if records is not None else _read_pack_records(pack_dir)
    pack = pack if pack is not None else _read_pack_metadata(pack_dir)
    citations_payload = citations_payload or {}
    domains = _domains_from_records(records)
    license_hints = sorted(
        {
            str(record.get("license_hint") or "").strip()
            for record in records
            if str(record.get("license_hint") or "").strip()
        }
    )
    primary_url = _primary_url(records)
    pii_risk, sensitive_risk = _privacy_risks(records)
    explicit_allowed = _dict_value(pack.get("allowed_use"))
    record_allowed = _record_allowed_use(records) if not explicit_allowed else {}
    allowed_use = {
        "internal_indexing": _allowed_status(
            explicit_allowed.get("internal_indexing", record_allowed.get("internal_indexing"))
        ),
        "redistribution": _allowed_status(
            explicit_allowed.get("redistribution", record_allowed.get("redistribution"))
        ),
        "model_training": _allowed_status(
            explicit_allowed.get("model_training", record_allowed.get("model_training"))
        ),
        "eval_generation": _allowed_status(
            explicit_allowed.get("eval_generation", record_allowed.get("eval_generation"))
        ),
    }
    rights_status = (
        "permissioned"
        if any(value in {"allowed", "allowed_with_conditions"} for value in allowed_use.values())
        else "unknown"
    )
    obligations = _rights_obligations(records, pack=pack)
    return {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir.resolve()),
        "source_owner": pack.get("source_owner"),
        "source_domains": domains,
        "source_count": citations_payload.get("source_count", len(domains)),
        "terms_url": pack.get("terms_url"),
        "robots_txt_url": _robots_url(primary_url),
        "robots_txt_hash": _robots_hash(pack_dir),
        "detected_license": license_hints[0] if len(license_hints) == 1 else None,
        "license_hints": license_hints,
        "allowed_use": allowed_use,
        "obligations": obligations,
        "pii_risk": pii_risk,
        "sensitive_data_risk": sensitive_risk,
        "summary": {
            "rights_status": rights_status,
            "redistribution_status": allowed_use["redistribution"],
            "model_training_status": allowed_use["model_training"],
            "eval_generation_status": allowed_use["eval_generation"],
        },
        "notes": [
            "Public or local source acquisition metadata only; this manifest is not legal advice.",
            "Allowed-with-conditions rights require the listed obligations to be honored.",
            "Unknown allowed-use fields require source-owner or policy review before reuse.",
            (
                "Review source terms and owner permissions before sharing raw content outside "
                "the originating workspace."
            ),
        ],
    }


def build_provenance_graph(
    pack_dir: Path,
    *,
    records: list[dict[str, Any]] | None = None,
    pack: dict[str, Any] | None = None,
    citations_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = records if records is not None else _read_pack_records(pack_dir)
    pack = pack if pack is not None else _read_pack_metadata(pack_dir)
    citations_payload = citations_payload or {}
    records_by_url = _records_by_url(records)
    sources = _list_value(citations_payload.get("sources"))
    routes = _read_json(pack_dir / "acquisition.routes.json", default={"routes": []})
    route_names = _route_names(routes)
    source_entries: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        url = str(source.get("url") or "")
        if not url:
            continue
        url_records = records_by_url.get(url, [])
        source_path = _source_path(pack_dir, source.get("path"))
        source_entries.append(
            {
                "url": url,
                "canonical_url": _first_record_value(url_records, "canonical_url") or url,
                "citation_id": source.get("citation_id"),
                "content_hashes": sorted(
                    {
                        str(record.get("content_hash") or "")
                        for record in url_records
                        if record.get("content_hash")
                    }
                ),
                "discovered_by": route_names or [_source_type_summary(url_records)],
                "selected_by": str(pack.get("workflow") or "pack_manifest"),
                "fetched_by": _source_type_summary(url_records),
                "converted_by": "docpull_normalizer",
                "included_documents": [
                    {
                        "document_id": record.get("document_id"),
                        "chunk_id": record.get("chunk_id"),
                        "content_hash": record.get("content_hash"),
                        "title": record.get("title"),
                    }
                    for record in url_records
                ],
                "citations": [
                    {
                        "citation_id": source.get("citation_id"),
                        "source_file": _relative_source_path(pack_dir, source_path),
                        "line_start": _line_range(source_path, url_records)[0],
                        "line_end": _line_range(source_path, url_records)[1],
                    }
                ],
            }
        )
    return {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir.resolve()),
        "summary": {
            "source_count": len(source_entries),
            "record_count": len(records),
            "route_count": len(route_names),
        },
        "routes": routes.get("routes", []) if isinstance(routes, dict) else [],
        "sources": source_entries,
    }


def build_citation_index(
    pack_dir: Path,
    *,
    records: list[dict[str, Any]] | None = None,
    citations_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records = records if records is not None else _read_pack_records(pack_dir)
    if citations_payload is None:
        from .pack_tools import build_citation_map

        citations_payload = build_citation_map(pack_dir)
    source_by_url = {
        str(source.get("url")): source
        for source in citations_payload.get("sources", [])
        if isinstance(source, dict) and source.get("url")
    }
    entries: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        url = str(record.get("url") or "")
        source = source_by_url.get(url, {})
        record_citation_id = _record_citation_id(source, record)
        source_path = _source_path(pack_dir, source.get("path") or record.get("output_path"))
        line_start, line_end = _line_range(source_path, [record])
        entries.append(
            {
                "citation_index_id": _stable_id(
                    "citation-index",
                    url,
                    str(record.get("content_hash") or ""),
                    str(index),
                ),
                "citation_id": source.get("citation_id"),
                "record_citation_id": record_citation_id,
                "document_id": record.get("document_id"),
                "chunk_id": record.get("chunk_id"),
                "url": url,
                "canonical_url": record.get("canonical_url") or url,
                "title": record.get("title") or source.get("title") or url,
                "source_file": _relative_source_path(pack_dir, source_path),
                "line_start": line_start,
                "line_end": line_end,
                "content_hash": record.get("content_hash"),
                "source_content_hash": record.get("source_content_hash") or record.get("content_hash"),
                "fetched_at": record.get("fetched_at"),
            }
        )
    return {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir.resolve()),
        "summary": {
            "entry_count": len(entries),
            "source_count": citations_payload.get("source_count", 0),
            "record_count": len(records),
        },
        "entries": entries,
    }


def build_pack_card(
    pack_dir: Path,
    *,
    pack: dict[str, Any] | None = None,
    records: list[dict[str, Any]] | None = None,
    score_payload: dict[str, Any] | None = None,
    citations_payload: dict[str, Any] | None = None,
    rights_payload: dict[str, Any] | None = None,
) -> str:
    pack = pack if pack is not None else _read_pack_metadata(pack_dir)
    records = records if records is not None else _read_pack_records(pack_dir)
    if score_payload is None:
        from .pack_tools import score_pack

        score_payload = score_pack(pack_dir)
    citations_payload = citations_payload or {}
    rights_payload = rights_payload or build_rights_manifest(pack_dir, records=records, pack=pack)
    coverage = _read_json(pack_dir / "coverage.report.json", default={})
    raw_coverage_summary = coverage.get("summary") if isinstance(coverage, dict) else {}
    coverage_summary = raw_coverage_summary if isinstance(raw_coverage_summary, dict) else {}
    basis_report = _read_json(pack_dir / "basis.report.json", default={})
    raw_basis_summary = basis_report.get("summary") if isinstance(basis_report, dict) else {}
    basis_summary = raw_basis_summary if isinstance(raw_basis_summary, dict) else {}
    eval_summary = _eval_summary(pack_dir)
    semantic_summary = _semantic_summary(pack_dir)
    lines = [
        "# Context Pack Card",
        "",
        "## Source",
        "",
        f"- Pack directory: `{pack_dir.resolve()}`",
        f"- Workflow: `{pack.get('workflow') or 'unknown'}`",
        f"- Provider: `{pack.get('provider') or 'unknown'}`",
        f"- Generated: `{utc_now_iso()}`",
        f"- Records: `{len(records)}`",
        f"- Sources: `{citations_payload.get('source_count', 'unknown')}`",
        "",
        "## Coverage",
        "",
        f"- Confidence: `{coverage_summary.get('coverage_confidence', 'unknown')}`",
        f"- Discovered URLs: `{coverage_summary.get('discovered_url_count', 'unknown')}`",
        f"- Selected URLs: `{coverage_summary.get('selected_url_count', 'unknown')}`",
        f"- Extracted docs: `{coverage_summary.get('extracted_doc_count', len(records))}`",
        f"- Pack score: `{score_payload.get('score')}/100 ({score_payload.get('grade')})`",
        "",
        "## Evidence Basis",
        "",
        f"- Basis records: `{basis_summary.get('basis_count', 'unknown')}`",
        f"- Supported ratio: `{basis_summary.get('supported_ratio', 'unknown')}`",
        f"- Citation coverage: `{basis_summary.get('citation_coverage', 'unknown')}`",
        f"- Low-confidence records: `{basis_summary.get('low_confidence_count', 'unknown')}`",
        "- Agent rule: refuse or ask for fresher context when basis evidence is partial or insufficient.",
        "",
        "## Rights",
        "",
        f"- Rights status: `{rights_payload['summary']['rights_status']}`",
        f"- Redistribution: `{rights_payload['allowed_use']['redistribution']}`",
        f"- Model training: `{rights_payload['allowed_use']['model_training']}`",
        f"- Eval generation: `{rights_payload['allowed_use']['eval_generation']}`",
        f"- PII risk: `{rights_payload['pii_risk']}`",
        f"- Sensitive data risk: `{rights_payload['sensitive_data_risk']}`",
        "",
        "## Recommended Uses",
        "",
        "- Local RAG and agent runtime context with citations.",
        "- Context coverage audits and freshness checks.",
        "- Eval candidate generation when rights review allows derived eval use.",
        "",
        "## Not Recommended Uses",
        "",
        "- Raw content redistribution without source-owner permission.",
        "- Model training unless the rights manifest explicitly permits it.",
        "- Complete-coverage claims when coverage confidence is not high.",
        "",
        "## Eval Summary",
        "",
        f"- Public tasks: `{eval_summary['public_task_count']}`",
        f"- Hidden answers: `{eval_summary['hidden_answer_count']}`",
        "",
        "## Change Summary",
        "",
        f"- Semantic categories: `{semantic_summary}`",
    ]
    raw_recommendations = coverage.get("recommendations") if isinstance(coverage, dict) else []
    recommendations = raw_recommendations if isinstance(raw_recommendations, list) else []
    if recommendations:
        lines.extend(["", "## Known Gaps", ""])
        lines.extend(f"- {item}" for item in recommendations if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def classify_semantic_diff(
    old_pack_dir: Path,
    new_pack_dir: Path,
    *,
    diff_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    old_pack_dir = old_pack_dir.resolve()
    new_pack_dir = new_pack_dir.resolve()
    old_records = _records_by_url(_read_ndjson(old_pack_dir / "documents.ndjson"))
    new_records = _records_by_url(_read_ndjson(new_pack_dir / "documents.ndjson"))
    if diff_payload is None:
        diff_payload = _simple_diff_payload(old_records, new_records)

    categories: dict[str, list[dict[str, Any]]] = {
        "breaking_change_candidate": [],
        "deprecation_candidate": [],
        "new_feature_candidate": [],
        "removed_section": [],
        "auth_security_change": [],
        "pricing_or_limit_change": [],
        "ambiguous_change": [],
    }
    for url in diff_payload.get("removed_urls", []):
        categories["removed_section"].append(_change_item(str(url), "URL disappeared from the newer pack."))
    for url in diff_payload.get("added_urls", []):
        text = _combined_text(new_records.get(str(url), []))
        _classify_changed_text(categories, str(url), "", text, added=True)
    for detail in diff_payload.get("changed_details", []):
        if not isinstance(detail, dict):
            continue
        url = str(detail.get("url") or "")
        if not url or not detail.get("content_changed"):
            continue
        old_text = _combined_text(old_records.get(url, []))
        new_text = _combined_text(new_records.get(url, []))
        _classify_changed_text(categories, url, old_text, new_text, added=False)

    summary = {key: len(value) for key, value in categories.items()}
    return {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "old_pack_dir": str(old_pack_dir),
        "new_pack_dir": str(new_pack_dir),
        "summary": {
            **summary,
            "total_candidate_count": sum(summary.values()),
            "added_count": len(diff_payload.get("added_urls", [])),
            "removed_count": len(diff_payload.get("removed_urls", [])),
            "changed_count": len(diff_payload.get("changed_urls", [])),
        },
        "categories": categories,
    }


def generate_eval_pack(
    pack_dir: Path,
    *,
    types: list[str] | None = None,
    limit: int = 50,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise EvalGradeError("--limit must be at least 1.")
    pack_dir = pack_dir.resolve()
    _require_pack(pack_dir)
    eval_types = _normalize_eval_types(types)
    records = [record for record in _read_pack_records(pack_dir) if str(record.get("content") or "").strip()]
    if not records:
        raise EvalGradeError("Pack has no non-empty records to generate evals from.")

    citation_index_path = pack_dir / "citation.index.json"
    if citation_index_path.exists():
        citation_index = _read_json(citation_index_path, default={})
    else:
        citation_index = build_citation_index(pack_dir, records=records)
        _write_json(citation_index_path, citation_index)
    raw_citation_entries = citation_index.get("entries")
    citation_entries = raw_citation_entries if isinstance(raw_citation_entries, list) else []
    entry_by_hash = {
        str(entry.get("content_hash")): entry
        for entry in citation_entries
        if isinstance(entry, dict) and entry.get("content_hash")
    }

    tasks: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    traps: list[dict[str, Any]] = []
    for record in records:
        for task_type in eval_types:
            if len(tasks) >= limit:
                break
            task, answer, trap = _eval_task_for_record(pack_dir, record, task_type, entry_by_hash)
            tasks.append(task)
            answers.append(answer)
            if trap:
                traps.append(trap)
        if len(tasks) >= limit:
            break

    eval_dir = (output_dir or (pack_dir / "evals")).resolve()
    eval_dir.mkdir(parents=True, exist_ok=True)
    tasks_path = eval_dir / "tasks.public.jsonl"
    answers_path = eval_dir / "answers.hidden.jsonl"
    traps_path = eval_dir / "traps.jsonl"
    rubric_path = eval_dir / "rubric.md"
    grader_path = eval_dir / "grader.py"
    _write_jsonl(tasks_path, tasks)
    _write_jsonl(answers_path, answers)
    _write_jsonl(traps_path, traps)
    rubric_path.write_text(_rubric_markdown(eval_types), encoding="utf-8")
    grader_path.write_text(_grader_py(), encoding="utf-8")
    return {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "eval_dir": str(eval_dir),
        "types": eval_types,
        "task_count": len(tasks),
        "hidden_answer_count": len(answers),
        "trap_count": len(traps),
        "artifacts": {
            "tasks_public": _artifact_ref(pack_dir, tasks_path),
            "answers_hidden": _artifact_ref(pack_dir, answers_path),
            "rubric": _artifact_ref(pack_dir, rubric_path),
            "grader": _artifact_ref(pack_dir, grader_path),
            "traps": _artifact_ref(pack_dir, traps_path),
        },
    }


def run_evalgen_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull evalgen",
        description="Generate citation-constrained eval candidates from a local context pack",
    )
    parser.add_argument("pack_dir", type=Path, help="Context pack directory")
    parser.add_argument(
        "--types",
        default=",".join(DEFAULT_EVAL_TYPES),
        help=(
            "Comma-separated eval types: current-context-qa,version-drift,citation,"
            "coverage-aware. current-docs-qa is accepted as a legacy alias."
        ),
    )
    parser.add_argument("--limit", type=int, default=50, help="Maximum public tasks to generate")
    parser.add_argument("--output-dir", "-o", type=Path, help="Eval artifact directory")
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print JSON summary")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = generate_eval_pack(
            args.pack_dir,
            types=[item.strip() for item in args.types.split(",")],
            limit=args.limit,
            output_dir=args.output_dir,
        )
    except EvalGradeError as err:
        console.print("[red]Evalgen error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(f"[green]Evalgen:[/green] {payload['task_count']} tasks -> {payload['eval_dir']}")
    return 0


def freshdocs_bench(
    pack_dir: Path,
    *,
    evals_path: Path | None = None,
    answers_path: Path | None = None,
    predictions_path: Path | None = None,
    output: Path | None = None,
    markdown_path: Path | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Write a local FreshDocs Bench report for one eval-grade pack."""
    pack_dir = pack_dir.resolve()
    _require_pack(pack_dir)
    eval_dir = pack_dir / "evals"
    tasks_path = evals_path or (eval_dir / "tasks.public.jsonl")
    hidden_path = answers_path or (eval_dir / "answers.hidden.jsonl")
    generated = None
    if not tasks_path.exists():
        generated = generate_eval_pack(pack_dir, limit=limit)
        tasks_path = Path(generated["eval_dir"]) / "tasks.public.jsonl"
        hidden_path = Path(generated["eval_dir"]) / "answers.hidden.jsonl"
    tasks = _read_jsonl(tasks_path)
    hidden_answers = _read_jsonl(hidden_path) if hidden_path.exists() else []
    predictions = _read_jsonl(predictions_path) if predictions_path else []
    prediction_by_id = {
        str(item.get("id") or item.get("task_id")): item
        for item in predictions
        if isinstance(item, dict) and (item.get("id") or item.get("task_id"))
    }
    hidden_by_id = {
        str(item.get("id")): item for item in hidden_answers if isinstance(item, dict) and item.get("id")
    }
    graded = [
        _grade_prediction(
            task,
            prediction_by_id.get(str(task.get("id"))),
            hidden_by_id.get(str(task.get("id"))),
        )
        for task in tasks
        if isinstance(task, dict)
    ]
    graded_with_predictions = [item for item in graded if item["status"] != "missing_prediction"]
    passed = sum(1 for item in graded_with_predictions if item["passed"])
    report_path = (output or (pack_dir / "freshdocs.report.json")).resolve()
    markdown_output = (markdown_path or (pack_dir / "FRESHDOCS_BENCH.md")).resolve()
    payload = {
        "schema_version": EVAL_GRADE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "summary": {
            "task_count": len(tasks),
            "hidden_answer_count": len(hidden_answers),
            "prediction_count": len(predictions),
            "graded_prediction_count": len(graded_with_predictions),
            "passed_count": passed,
            "pass_rate": (passed / len(graded_with_predictions)) if graded_with_predictions else None,
            "citation_requirement_count": sum(
                len(task.get("citation_requirements") or []) for task in tasks if isinstance(task, dict)
            ),
            "source_hash_requirement_count": sum(
                len(task.get("expected_source_hashes") or []) for task in tasks if isinstance(task, dict)
            ),
            "generated_missing_evals": generated is not None,
        },
        "artifacts": {
            "tasks_public": _artifact_ref(pack_dir, tasks_path),
            "answers_hidden": _artifact_ref(pack_dir, hidden_path),
            "report": _artifact_ref(pack_dir, report_path),
            "markdown": _artifact_ref(pack_dir, markdown_output),
        },
        "results": graded,
    }
    _write_json(report_path, payload)
    markdown_output.write_text(_freshdocs_markdown(payload), encoding="utf-8")
    return payload


def run_freshdocs_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull freshdocs",
        description="Run local FreshDocs Bench reports over eval-grade context packs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    bench = subparsers.add_parser("bench", help="Write a FreshDocs Bench report for a local pack")
    bench.add_argument("pack_dir", type=Path, help="Context pack directory")
    bench.add_argument("--evals", type=Path, help="tasks.public.jsonl path")
    bench.add_argument("--answers", type=Path, help="answers.hidden.jsonl path")
    bench.add_argument("--predictions", type=Path, help="Prediction JSONL with id/task_id and answer text")
    bench.add_argument("--output", "-o", type=Path, help="Report JSON output path")
    bench.add_argument("--markdown", type=Path, help="Markdown report output path")
    bench.add_argument("--limit", type=int, default=50, help="Evalgen limit when eval files are missing")
    bench.add_argument("--json", action="store_true", dest="json_output", help="Print JSON summary")
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.command == "bench":
            payload = freshdocs_bench(
                args.pack_dir,
                evals_path=args.evals,
                answers_path=args.answers,
                predictions_path=args.predictions,
                output=args.output,
                markdown_path=args.markdown,
                limit=args.limit,
            )
        else:  # pragma: no cover - guarded by argparse
            parser.error(f"Unknown command: {args.command}")
    except EvalGradeError as err:
        console.print("[red]FreshDocs error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(
            f"[green]FreshDocs Bench:[/green] {payload['summary']['task_count']} tasks -> "
            f"{payload['artifacts']['report']}"
        )
    return 0


def _classify_changed_text(
    categories: dict[str, list[dict[str, Any]]],
    url: str,
    old_text: str,
    new_text: str,
    *,
    added: bool,
) -> None:
    text = f"{url}\n{new_text}"
    matched = False
    if _DEPRECATION_RE.search(text):
        categories["deprecation_candidate"].append(
            _change_item(url, "Deprecation or removal language detected.")
        )
        matched = True
    if _BREAKING_RE.search(text) and not added:
        categories["breaking_change_candidate"].append(
            _change_item(url, "Breaking-change or migration language detected.")
        )
        matched = True
    if _FEATURE_RE.search(text) or added:
        categories["new_feature_candidate"].append(_change_item(url, "New or added source surface detected."))
        matched = True
    if _AUTH_SECURITY_RE.search(text):
        categories["auth_security_change"].append(_change_item(url, "Auth or security language changed."))
        matched = True
    if _PRICING_LIMIT_RE.search(text):
        categories["pricing_or_limit_change"].append(
            _change_item(url, "Pricing, billing, quota, or limit language changed.")
        )
        matched = True
    if not matched:
        categories["ambiguous_change"].append(
            _change_item(
                url,
                "Content hash changed but deterministic classifiers found no specific category.",
            )
        )


def _change_item(url: str, reason: str) -> dict[str, Any]:
    return {"url": url, "reason": reason}


def _eval_task_for_record(
    pack_dir: Path,
    record: dict[str, Any],
    task_type: str,
    entry_by_hash: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    content_hash = str(record.get("content_hash") or "")
    entry = entry_by_hash.get(content_hash, {})
    url = str(record.get("url") or "")
    title = str(record.get("title") or url)
    excerpt = _best_excerpt(str(record.get("content") or ""))
    task_id = _stable_id("eval", pack_dir.name, task_type, url, content_hash)
    citation_req = {
        "citation_id": entry.get("citation_id"),
        "source_url": url,
        "content_hash": content_hash,
        "source_file": entry.get("source_file"),
        "line_start": entry.get("line_start"),
        "line_end": entry.get("line_end"),
    }
    task = {
        "id": task_id,
        "pack": pack_dir.name,
        "task_type": task_type,
        "input": _question_for_task(task_type, title),
        "required_behavior": _required_behavior(task_type),
        "source_url": url,
        "required_sources": [url] if url else [],
        "citation_requirements": [citation_req],
        "expected_source_hashes": [content_hash] if content_hash else [],
        "grader": "claim_citation_rubric_v1",
        "difficulty": "medium",
    }
    answer = {
        "id": task_id,
        "task_type": task_type,
        "expected_claims": [
            {
                "claim": excerpt,
                "source_url": url,
                "source_hash": content_hash,
                "citation_id": entry.get("citation_id"),
                "line_start": entry.get("line_start"),
                "line_end": entry.get("line_end"),
                "citation_required": True,
            }
        ],
        "fail_if_contains": _fail_terms(task_type, str(record.get("content") or "")),
    }
    trap = None
    if task_type == "version-drift" or _DEPRECATION_RE.search(str(record.get("content") or "")):
        trap = {
            "id": task_id,
            "trap_type": "stale_or_deprecated_answer",
            "source_url": url,
            "fail_if_contains": answer["fail_if_contains"],
            "required_behavior": "Use the current cited source and avoid stale or deprecated claims.",
        }
    return task, answer, trap


def _question_for_task(task_type: str, title: str) -> str:
    if task_type == "version-drift":
        return (
            "An older answer may be stale. Using the current context, what is the current behavior "
            f"for {title}?"
        )
    if task_type == "citation":
        return f"Answer the question about {title} and cite the exact source that supports each claim."
    if task_type == "coverage-aware":
        return (
            "Can this context pack confirm the current source-backed behavior for "
            f"{title}? Answer only with supported citations."
        )
    return f"Using only the current context, what does {title} say?"


def _required_behavior(task_type: str) -> list[str]:
    common = [
        "answer from current source content",
        "include citations for factual claims",
        "do not invent uncited parameters, APIs, or policy details",
    ]
    if task_type == "version-drift":
        return [*common, "flag stale or deprecated behavior when present"]
    if task_type == "coverage-aware":
        return [*common, "say when the pack does not provide enough evidence"]
    if task_type == "citation":
        return [*common, "every material claim must map to a citation requirement"]
    return common


def _fail_terms(task_type: str, content: str) -> list[str]:
    terms = ["uncited answer", "I know from training data"]
    if task_type == "version-drift" or _DEPRECATION_RE.search(content):
        terms.extend(["deprecated method without warning", "old SDK behavior", "pre-current context answer"])
    return terms


def _normalize_eval_types(types: list[str] | None) -> list[str]:
    raw = types or list(DEFAULT_EVAL_TYPES)
    normalized: list[str] = []
    for item in raw:
        for value in str(item).split(","):
            cleaned = value.strip()
            if not cleaned:
                continue
            if cleaned not in _KNOWN_EVAL_TYPES:
                raise EvalGradeError(f"Unsupported eval type: {cleaned}")
            cleaned = LEGACY_EVAL_TYPE_ALIASES.get(cleaned, cleaned)
            if cleaned not in normalized:
                normalized.append(cleaned)
    return normalized or list(DEFAULT_EVAL_TYPES)


def _best_excerpt(content: str) -> str:
    cleaned = _clean_text(content)
    if not cleaned:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    candidates = [sentence.strip() for sentence in sentences if len(sentence.strip()) >= 40]
    return _truncate(candidates[0] if candidates else cleaned, 420)


def _rubric_markdown(eval_types: list[str]) -> str:
    return (
        "# DocPull Eval Rubric\n\n"
        f"Eval types: `{', '.join(eval_types)}`\n\n"
        "A passing answer must:\n\n"
        "- answer only from the provided current source content;\n"
        "- cite the required source URL or citation ID for every material claim;\n"
        "- avoid deprecated or stale behavior when the task asks for current behavior;\n"
        "- say when coverage is insufficient instead of inventing missing facts.\n"
    )


def _grader_py() -> str:
    return '''"""Minimal citation-required grader for DocPull evalgen tasks."""

from __future__ import annotations


def grade(answer: str, task: dict) -> dict:
    text = answer or ""
    requirements = task.get("citation_requirements") or []
    required_urls = {item.get("source_url") for item in requirements if item.get("source_url")}
    required_citations = {item.get("citation_id") for item in requirements if item.get("citation_id")}
    has_url = any(url in text for url in required_urls)
    has_citation = any(str(citation) in text for citation in required_citations)
    passed = bool(text.strip()) and (has_url or has_citation)
    return {
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "checks": {
            "non_empty": bool(text.strip()),
            "has_required_url": has_url,
            "has_required_citation": has_citation,
        },
    }
'''


def _grade_prediction(
    task: dict[str, Any],
    prediction: dict[str, Any] | None,
    hidden_answer: dict[str, Any] | None,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "")
    if not prediction:
        return {"id": task_id, "status": "missing_prediction", "passed": False, "score": None}
    text = _prediction_text(prediction)
    raw_requirements = task.get("citation_requirements")
    requirements = raw_requirements if isinstance(raw_requirements, list) else []
    required_urls = [
        str(item.get("source_url"))
        for item in requirements
        if isinstance(item, dict) and item.get("source_url")
    ]
    required_citations = [
        str(item.get("citation_id"))
        for item in requirements
        if isinstance(item, dict) and item.get("citation_id")
    ]
    text_lower = text.lower()
    fail_terms = []
    if isinstance(hidden_answer, dict):
        raw_terms = hidden_answer.get("fail_if_contains")
        fail_terms = [str(item) for item in raw_terms] if isinstance(raw_terms, list) else []
    failed_terms = [term for term in fail_terms if term and term.lower() in text_lower]
    has_required_url = any(url and url in text for url in required_urls)
    has_required_citation = any(citation and citation in text for citation in required_citations)
    citation_ok = has_required_url or has_required_citation or not requirements
    passed = bool(text.strip()) and citation_ok and not failed_terms
    return {
        "id": task_id,
        "status": "graded",
        "passed": passed,
        "score": 1.0 if passed else 0.0,
        "checks": {
            "non_empty": bool(text.strip()),
            "has_required_url": has_required_url,
            "has_required_citation": has_required_citation,
            "failed_terms": failed_terms,
        },
    }


def _prediction_text(prediction: dict[str, Any]) -> str:
    for key in ("answer", "output", "text", "response"):
        value = prediction.get(key)
        if isinstance(value, str):
            return value
    return ""


def _freshdocs_markdown(payload: dict[str, Any]) -> str:
    summary = _dict_value(payload.get("summary"))
    lines = [
        "# FreshDocs Bench Report",
        "",
        f"- Pack: `{payload.get('pack_dir')}`",
        f"- Tasks: `{summary.get('task_count', 0)}`",
        f"- Hidden answers: `{summary.get('hidden_answer_count', 0)}`",
        f"- Predictions: `{summary.get('prediction_count', 0)}`",
        f"- Graded predictions: `{summary.get('graded_prediction_count', 0)}`",
        f"- Passed: `{summary.get('passed_count', 0)}`",
        f"- Pass rate: `{summary.get('pass_rate')}`",
        f"- Citation requirements: `{summary.get('citation_requirement_count', 0)}`",
        f"- Source hash requirements: `{summary.get('source_hash_requirement_count', 0)}`",
    ]
    if summary.get("generated_missing_evals"):
        lines.append("- Eval files were generated because none were present.")
    return "\n".join(lines).rstrip() + "\n"


def _privacy_risks(records: list[dict[str, Any]]) -> tuple[str, str]:
    sample = "\n".join(str(record.get("content") or "") for record in records[:200])
    pii = "medium" if _EMAIL_RE.search(sample) else "low"
    sensitive = "medium" if _SENSITIVE_RE.search(sample) else "low"
    return pii, sensitive


def _record_allowed_use(records: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field in ("internal_indexing", "redistribution", "model_training", "eval_generation"):
        statuses: list[str] = []
        for record in records:
            rights = _dict_value(record.get("rights"))
            allowed = _dict_value(rights.get("allowed_use"))
            statuses.append(_allowed_status(allowed.get(field)))
        explicit = [status for status in statuses if status != "unknown"]
        if explicit and all(status in {"allowed", "allowed_with_conditions"} for status in explicit):
            result[field] = (
                "allowed_with_conditions"
                if any(status == "allowed_with_conditions" for status in explicit)
                else "allowed"
            )
        elif any(status == "denied" for status in explicit):
            result[field] = "denied"
        else:
            result[field] = "unknown"
    return result


def _allowed_status(value: Any) -> str:
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"allowed", "permitted", "yes", "true"}:
        return "allowed"
    if normalized in {
        "allowed_with_conditions",
        "conditional",
        "conditioned",
        "permitted_with_conditions",
    }:
        return "allowed_with_conditions"
    if normalized in {"denied", "disallowed", "no", "false"}:
        return "denied"
    return "unknown"


def _rights_obligations(records: list[dict[str, Any]], *, pack: dict[str, Any]) -> list[str]:
    obligations: set[str] = set()
    pack_obligations = pack.get("obligations")
    if isinstance(pack_obligations, list):
        obligations.update(str(item).strip() for item in pack_obligations if str(item).strip())
    for record in records:
        rights = _dict_value(record.get("rights"))
        record_obligations = _list_value(rights.get("obligations"))
        obligations.update(str(item).strip() for item in record_obligations if str(item).strip())
    return sorted(obligations)


def _line_range(path: Path | None, records: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    if path is None or not path.exists() or not path.is_file():
        return None, None
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines:
        return 1, 1
    for record in records:
        content = str(record.get("content") or "").strip()
        if not content:
            continue
        offset = text.find(content)
        if offset >= 0:
            start = text.count("\n", 0, offset) + 1
            end = start + content.count("\n")
            return start, max(start, end)
    return 1, len(lines)


def _route_names(acquisition: Any) -> list[str]:
    if not isinstance(acquisition, dict):
        return []
    routes = acquisition.get("routes")
    if not isinstance(routes, list):
        return []
    names: list[str] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        name = str(route.get("route") or route.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _source_type_summary(records: list[dict[str, Any]]) -> str:
    counts = Counter(str(record.get("source_type") or "documents.ndjson") for record in records)
    return counts.most_common(1)[0][0] if counts else "documents.ndjson"


def _first_record_value(records: list[dict[str, Any]], key: str) -> Any:
    for record in records:
        value = record.get(key)
        if value:
            return value
    return None


def _coverage_confidence(pack_dir: Path) -> str:
    coverage = _read_json(pack_dir / "coverage.report.json", default={})
    raw_summary = coverage.get("summary") if isinstance(coverage, dict) else {}
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    return str(summary.get("coverage_confidence") or "unknown")


def _eval_summary(pack_dir: Path) -> dict[str, int]:
    return {
        "public_task_count": _line_count(pack_dir / "evals" / "tasks.public.jsonl"),
        "hidden_answer_count": _line_count(pack_dir / "evals" / "answers.hidden.jsonl"),
    }


def _semantic_summary(pack_dir: Path) -> dict[str, int]:
    payload = _read_json(pack_dir / "semantic.diff.json", default={})
    raw_summary = payload.get("summary") if isinstance(payload, dict) else {}
    summary = raw_summary if isinstance(raw_summary, dict) else {}
    return {
        key: int(value)
        for key, value in summary.items()
        if key.endswith("_candidate") or key.endswith("_change") or key == "removed_section"
        if isinstance(value, int)
    }


def _simple_diff_payload(
    old_records: dict[str, list[dict[str, Any]]],
    new_records: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    old_urls = set(old_records)
    new_urls = set(new_records)
    shared = sorted(old_urls & new_urls)
    changed = [url for url in shared if _hashes(old_records[url]) != _hashes(new_records[url])]
    return {
        "added_urls": sorted(new_urls - old_urls),
        "removed_urls": sorted(old_urls - new_urls),
        "changed_urls": changed,
        "changed_details": [{"url": url, "content_changed": True} for url in changed],
    }


def _hashes(records: list[dict[str, Any]]) -> list[str]:
    return sorted(str(record.get("content_hash") or "") for record in records)


def _combined_text(records: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for record in records:
        parts.append(str(record.get("title") or ""))
        parts.append(str(record.get("content") or ""))
    return "\n".join(parts)


def _records_by_url(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        url = str(record.get("url") or "")
        if url:
            grouped.setdefault(url, []).append(record)
    return grouped


def _domains_from_records(records: list[dict[str, Any]]) -> list[str]:
    domains = sorted(
        {
            (urlparse(str(record.get("url") or "")).hostname or "").lower().removeprefix("www.")
            for record in records
            if record.get("url")
        }
    )
    return [domain for domain in domains if domain]


def _primary_url(records: list[dict[str, Any]]) -> str | None:
    for record in records:
        url = str(record.get("url") or "")
        if url:
            return url
    return None


def _robots_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/robots.txt"


def _robots_hash(pack_dir: Path) -> str | None:
    for candidate in ("robots.txt", "robots.snapshot.txt"):
        path = pack_dir / candidate
        if path.exists() and path.is_file():
            return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def _source_path(pack_dir: Path, value: Any) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved = pack_dir / path
    return resolved if resolved.exists() else None


def _relative_source_path(pack_dir: Path, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.relative_to(pack_dir).as_posix()
    except ValueError:
        return str(path)


def _record_citation_id(source: dict[str, Any], record: dict[str, Any]) -> str | None:
    record_key = str(record.get("chunk_id") or record.get("document_id") or record.get("content_hash") or "")
    citations = source.get("record_citations")
    if isinstance(citations, list):
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            if str(citation.get("record_key") or "") == record_key:
                value = citation.get("record_citation_id")
                return str(value) if value else None
    value = source.get("citation_id")
    return str(value) if value else None


def _artifact_ref(pack_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(pack_dir).as_posix()
    except ValueError:
        return str(path)


def _stable_id(*parts: str) -> str:
    raw = "\x1f".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _clean_text(value: str) -> str:
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", value, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _read_pack_metadata(pack_dir: Path) -> dict[str, Any]:
    candidates = [
        pack_dir / "parallel.pack.json",
        pack_dir / "local.pack.json",
        *sorted(pack_dir.glob("*.pack.json")),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        payload = _read_json(candidate, default=None)
        if isinstance(payload, dict):
            return payload
    return {}


def _require_pack(pack_dir: Path) -> None:
    if not pack_dir.exists() or not pack_dir.is_dir():
        raise EvalGradeError(f"Pack directory does not exist: {pack_dir}")
    if not (pack_dir / "documents.ndjson").exists():
        raise EvalGradeError(f"Missing required file: {pack_dir / 'documents.ndjson'}")


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise EvalGradeError(f"Invalid JSON in {path}: {err}") from err


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise EvalGradeError(f"Missing required file: {path}")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise EvalGradeError(f"Invalid NDJSON in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise EvalGradeError(f"Invalid NDJSON in {path} line {index}: expected object")
        records.append(value)
    return records


def _read_pack_records(pack_dir: Path) -> list[dict[str, Any]]:
    ndjson = pack_dir / "documents.ndjson"
    if ndjson.exists():
        return _read_ndjson(ndjson)
    try:
        from .pack_reader import load_pack

        pack = load_pack(pack_dir)
    except Exception as err:  # noqa: BLE001
        raise EvalGradeError(f"Unable to load pack records from {pack_dir}: {err}") from err
    return [record.model_dump(mode="json", exclude_none=True) for record in pack.documents]


def _read_jsonl(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    if not path.exists():
        raise EvalGradeError(f"Missing required JSONL file: {path}")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise EvalGradeError(f"Invalid JSONL in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise EvalGradeError(f"Invalid JSONL in {path} line {index}: expected object")
        records.append(value)
    return records


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_evalgen_cli())
