"""Utilities for inspecting docpull context packs."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .source_scoring import score_source_entries
from .time_utils import utc_now_iso

SCORE_SCHEMA_VERSION = 1
DIFF_SCHEMA_VERSION = 1
SOURCE_SCORE_SCHEMA_VERSION = 1
CITATION_SCHEMA_VERSION = 1
ENTITY_SCHEMA_VERSION = 1
BRIEF_SCHEMA_VERSION = 1
SEARCH_SCHEMA_VERSION = 1
SEARCH_COLLECTION_SCHEMA_VERSION = 1
PREPARE_SCHEMA_VERSION = 1
DEFAULT_ENTITY_LIMIT = 100
DEFAULT_BRIEF_EXCERPTS = 8
DEFAULT_BRIEF_ENTITY_LIMIT = 20
DEFAULT_SEARCH_LIMIT = 10
DEFAULT_GRAPH_ENTITY_LIMIT = 500

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{2,}", re.IGNORECASE)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\([^)]+\)")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_MONEY_RE = re.compile(
    r"(?<!\w)(?:\$|USD\s*)\d[\d,]*(?:\.\d+)?(?:\s?(?:k|K|m|M|b|B|million|billion|thousand))?\b"
)
_DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_VERSION_RE = re.compile(r"\b(?:v|version\s*)?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?\b", re.IGNORECASE)
_ORG_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9&.'-]+(?:\s+[A-Z][A-Za-z0-9&.'-]+){0,5}\s+"
    r"(?:Inc\.?|LLC|Ltd\.?|Corporation|Corp\.?|Labs?|Systems?|Technologies|"
    r"Technology|Software|Cloud|Research|Foundation)\b"
)
_TECH_TERM_RE = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9.+-]*\s+){0,4}"
    r"(?:API|SDK|MCP|RAG|LLM|CLI|JSON|NDJSON|SQLite|SQL|OpenAPI)"
    r"(?:\s+[A-Z][A-Za-z0-9.+-]*){0,3}\b"
)
_ENTITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("money", _MONEY_RE),
    ("date", _DATE_RE),
    ("version", _VERSION_RE),
    ("organization", _ORG_RE),
    ("technical_term", _TECH_TERM_RE),
)


class PackToolError(RuntimeError):
    """User-facing pack tooling error."""


def create_pack_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull pack",
        description="Inspect, score, and diff docpull context packs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    score = subparsers.add_parser("score", help="Score a context pack for agent readiness")
    score.add_argument("pack_dir", type=Path, help="Context pack directory")
    score.add_argument("--output", type=Path, help="Score JSON output path")
    score.add_argument("--min-score", type=int, default=0, help="Exit non-zero if score is below N")
    score.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    audit = subparsers.add_parser("audit", help="Write an actionable local quality audit")
    audit.add_argument("pack_dir", type=Path, help="Context pack directory")
    audit.add_argument("--output", type=Path, help="Audit JSON output path")
    audit.add_argument("--markdown", type=Path, help="Audit Markdown output path")
    audit.add_argument(
        "--fail-under",
        type=float,
        help="Exit non-zero if audit score is below this 0.0-1.0 threshold",
    )
    audit.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    diff = subparsers.add_parser("diff", help="Diff two context packs")
    diff.add_argument("old_pack_dir", type=Path, help="Older context pack directory")
    diff.add_argument("new_pack_dir", type=Path, help="Newer context pack directory")
    diff.add_argument("--output", type=Path, help="Diff JSON output path")
    diff.add_argument("--markdown", type=Path, help="Diff Markdown output path")

    sources = subparsers.add_parser("sources", help="Score and rank pack sources")
    sources.add_argument("pack_dir", type=Path, help="Context pack directory")
    sources.add_argument("--output", type=Path, help="Source score JSON output path")
    sources.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    citations = subparsers.add_parser("citations", help="Build a stable citation map for a pack")
    citations.add_argument("pack_dir", type=Path, help="Context pack directory")
    citations.add_argument("--output", type=Path, help="Citation JSON output path")
    citations.add_argument("--markdown", type=Path, help="Citation Markdown output path")
    citations.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    entities = subparsers.add_parser("entities", help="Extract cited local entities from a pack")
    entities.add_argument("pack_dir", type=Path, help="Context pack directory")
    entities.add_argument("--output", type=Path, help="Entity JSON output path")
    entities.add_argument("--markdown", type=Path, help="Entity Markdown output path")
    entities.add_argument("--limit", type=int, default=DEFAULT_ENTITY_LIMIT, help="Maximum entities")
    entities.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    search = subparsers.add_parser("search", help="Search a context pack locally with citations")
    search.add_argument("pack_dir", type=Path, help="Context pack directory")
    search.add_argument("query", help="Search query")
    search.add_argument("--output", type=Path, help="Search JSON output path")
    search.add_argument("--markdown", type=Path, help="Search Markdown output path")
    search.add_argument("--limit", type=int, default=DEFAULT_SEARCH_LIMIT, help="Maximum results")
    search.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    brief = subparsers.add_parser("brief", help="Write a local cited research brief for a pack")
    brief.add_argument("pack_dir", type=Path, help="Context pack directory")
    brief.add_argument("--objective", help="Brief objective. Defaults to pack metadata when present.")
    brief.add_argument("--output", type=Path, help="Markdown brief output path")
    brief.add_argument("--json-output", type=Path, help="Brief JSON output path")
    brief.add_argument(
        "--max-excerpts",
        type=int,
        default=DEFAULT_BRIEF_EXCERPTS,
        help="Maximum cited excerpts in the brief",
    )
    brief.add_argument(
        "--entity-limit",
        type=int,
        default=DEFAULT_BRIEF_ENTITY_LIMIT,
        help="Maximum entity records included in the brief",
    )
    brief.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )

    prepare = subparsers.add_parser(
        "prepare",
        help="Write all local pack intelligence artifacts for agent loading",
    )
    prepare.add_argument("pack_dir", type=Path, help="Context pack directory")
    prepare.add_argument("--objective", help="Brief objective. Defaults to pack metadata when present.")
    prepare.add_argument(
        "--search-query",
        action="append",
        dest="search_queries",
        default=[],
        help="Local search query to include in SEARCH.md. Repeat as needed.",
    )
    prepare.add_argument("--no-search", action="store_true", help="Skip local pack search artifacts")
    prepare.add_argument("--output", type=Path, help="Prepare summary JSON output path")
    prepare.add_argument(
        "--max-excerpts",
        type=int,
        default=DEFAULT_BRIEF_EXCERPTS,
        help="Maximum cited excerpts in the brief",
    )
    prepare.add_argument(
        "--entity-limit",
        type=int,
        default=DEFAULT_BRIEF_ENTITY_LIMIT,
        help="Maximum entity records included in generated artifacts",
    )
    prepare.add_argument(
        "--search-limit",
        type=int,
        default=DEFAULT_SEARCH_LIMIT,
        help="Maximum search results",
    )
    prepare.add_argument(
        "--graph-entity-limit",
        type=int,
        default=DEFAULT_GRAPH_ENTITY_LIMIT,
        help="Maximum entities to include in graph artifacts",
    )
    prepare.add_argument("--no-graph", action="store_true", help="Skip local source graph artifacts")
    prepare.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )
    prepare.add_argument("--no-markdown", action="store_true", help="Write JSON artifacts only")

    return parser


def run_pack_cli(argv: list[str] | None = None) -> int:
    parser = create_pack_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.command == "score":
            payload = score_pack(args.pack_dir, required_domains=args.required_domains)
            output = args.output or (args.pack_dir / "pack.score.json")
            _write_json(output, payload)
            console.print(
                f"[green]Pack score:[/green] {payload['score']}/100 ({payload['grade']}) -> {output}"
            )
            if payload["score"] < args.min_score:
                return 1
            return 0
        if args.command == "audit":
            from .local_workflows import audit_pack

            payload = audit_pack(
                args.pack_dir,
                required_domains=args.required_domains,
                fail_under=args.fail_under,
                json_path=args.output,
                markdown_path=args.markdown,
            )
            console.print(
                f"[green]Pack audit:[/green] {payload['score']}/100 ({payload['grade']}) "
                f"-> {payload['artifacts']['json']}"
            )
            return 0
        if args.command == "diff":
            payload = diff_packs(args.old_pack_dir, args.new_pack_dir)
            output = args.output or (args.new_pack_dir / "pack.diff.json")
            _write_json(output, payload)
            if args.markdown:
                args.markdown.write_text(_diff_markdown(payload), encoding="utf-8")
            console.print(
                "[green]Pack diff:[/green] "
                f"+{len(payload['added_urls'])} "
                f"-{len(payload['removed_urls'])} "
                f"~{len(payload['changed_urls'])} -> {output}"
            )
            return 0
        if args.command == "sources":
            payload = score_pack_sources(args.pack_dir, required_domains=args.required_domains)
            output = args.output or (args.pack_dir / "source.scores.json")
            _write_json(output, payload)
            console.print(f"[green]Source scores:[/green] {len(payload['sources'])} sources -> {output}")
            return 0
        if args.command == "citations":
            payload = build_citation_map(args.pack_dir, required_domains=args.required_domains)
            output = args.output or (args.pack_dir / "citations.json")
            _write_json(output, payload)
            if args.markdown:
                args.markdown.parent.mkdir(parents=True, exist_ok=True)
                args.markdown.write_text(_citations_markdown(payload), encoding="utf-8")
            console.print(f"[green]Citations:[/green] {payload['source_count']} sources -> {output}")
            return 0
        if args.command == "entities":
            payload = extract_pack_entities(
                args.pack_dir,
                required_domains=args.required_domains,
                limit=args.limit,
            )
            output = args.output or (args.pack_dir / "entities.json")
            _write_json(output, payload)
            if args.markdown:
                args.markdown.parent.mkdir(parents=True, exist_ok=True)
                args.markdown.write_text(_entities_markdown(payload), encoding="utf-8")
            console.print(f"[green]Entities:[/green] {payload['entity_count']} entities -> {output}")
            return 0
        if args.command == "search":
            payload = search_pack(
                args.pack_dir,
                args.query,
                required_domains=args.required_domains,
                limit=args.limit,
            )
            output = args.output or (args.pack_dir / "pack.search.json")
            _write_json(output, payload)
            if args.markdown:
                args.markdown.parent.mkdir(parents=True, exist_ok=True)
                args.markdown.write_text(_search_markdown(payload), encoding="utf-8")
            console.print(f"[green]Pack search:[/green] {payload['result_count']} results -> {output}")
            return 0
        if args.command == "brief":
            payload = build_research_brief(
                args.pack_dir,
                objective=args.objective,
                required_domains=args.required_domains,
                max_excerpts=args.max_excerpts,
                entity_limit=args.entity_limit,
            )
            json_output = args.json_output or (args.pack_dir / "research.brief.json")
            markdown_output = args.output or (args.pack_dir / "RESEARCH_BRIEF.md")
            _write_json(json_output, payload)
            markdown_output.parent.mkdir(parents=True, exist_ok=True)
            markdown_output.write_text(_brief_markdown(payload), encoding="utf-8")
            _write_json(
                args.pack_dir / "citations.json",
                build_citation_map(args.pack_dir, required_domains=args.required_domains),
            )
            _write_json(
                args.pack_dir / "entities.json",
                extract_pack_entities(
                    args.pack_dir,
                    required_domains=args.required_domains,
                    limit=max(args.entity_limit, 1),
                ),
            )
            console.print(
                f"[green]Research brief:[/green] {len(payload['key_excerpts'])} excerpts -> {markdown_output}"
            )
            return 0
        if args.command == "prepare":
            payload = prepare_pack(
                args.pack_dir,
                objective=args.objective,
                search_queries=[] if args.no_search else (args.search_queries or None),
                default_search=not args.no_search,
                required_domains=args.required_domains,
                max_excerpts=args.max_excerpts,
                entity_limit=args.entity_limit,
                search_limit=args.search_limit,
                graph=not args.no_graph,
                graph_entity_limit=args.graph_entity_limit,
                markdown=not args.no_markdown,
                output=args.output,
            )
            console.print(
                "[green]Prepared pack:[/green] "
                f"{payload['summary']['artifact_count']} artifacts -> "
                f"{payload['artifacts']['prepare']}"
            )
            return 0
        parser.error(f"Unknown command: {args.command}")
    except PackToolError as err:
        console.print("[red]Pack error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Pack command failed:[/red] " + escape(str(err)))
        return 1
    return 1


def score_pack(pack_dir: Path, *, required_domains: list[str] | None = None) -> dict[str, Any]:
    pack_dir = pack_dir.resolve()
    manifest = _read_json(pack_dir / "corpus.manifest.json", required=False) or {}
    parallel_pack, metadata_path = _read_pack_metadata_entry(pack_dir)
    metadata_label = metadata_path.name if metadata_path else "pack metadata"
    records = _read_ndjson(pack_dir / "documents.ndjson")

    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    score = 100

    record_count = len(records)
    urls = [str(record.get("url", "")) for record in records if record.get("url")]
    unique_urls = sorted(set(urls))
    domains = Counter(_domain(url) for url in urls if _domain(url))
    content_hashes = [str(record.get("content_hash", "")) for record in records if record.get("content_hash")]
    duplicate_chunks = len(content_hashes) - len(set(content_hashes))
    token_counts = [int(record.get("token_count", 0) or 0) for record in records]
    empty_records = [record for record in records if not str(record.get("content", "")).strip()]

    if record_count == 0:
        score -= 35
        issues.append(_issue("empty_pack", "Pack has no records.", severity="error"))
    if empty_records:
        score -= min(20, len(empty_records) * 5)
        issues.append(_issue("empty_records", f"{len(empty_records)} records have empty content."))
    manifest_record_count = _optional_int(manifest.get("record_count"))
    if manifest_record_count is not None and manifest_record_count != record_count:
        score -= 15
        issues.append(
            _issue(
                "manifest_record_count_mismatch",
                (
                    "corpus.manifest.json record_count "
                    f"({manifest_record_count}) does not match documents.ndjson ({record_count})."
                ),
                severity="error",
            )
        )
    manifest_records = manifest.get("records")
    if isinstance(manifest_records, list) and len(manifest_records) != record_count:
        score -= 10
        issues.append(
            _issue(
                "manifest_records_mismatch",
                (
                    "corpus.manifest.json records length "
                    f"({len(manifest_records)}) does not match documents.ndjson ({record_count})."
                ),
                severity="error",
            )
        )
    if duplicate_chunks:
        score -= min(15, duplicate_chunks * 3)
        warnings.append(
            _issue("duplicate_chunks", f"{duplicate_chunks} records share duplicate content hashes.")
        )
    if not (pack_dir / "corpus.manifest.json").exists():
        score -= 15
        issues.append(_issue("missing_manifest", "corpus.manifest.json is missing."))
    if not (pack_dir / "sources.md").exists():
        score -= 8
        warnings.append(_issue("missing_sources_index", "sources.md is missing."))
    if parallel_pack:
        pack_record_count = _optional_int(parallel_pack.get("record_count"))
        if pack_record_count is not None and pack_record_count != record_count:
            score -= 15
            issues.append(
                _issue(
                    "pack_record_count_mismatch",
                    (
                        f"{metadata_label} record_count "
                        f"({pack_record_count}) does not match documents.ndjson ({record_count})."
                    ),
                    severity="error",
                )
            )
        missing_artifacts = _missing_declared_artifacts(pack_dir, parallel_pack)
        if missing_artifacts:
            score -= min(30, len(missing_artifacts) * 10)
            issues.append(
                _issue(
                    "missing_declared_artifacts",
                    f"{len(missing_artifacts)} declared pack artifacts are missing.",
                    paths=missing_artifacts,
                    severity="error",
                )
            )
        missing_sources = _missing_declared_sources(pack_dir, parallel_pack)
        if missing_sources:
            score -= min(20, len(missing_sources) * 8)
            issues.append(
                _issue(
                    "missing_declared_sources",
                    f"{len(missing_sources)} declared source files are missing.",
                    paths=missing_sources,
                    severity="error",
                )
            )
        if "artifacts" not in parallel_pack:
            score -= 5
            warnings.append(_issue("missing_artifact_index", f"{metadata_label} has no artifacts index."))
        if parallel_pack.get("extract_error_count", 0):
            count = int(parallel_pack.get("extract_error_count", 0))
            score -= min(15, count * 5)
            warnings.append(_issue("extract_errors", f"Pack preserved {count} extract errors."))
        if not _pack_request_options(parallel_pack):
            score -= 5
            warnings.append(_issue("missing_request_options", f"{metadata_label} has no request_options."))
        if parallel_pack.get("task_run_id") and not parallel_pack.get("task_basis"):
            score -= 5
            warnings.append(_issue("missing_task_basis", "Task output has no basis metadata."))

    expected = required_domains or _expected_domains(parallel_pack)
    off_domain = _off_domain_urls(unique_urls, expected)
    if expected and off_domain:
        score -= min(25, len(off_domain) * 8)
        issues.append(
            _issue(
                "off_domain_sources",
                f"{len(off_domain)} sources are outside the expected domains.",
                urls=off_domain,
            )
        )

    score = max(0, min(100, score))
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "score": score,
        "grade": _grade(score),
        "summary": {
            "record_count": record_count,
            "document_count": manifest.get("document_count"),
            "unique_url_count": len(unique_urls),
            "domain_count": len(domains),
            "duplicate_chunk_count": duplicate_chunks,
            "total_tokens": sum(token_counts),
        },
        "domains": dict(domains.most_common()),
        "expected_domains": expected,
        "issues": issues,
        "warnings": warnings,
    }


def score_pack_sources(pack_dir: Path, *, required_domains: list[str] | None = None) -> dict[str, Any]:
    pack_dir = pack_dir.resolve()
    parallel_pack = _read_pack_metadata(pack_dir)
    records = _read_ndjson(pack_dir / "documents.ndjson")
    expected = required_domains or _expected_domains(parallel_pack)
    sources = _pack_source_entries(parallel_pack, records)
    scored = score_source_entries(sources, expected_domains=expected)
    return {
        "schema_version": SOURCE_SCORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "expected_domains": expected,
        "source_count": len(scored),
        "sources": scored,
    }


def build_citation_map(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
) -> dict[str, Any]:
    pack_dir = pack_dir.resolve()
    pack = _read_pack_metadata(pack_dir)
    records = _read_ndjson(pack_dir / "documents.ndjson")
    sources, _citation_by_url, expected = _citation_sources(pack_dir, pack, records, required_domains)
    return {
        "schema_version": CITATION_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "expected_domains": expected,
        "source_count": len(sources),
        "record_count": len(records),
        "sources": sources,
    }


def extract_pack_entities(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    limit: int = DEFAULT_ENTITY_LIMIT,
) -> dict[str, Any]:
    if limit < 1:
        raise PackToolError("--limit must be at least 1.")
    pack_dir = pack_dir.resolve()
    pack = _read_pack_metadata(pack_dir)
    records = _read_ndjson(pack_dir / "documents.ndjson")
    sources, citation_by_url, expected = _citation_sources(pack_dir, pack, records, required_domains)
    entities = _extract_entities(records, citation_by_url, limit=limit)
    return {
        "schema_version": ENTITY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "expected_domains": expected,
        "source_count": len(sources),
        "record_count": len(records),
        "entity_count": len(entities),
        "entities": entities,
    }


def build_research_brief(
    pack_dir: Path,
    *,
    objective: str | None = None,
    required_domains: list[str] | None = None,
    max_excerpts: int = DEFAULT_BRIEF_EXCERPTS,
    entity_limit: int = DEFAULT_BRIEF_ENTITY_LIMIT,
) -> dict[str, Any]:
    if max_excerpts < 1:
        raise PackToolError("--max-excerpts must be at least 1.")
    if entity_limit < 0:
        raise PackToolError("--entity-limit cannot be negative.")
    pack_dir = pack_dir.resolve()
    pack = _read_pack_metadata(pack_dir)
    records = _read_ndjson(pack_dir / "documents.ndjson")
    sources, citation_by_url, expected = _citation_sources(pack_dir, pack, records, required_domains)
    brief_objective = objective or str(pack.get("objective") or "Review local DocPull context pack")
    entities = _extract_entities(records, citation_by_url, limit=max(1, entity_limit)) if entity_limit else []
    return {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "objective": brief_objective,
        "expected_domains": expected,
        "summary": {
            "source_count": len(sources),
            "record_count": len(records),
            "entity_count": len(entities),
            "total_tokens": sum(_safe_int(record.get("token_count")) for record in records),
        },
        "load_plan": sources[: min(len(sources), 12)],
        "key_excerpts": _select_key_excerpts(
            records,
            citation_by_url,
            objective=brief_objective,
            max_excerpts=max_excerpts,
        ),
        "entities": entities[:entity_limit] if entity_limit else [],
        "artifacts": {
            "citations": "citations.json",
            "entities": "entities.json",
            "brief_json": "research.brief.json",
            "brief_markdown": "RESEARCH_BRIEF.md",
        },
    }


def prepare_pack(
    pack_dir: Path,
    *,
    objective: str | None = None,
    search_queries: list[str] | None = None,
    default_search: bool = True,
    required_domains: list[str] | None = None,
    max_excerpts: int = DEFAULT_BRIEF_EXCERPTS,
    entity_limit: int = DEFAULT_BRIEF_ENTITY_LIMIT,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    graph: bool = True,
    graph_entity_limit: int = DEFAULT_GRAPH_ENTITY_LIMIT,
    markdown: bool = True,
    output: Path | None = None,
) -> dict[str, Any]:
    if max_excerpts < 1:
        raise PackToolError("--max-excerpts must be at least 1.")
    if entity_limit < 0:
        raise PackToolError("--entity-limit cannot be negative.")
    if search_limit < 1:
        raise PackToolError("--search-limit must be at least 1.")
    if graph_entity_limit < 1:
        raise PackToolError("--graph-entity-limit must be at least 1.")

    pack_dir = pack_dir.resolve()
    pack = _read_pack_metadata(pack_dir)
    prepare_objective = objective or str(pack.get("objective") or "Review local DocPull context pack")
    queries = _prepare_search_queries(
        search_queries,
        objective=prepare_objective,
        default_search=default_search,
    )

    artifacts: dict[str, str] = {}
    score_payload, source_scores_payload = _write_prepare_score_artifacts(
        pack_dir,
        required_domains=required_domains,
        artifacts=artifacts,
    )
    citations_payload = _write_prepare_citation_artifacts(
        pack_dir,
        required_domains=required_domains,
        markdown=markdown,
        artifacts=artifacts,
    )
    entities_payload = _write_prepare_entity_artifacts(
        pack_dir,
        required_domains=required_domains,
        citations_payload=citations_payload,
        entity_limit=entity_limit,
        markdown=markdown,
        artifacts=artifacts,
    )
    search_payloads = _write_prepare_search_artifacts(
        pack_dir,
        queries=queries,
        required_domains=required_domains,
        search_limit=search_limit,
        markdown=markdown,
        artifacts=artifacts,
    )
    brief_payload = _write_prepare_brief_artifacts(
        pack_dir,
        objective=prepare_objective,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
        markdown=markdown,
        artifacts=artifacts,
    )
    graph_payload = (
        _write_prepare_graph_artifacts(
            pack_dir,
            entity_limit=graph_entity_limit,
            markdown=markdown,
            artifacts=artifacts,
        )
        if graph
        else None
    )

    output_path = (output or (pack_dir / "pack.prepare.json")).resolve()
    artifacts["prepare"] = _artifact_ref(pack_dir, output_path)
    payload = {
        "schema_version": PREPARE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "objective": prepare_objective,
        "search_queries": queries,
        "expected_domains": citations_payload["expected_domains"],
        "summary": {
            "score": score_payload["score"],
            "grade": score_payload["grade"],
            "record_count": score_payload["summary"]["record_count"],
            "source_count": citations_payload["source_count"],
            "entity_count": entities_payload["entity_count"],
            "brief_excerpt_count": len(brief_payload["key_excerpts"]),
            "graph_node_count": (
                _safe_int(graph_payload.get("summary", {}).get("node_count"))
                if isinstance(graph_payload, dict)
                else 0
            ),
            "graph_edge_count": (
                _safe_int(graph_payload.get("summary", {}).get("edge_count"))
                if isinstance(graph_payload, dict)
                else 0
            ),
            "search_query_count": len(search_payloads),
            "search_result_count": sum(
                _safe_int(search_payload.get("result_count")) for search_payload in search_payloads
            ),
            "artifact_count": len(artifacts),
        },
        "artifacts": artifacts,
    }
    _write_json(output_path, payload)
    return payload


def _write_prepare_score_artifacts(
    pack_dir: Path,
    *,
    required_domains: list[str] | None,
    artifacts: dict[str, str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    score_payload = score_pack(pack_dir, required_domains=required_domains)
    score_path = pack_dir / "pack.score.json"
    _write_json(score_path, score_payload)
    artifacts["score"] = _artifact_ref(pack_dir, score_path)

    source_scores_payload = score_pack_sources(pack_dir, required_domains=required_domains)
    source_scores_path = pack_dir / "source.scores.json"
    _write_json(source_scores_path, source_scores_payload)
    artifacts["source_scores"] = _artifact_ref(pack_dir, source_scores_path)
    return score_payload, source_scores_payload


def _write_prepare_citation_artifacts(
    pack_dir: Path,
    *,
    required_domains: list[str] | None,
    markdown: bool,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    citations_payload = build_citation_map(pack_dir, required_domains=required_domains)
    citations_path = pack_dir / "citations.json"
    _write_json(citations_path, citations_payload)
    artifacts["citations"] = _artifact_ref(pack_dir, citations_path)
    if markdown:
        citations_md_path = pack_dir / "CITATIONS.md"
        citations_md_path.write_text(_citations_markdown(citations_payload), encoding="utf-8")
        artifacts["citations_markdown"] = _artifact_ref(pack_dir, citations_md_path)
    return citations_payload


def _write_prepare_entity_artifacts(
    pack_dir: Path,
    *,
    required_domains: list[str] | None,
    citations_payload: dict[str, Any],
    entity_limit: int,
    markdown: bool,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    if entity_limit:
        entities_payload = extract_pack_entities(
            pack_dir,
            required_domains=required_domains,
            limit=entity_limit,
        )
    else:
        entities_payload = _empty_entities_payload(pack_dir, citations_payload)
    entities_path = pack_dir / "entities.json"
    _write_json(entities_path, entities_payload)
    artifacts["entities"] = _artifact_ref(pack_dir, entities_path)
    if markdown:
        entities_md_path = pack_dir / "ENTITIES.md"
        entities_md_path.write_text(_entities_markdown(entities_payload), encoding="utf-8")
        artifacts["entities_markdown"] = _artifact_ref(pack_dir, entities_md_path)
    return entities_payload


def _write_prepare_search_artifacts(
    pack_dir: Path,
    *,
    queries: list[str],
    required_domains: list[str] | None,
    search_limit: int,
    markdown: bool,
    artifacts: dict[str, str],
) -> list[dict[str, Any]]:
    search_payloads = [
        search_pack(
            pack_dir,
            query,
            required_domains=required_domains,
            limit=search_limit,
        )
        for query in queries
    ]
    if not search_payloads:
        return []

    primary_search_path = pack_dir / "pack.search.json"
    _write_json(primary_search_path, search_payloads[0])
    artifacts["search"] = _artifact_ref(pack_dir, primary_search_path)

    search_collection = {
        "schema_version": SEARCH_COLLECTION_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "query_count": len(search_payloads),
        "result_count": sum(_safe_int(payload.get("result_count")) for payload in search_payloads),
        "queries": search_payloads,
    }
    search_collection_path = pack_dir / "pack.searches.json"
    _write_json(search_collection_path, search_collection)
    artifacts["searches"] = _artifact_ref(pack_dir, search_collection_path)
    if markdown:
        search_md_path = pack_dir / "SEARCH.md"
        search_md_path.write_text(_searches_markdown(search_collection), encoding="utf-8")
        artifacts["search_markdown"] = _artifact_ref(pack_dir, search_md_path)
    return search_payloads


def _write_prepare_brief_artifacts(
    pack_dir: Path,
    *,
    objective: str,
    required_domains: list[str] | None,
    max_excerpts: int,
    entity_limit: int,
    markdown: bool,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    brief_payload = build_research_brief(
        pack_dir,
        objective=objective,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
    )
    brief_json_path = pack_dir / "research.brief.json"
    _write_json(brief_json_path, brief_payload)
    artifacts["brief_json"] = _artifact_ref(pack_dir, brief_json_path)
    if markdown:
        brief_md_path = pack_dir / "RESEARCH_BRIEF.md"
        brief_md_path.write_text(_brief_markdown(brief_payload), encoding="utf-8")
        artifacts["brief_markdown"] = _artifact_ref(pack_dir, brief_md_path)
    return brief_payload


def _write_prepare_graph_artifacts(
    pack_dir: Path,
    *,
    entity_limit: int,
    markdown: bool,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    from .graph import build_graph

    graph_payload = build_graph(pack_dir, entity_limit=entity_limit, markdown=markdown)
    graph_artifacts = graph_payload.get("artifacts")
    if isinstance(graph_artifacts, dict):
        for key, value in graph_artifacts.items():
            if isinstance(value, str):
                artifacts[f"graph_{key}"] = value
    return graph_payload


def _empty_entities_payload(pack_dir: Path, citations_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": ENTITY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "expected_domains": citations_payload.get("expected_domains") or [],
        "source_count": _safe_int(citations_payload.get("source_count")),
        "record_count": _safe_int(citations_payload.get("record_count")),
        "entity_count": 0,
        "entities": [],
    }


def search_pack(
    pack_dir: Path,
    query: str,
    *,
    required_domains: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    if not query.strip():
        raise PackToolError("query must be non-empty.")
    if limit < 1:
        raise PackToolError("--limit must be at least 1.")
    pack_dir = pack_dir.resolve()
    pack = _read_pack_metadata(pack_dir)
    records = _read_ndjson(pack_dir / "documents.ndjson")
    sources, citation_by_url, expected = _citation_sources(pack_dir, pack, records, required_domains)
    results = _search_records(records, citation_by_url, query=query, limit=limit)
    citation_ids = {str(result["citation_id"]) for result in results}
    return {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "query": query,
        "expected_domains": expected,
        "source_count": len(sources),
        "record_count": len(records),
        "result_count": len(results),
        "results": results,
        "citations": [source for source in sources if source["citation_id"] in citation_ids],
    }


def diff_packs(old_pack_dir: Path, new_pack_dir: Path) -> dict[str, Any]:
    old_records = _records_by_url(_read_ndjson(old_pack_dir / "documents.ndjson"))
    new_records = _records_by_url(_read_ndjson(new_pack_dir / "documents.ndjson"))

    old_urls = set(old_records)
    new_urls = set(new_records)
    shared_urls = sorted(old_urls & new_urls)
    changed_urls = [url for url in shared_urls if _hashes(old_records[url]) != _hashes(new_records[url])]
    title_changed_urls = [
        url for url in shared_urls if _titles(old_records[url]) != _titles(new_records[url])
    ]
    path_changed_urls = [
        url for url in shared_urls if _output_paths(old_records[url]) != _output_paths(new_records[url])
    ]
    any_changed_urls = set(changed_urls) | set(title_changed_urls) | set(path_changed_urls)
    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "old_pack_dir": str(old_pack_dir.resolve()),
        "new_pack_dir": str(new_pack_dir.resolve()),
        "added_urls": sorted(new_urls - old_urls),
        "removed_urls": sorted(old_urls - new_urls),
        "changed_urls": changed_urls,
        "title_changed_urls": title_changed_urls,
        "path_changed_urls": path_changed_urls,
        "changed_details": [
            {
                "url": url,
                "content_changed": url in changed_urls,
                "title_changed": url in title_changed_urls,
                "path_changed": url in path_changed_urls,
                "old_hashes": _hashes(old_records[url]),
                "new_hashes": _hashes(new_records[url]),
                "old_titles": _titles(old_records[url]),
                "new_titles": _titles(new_records[url]),
                "old_paths": _output_paths(old_records[url]),
                "new_paths": _output_paths(new_records[url]),
            }
            for url in sorted(any_changed_urls)
        ],
        "unchanged_urls": [url for url in shared_urls if url not in any_changed_urls],
        "old_record_count": sum(len(items) for items in old_records.values()),
        "new_record_count": sum(len(items) for items in new_records.values()),
    }


def _read_json(path: Path, *, required: bool = True) -> Any:
    if not path.exists():
        if required:
            raise PackToolError(f"Missing required file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise PackToolError(f"Invalid JSON in {path}: {err}") from err


def _read_pack_metadata(pack_dir: Path) -> dict[str, Any]:
    metadata, _path = _read_pack_metadata_entry(pack_dir)
    return metadata


def _read_pack_metadata_entry(pack_dir: Path) -> tuple[dict[str, Any], Path | None]:
    direct = _read_json(pack_dir / "parallel.pack.json", required=False)
    if isinstance(direct, dict):
        return direct, pack_dir / "parallel.pack.json"
    candidates = sorted(pack_dir.glob("*.pack.json"))
    for candidate in candidates:
        parsed = _read_json(candidate, required=False)
        if isinstance(parsed, dict):
            return parsed, candidate
    return {}, None


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise PackToolError(f"Missing required file: {path}")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise PackToolError(f"Invalid NDJSON in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise PackToolError(f"Invalid NDJSON in {path} line {index}: expected object")
        records.append(value)
    return records


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _missing_declared_artifacts(pack_dir: Path, pack: dict[str, Any]) -> list[str]:
    artifacts = pack.get("artifacts")
    if not isinstance(artifacts, dict):
        return []
    missing: list[str] = []
    for path in artifacts.values():
        relative_path = _relative_pack_path(path)
        if relative_path and not (pack_dir / relative_path).exists():
            missing.append(relative_path)
    return sorted(set(missing))


def _missing_declared_sources(pack_dir: Path, pack: dict[str, Any]) -> list[str]:
    sources = pack.get("sources")
    if not isinstance(sources, list):
        return []
    missing: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        relative_path = _relative_pack_path(source.get("path"))
        if relative_path and not (pack_dir / relative_path).exists():
            missing.append(relative_path)
    return sorted(set(missing))


def _relative_pack_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        return None
    return value


def _citation_sources(
    pack_dir: Path,
    pack: dict[str, Any],
    records: list[dict[str, Any]],
    required_domains: list[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    expected = required_domains or _expected_domains(pack)
    entries: list[dict[str, Any]] = []
    entry_urls: set[str] = set()
    for entry in _pack_source_entries(pack, records):
        url = str(entry.get("url") or "")
        if not url or url in entry_urls:
            continue
        entries.append(entry)
        entry_urls.add(url)
    for index, record in enumerate(records, start=1):
        url = str(record.get("url") or "")
        if not url or url in entry_urls:
            continue
        entries.append({"index": index, "url": url, "title": str(record.get("title") or url)})
        entry_urls.add(url)

    scored = score_source_entries(entries, expected_domains=expected)
    records_by_url = _records_by_url(records)
    sources: list[dict[str, Any]] = []
    citation_by_url: dict[str, str] = {}
    for index, source in enumerate(scored, start=1):
        url = str(source.get("url") or "")
        if not url:
            continue
        citation_id = f"S{index}"
        citation_by_url[url] = citation_id
        url_records = records_by_url.get(url, [])
        headings = sorted(
            {
                str(record.get("chunk_heading") or "").strip()
                for record in url_records
                if str(record.get("chunk_heading") or "").strip()
            }
        )
        content_hashes = sorted(
            {
                str(record.get("content_hash") or "").strip()
                for record in url_records
                if str(record.get("content_hash") or "").strip()
            }
        )
        source_types = sorted({str(record.get("source_type") or "unknown") for record in url_records})
        fetched_at_values = sorted(
            str(record.get("fetched_at") or "") for record in url_records if record.get("fetched_at")
        )
        path = source.get("path")
        relative_path = _relative_pack_path(path)
        if relative_path and not (pack_dir / relative_path).exists():
            relative_path = None
        sources.append(
            {
                "citation_id": citation_id,
                "url": url,
                "title": str(source.get("title") or url),
                "domain": str(source.get("domain") or _domain(url)),
                "score": _safe_int(source.get("score")),
                "grade": str(source.get("grade") or "usable"),
                "reasons": list(source.get("reasons") or []),
                "path": relative_path,
                "record_count": len(url_records),
                "chunk_count": sum(1 for record in url_records if record.get("chunk_id")),
                "token_count": sum(_safe_int(record.get("token_count")) for record in url_records),
                "source_types": source_types,
                "headings": headings[:20],
                "content_hashes": content_hashes[:20],
                "first_fetched_at": fetched_at_values[0] if fetched_at_values else None,
                "latest_fetched_at": fetched_at_values[-1] if fetched_at_values else None,
            }
        )
    return sources, citation_by_url, expected


def _pack_source_entries(
    parallel_pack: dict[str, Any],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sources = parallel_pack.get("sources") if isinstance(parallel_pack, dict) else None
    if isinstance(sources, list):
        declared_entries = [source for source in sources if isinstance(source, dict) and source.get("url")]
        if declared_entries:
            return declared_entries

    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        url = str(record.get("url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        entries.append(
            {
                "index": index,
                "url": url,
                "title": str(record.get("title") or url),
            }
        )
    return entries


def _extract_entities(
    records: list[dict[str, Any]],
    citation_by_url: dict[str, str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    entities: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        content = str(record.get("content") or "")
        if not content:
            continue
        url = str(record.get("url") or "")
        citation_id = citation_by_url.get(url)
        for entity_type, pattern in _ENTITY_PATTERNS:
            for match in pattern.finditer(content):
                value = _clean_entity_value(match.group(0))
                if not _valid_entity(entity_type, value):
                    continue
                normalized = _normalize_entity_value(entity_type, value)
                key = (entity_type, normalized)
                item = entities.setdefault(
                    key,
                    {
                        "type": entity_type,
                        "value": value,
                        "normalized": normalized,
                        "count": 0,
                        "source_count": 0,
                        "citations": [],
                    },
                )
                item["count"] = _safe_int(item.get("count")) + 1
                citations = item["citations"]
                if isinstance(citations, list) and citation_id:
                    existing_ids = {str(citation.get("citation_id")) for citation in citations}
                    if citation_id not in existing_ids:
                        citations.append(
                            {
                                "citation_id": citation_id,
                                "url": url,
                                "title": str(record.get("title") or url),
                                "excerpt": _nearest_sentence(content, match.start(), match.end()),
                            }
                        )
                        item["source_count"] = len(citations)

    sorted_entities = sorted(
        entities.values(),
        key=lambda item: (
            -_safe_int(item.get("source_count")),
            -_safe_int(item.get("count")),
            str(item.get("type") or ""),
            str(item.get("normalized") or ""),
        ),
    )
    return sorted_entities[:limit]


def _select_key_excerpts(
    records: list[dict[str, Any]],
    citation_by_url: dict[str, str],
    *,
    objective: str,
    max_excerpts: int,
) -> list[dict[str, Any]]:
    objective_terms = set(_keywords(objective))
    seen: set[str] = set()
    excerpts: list[dict[str, Any]] = []
    for record in records:
        url = str(record.get("url") or "")
        citation_id = citation_by_url.get(url)
        if not citation_id:
            continue
        content = str(record.get("content") or "")
        best = _best_passage(content, objective_terms)
        if not best:
            continue
        normalized = re.sub(r"\W+", " ", best["excerpt"].lower()).strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        excerpts.append(
            {
                "citation_id": citation_id,
                "url": url,
                "title": str(record.get("title") or url),
                "chunk_id": record.get("chunk_id"),
                "chunk_heading": record.get("chunk_heading"),
                "score": best["score"],
                "excerpt": best["excerpt"],
            }
        )
    excerpts.sort(key=lambda item: (-_safe_int(item.get("score")), str(item.get("citation_id"))))
    return excerpts[:max_excerpts]


def _search_records(
    records: list[dict[str, Any]],
    citation_by_url: dict[str, str],
    *,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    terms = sorted(set(_keywords(query)))
    phrase = _clean_passage(query).lower()
    scored: list[dict[str, Any]] = []
    for record_index, record in enumerate(records, start=1):
        url = str(record.get("url") or "")
        citation_id = citation_by_url.get(url)
        if not citation_id:
            continue
        title = str(record.get("title") or url)
        heading = str(record.get("chunk_heading") or "")
        content = str(record.get("content") or "")
        score, matched_terms = _search_score(
            query_terms=terms,
            phrase=phrase,
            title=title,
            heading=heading,
            url=url,
            content=content,
        )
        if score <= 0:
            continue
        scored.append(
            {
                "record_index": record_index,
                "score": score,
                "citation_id": citation_id,
                "url": url,
                "title": title,
                "chunk_id": record.get("chunk_id"),
                "chunk_heading": record.get("chunk_heading"),
                "content_hash": record.get("content_hash"),
                "token_count": _safe_int(record.get("token_count")),
                "matched_terms": matched_terms,
                "excerpt": _best_search_excerpt(content or title, terms, phrase),
            }
        )

    scored.sort(
        key=lambda item: (
            -_safe_int(item.get("score")),
            str(item.get("citation_id") or ""),
            str(item.get("chunk_id") or ""),
            str(item.get("url") or ""),
        )
    )
    return [
        {
            "rank": rank,
            **result,
        }
        for rank, result in enumerate(scored[:limit], start=1)
    ]


def _prepare_search_queries(
    search_queries: list[str] | None,
    *,
    objective: str,
    default_search: bool,
) -> list[str]:
    raw_queries = search_queries if search_queries is not None else ([objective] if default_search else [])
    queries: list[str] = []
    seen: set[str] = set()
    for query in raw_queries:
        cleaned = query.strip()
        if not cleaned:
            continue
        normalized = cleaned.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        queries.append(cleaned)
    return queries


def _search_score(
    *,
    query_terms: list[str],
    phrase: str,
    title: str,
    heading: str,
    url: str,
    content: str,
) -> tuple[int, list[str]]:
    title_text = _clean_passage(title).lower()
    heading_text = _clean_passage(heading).lower()
    url_text = url.lower()
    content_text = _clean_passage(content).lower()
    matched_terms: list[str] = []
    score = 0
    for term in query_terms:
        title_hits = _term_count(title_text, term)
        heading_hits = _term_count(heading_text, term)
        url_hits = _term_count(url_text, term)
        content_hits = _term_count(content_text, term)
        if title_hits or heading_hits or url_hits or content_hits:
            matched_terms.append(term)
        score += min(title_hits, 3) * 8
        score += min(heading_hits, 3) * 6
        score += min(url_hits, 3) * 3
        score += min(content_hits, 10) * 2

    if phrase and len(phrase) >= 4:
        if phrase in title_text:
            score += 20
        if phrase in heading_text:
            score += 14
        if phrase in content_text:
            score += 10

    if len(matched_terms) > 1:
        score += len(matched_terms) * 3
    return score, matched_terms


def _term_count(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return len(re.findall(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE))


def _best_search_excerpt(content: str, query_terms: list[str], phrase: str) -> str:
    cleaned = _clean_passage(content)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    positions = [lowered.find(term) for term in query_terms if lowered.find(term) != -1]
    if phrase:
        phrase_position = lowered.find(phrase)
        if phrase_position != -1:
            positions.append(phrase_position)
    if not positions:
        return _truncate_text(cleaned, 520)
    position = min(positions)
    start = max(0, position - 180)
    end = min(len(cleaned), position + 420)
    if start:
        space = cleaned.find(" ", start)
        if 0 <= space < position:
            start = space + 1
    if end < len(cleaned):
        space = cleaned.rfind(" ", position, end)
        if space > position:
            end = space
    prefix = "..." if start else ""
    suffix = "..." if end < len(cleaned) else ""
    return _truncate_text(prefix + cleaned[start:end].strip(" ,.;:-") + suffix, 520)


def _best_passage(content: str, objective_terms: set[str]) -> dict[str, Any] | None:
    candidates = _candidate_passages(content)
    if not candidates:
        return None
    scored: list[dict[str, Any]] = []
    for passage in candidates:
        terms = set(_keywords(passage))
        overlap = len(objective_terms & terms) if objective_terms else 0
        score = min(8, max(1, len(passage) // 90)) + (overlap * 4)
        if any(marker in passage.lower() for marker in ("api", "pricing", "feature", "source", "citation")):
            score += 2
        scored.append({"score": score, "excerpt": _truncate_text(passage, 520)})
    scored.sort(key=lambda item: (-_safe_int(item.get("score")), str(item.get("excerpt"))))
    return scored[0]


def _candidate_passages(content: str) -> list[str]:
    cleaned = _clean_passage(content)
    candidates: list[str] = []
    for paragraph in re.split(r"\n\s*\n", cleaned):
        paragraph = _clean_passage(paragraph)
        if 80 <= len(paragraph) <= 1200:
            candidates.append(paragraph)
    if candidates:
        return candidates
    return [_truncate_text(cleaned, 520)] if cleaned else []


def _truncate_text(value: str, max_chars: int) -> str:
    text = value.strip()
    if max_chars < 4 or len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _clean_passage(value: str) -> str:
    text = _MARKDOWN_LINK_RE.sub(r"\1", value)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _nearest_sentence(content: str, start: int, end: int) -> str:
    left = max(content.rfind(".", 0, start), content.rfind("\n", 0, start))
    right_dot = content.find(".", end)
    right_newline = content.find("\n", end)
    right_candidates = [value for value in (right_dot, right_newline) if value != -1]
    sentence_start = 0 if left == -1 else left + 1
    sentence_end = min(right_candidates) + 1 if right_candidates else min(len(content), end + 220)
    return _truncate_text(_clean_passage(content[sentence_start:sentence_end]), 280)


def _keywords(value: str) -> list[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(value)]


def _clean_entity_value(value: str) -> str:
    return value.strip(" \t\r\n,.;:()[]{}\"'")


def _normalize_entity_value(entity_type: str, value: str) -> str:
    if entity_type in {"email", "technical_term", "organization"}:
        return re.sub(r"\s+", " ", value).strip().lower()
    if entity_type == "money":
        return re.sub(r"\s+", "", value).lower()
    if entity_type == "version":
        return re.sub(r"^(?:version\s*|v)", "", value, flags=re.IGNORECASE).strip().lower()
    return re.sub(r"\s+", " ", value).strip().lower()


def _valid_entity(entity_type: str, value: str) -> bool:
    if len(value) < 3:
        return False
    if entity_type == "version" and len(value) > 24:
        return False
    if entity_type == "technical_term" and value.upper() in {"API", "SDK", "MCP", "RAG", "LLM", "CLI"}:
        return False
    if entity_type == "organization":
        return not value.lower().startswith(("section title", "copy page"))
    return True


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _artifact_ref(pack_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(pack_dir).as_posix()
    except ValueError:
        return str(path)


def _citations_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Citation Map", "", f"Sources: {payload['source_count']}", ""]
    for source in payload.get("sources", []):
        if not isinstance(source, dict):
            continue
        title = source.get("title") or source.get("url")
        lines.append(
            f"- [{source.get('citation_id')}] {title} - {source.get('url')} "
            f"({source.get('grade')}, score {source.get('score')})"
        )
    return "\n".join(lines).rstrip() + "\n"


def _entities_markdown(payload: dict[str, Any]) -> str:
    lines = ["# Extracted Entities", "", f"Entities: {payload['entity_count']}", ""]
    current_type: str | None = None
    for entity in payload.get("entities", []):
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "unknown")
        if entity_type != current_type:
            current_type = entity_type
            lines.extend(["", f"## {entity_type.replace('_', ' ').title()}", ""])
        citations = ", ".join(
            str(citation.get("citation_id"))
            for citation in entity.get("citations", [])
            if isinstance(citation, dict) and citation.get("citation_id")
        )
        suffix = f" [{citations}]" if citations else ""
        lines.append(f"- {entity.get('value')} (count {entity.get('count')}){suffix}")
    return "\n".join(lines).rstrip() + "\n"


def _search_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pack Search Results",
        "",
        f"Query: {payload.get('query')}",
        f"Results: {payload.get('result_count', 0)}",
        "",
    ]
    for result in payload.get("results", []):
        if not isinstance(result, dict):
            continue
        matched_terms = ", ".join(str(term) for term in result.get("matched_terms", []))
        suffix = f" terms: {matched_terms}" if matched_terms else ""
        lines.extend(
            [
                f"## {result.get('rank')}. [{result.get('citation_id')}] {result.get('title')}",
                "",
                f"- URL: {result.get('url')}",
                f"- Score: {result.get('score')}{suffix}",
                "",
                str(result.get("excerpt") or ""),
                "",
            ]
        )
    if payload.get("citations"):
        lines.extend(["## Citations", ""])
        for source in payload["citations"]:
            if not isinstance(source, dict):
                continue
            lines.append(f"- [{source.get('citation_id')}] {source.get('url')}")
    return "\n".join(lines).rstrip() + "\n"


def _searches_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pack Search Results",
        "",
        f"Queries: {payload.get('query_count', 0)}",
        f"Results: {payload.get('result_count', 0)}",
        "",
    ]
    for search_payload in payload.get("queries", []):
        if not isinstance(search_payload, dict):
            continue
        lines.extend(
            [
                f"## Query: {search_payload.get('query')}",
                "",
                f"Results: {search_payload.get('result_count', 0)}",
                "",
            ]
        )
        for result in search_payload.get("results", []):
            if not isinstance(result, dict):
                continue
            matched_terms = ", ".join(str(term) for term in result.get("matched_terms", []))
            suffix = f" terms: {matched_terms}" if matched_terms else ""
            lines.extend(
                [
                    f"### {result.get('rank')}. [{result.get('citation_id')}] {result.get('title')}",
                    "",
                    f"- URL: {result.get('url')}",
                    f"- Score: {result.get('score')}{suffix}",
                    "",
                    str(result.get("excerpt") or ""),
                    "",
                ]
            )
        citations = search_payload.get("citations")
        if citations:
            lines.extend(["### Citations", ""])
            for source in citations:
                if not isinstance(source, dict):
                    continue
                lines.append(f"- [{source.get('citation_id')}] {source.get('url')}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _brief_markdown(payload: dict[str, Any]) -> str:
    summary_raw = payload.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    lines = [
        "# Local Research Brief",
        "",
        f"Objective: {payload.get('objective')}",
        f"Generated: {payload.get('generated_at')}",
        "",
        "## Coverage",
        "",
        f"- Sources: {summary.get('source_count', 0)}",
        f"- Records: {summary.get('record_count', 0)}",
        f"- Extracted entities: {summary.get('entity_count', 0)}",
        f"- Total tokens: {summary.get('total_tokens', 0)}",
        "",
        "## Load Plan",
        "",
    ]
    for source in payload.get("load_plan", []):
        if not isinstance(source, dict):
            continue
        lines.append(
            f"1. [{source.get('citation_id')}] {source.get('title')} - {source.get('url')} "
            f"({source.get('grade')}, score {source.get('score')})"
        )
    lines.extend(["", "## Key Excerpts", ""])
    for excerpt in payload.get("key_excerpts", []):
        if not isinstance(excerpt, dict):
            continue
        lines.append(f"- [{excerpt.get('citation_id')}] {excerpt.get('excerpt')}")
    if payload.get("entities"):
        lines.extend(["", "## Structured Signals", ""])
        for entity in payload["entities"]:
            if not isinstance(entity, dict):
                continue
            citations = ", ".join(
                str(citation.get("citation_id"))
                for citation in entity.get("citations", [])
                if isinstance(citation, dict) and citation.get("citation_id")
            )
            suffix = f" [{citations}]" if citations else ""
            lines.append(
                f"- {entity.get('type')}: {entity.get('value')} (count {entity.get('count')}){suffix}"
            )
    return "\n".join(lines).rstrip() + "\n"


def _records_by_url(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        url = str(record.get("url", ""))
        if not url:
            continue
        grouped.setdefault(url, []).append(record)
    return grouped


def _hashes(records: list[dict[str, Any]]) -> list[str]:
    return sorted(str(record.get("content_hash", "")) for record in records)


def _titles(records: list[dict[str, Any]]) -> list[str]:
    return sorted(str(record.get("title", "")) for record in records)


def _output_paths(records: list[dict[str, Any]]) -> list[str]:
    return sorted(str(record.get("output_path", "")) for record in records)


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _expected_domains(parallel_pack: dict[str, Any]) -> list[str]:
    request_options = _pack_request_options(parallel_pack)
    source_policy = request_options.get("source_policy") if isinstance(request_options, dict) else {}
    include_domains = source_policy.get("include_domains") if isinstance(source_policy, dict) else []
    return [str(domain).lower().removeprefix("www.") for domain in include_domains or []]


def _pack_request_options(pack: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(pack, dict):
        return {}
    request_options = pack.get("request_options")
    if isinstance(request_options, dict) and request_options:
        return request_options
    metadata = pack.get("metadata")
    if isinstance(metadata, dict):
        metadata_request_options = metadata.get("request_options")
        if isinstance(metadata_request_options, dict):
            return metadata_request_options
    return request_options if isinstance(request_options, dict) else {}


def _off_domain_urls(urls: list[str], expected_domains: list[str]) -> list[str]:
    if not expected_domains:
        return []
    off_domain: list[str] = []
    for url in urls:
        domain = _domain(url)
        if not any(domain == expected or domain.endswith(f".{expected}") for expected in expected_domains):
            off_domain.append(url)
    return off_domain


def _issue(
    code: str,
    message: str,
    *,
    severity: str = "warning",
    urls: list[str] | None = None,
    paths: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "severity": severity, "message": message}
    if urls:
        payload["urls"] = urls
    if paths:
        payload["paths"] = paths
    return payload


def _grade(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "needs_review"
    return "poor"


def _diff_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Context Pack Diff",
        "",
        f"Old: `{payload['old_pack_dir']}`",
        f"New: `{payload['new_pack_dir']}`",
        "",
        f"- Added URLs: {len(payload['added_urls'])}",
        f"- Removed URLs: {len(payload['removed_urls'])}",
        f"- Changed URLs: {len(payload['changed_urls'])}",
        f"- Unchanged URLs: {len(payload['unchanged_urls'])}",
    ]
    for heading, key in (
        ("Added", "added_urls"),
        ("Removed", "removed_urls"),
        ("Changed", "changed_urls"),
    ):
        if payload[key]:
            lines.extend(["", f"## {heading}", ""])
            lines.extend(f"- {url}" for url in payload[key])
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_pack_cli())
