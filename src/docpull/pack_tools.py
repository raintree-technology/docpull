"""Utilities for inspecting docpull context packs."""

from __future__ import annotations

import argparse
import heapq
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .change_events import build_change_events, write_change_events
from .contracts import canonical_sha256, stable_id
from .evidence import classify_source_authority, evidence_span_payload
from .output_contract import (
    VALIDATION_LEVELS,
    OutputContractError,
    ensure_agent_contract_sidecars,
    validate_pack_contract,
    validation_report_text,
)
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
COMPANY_BRAIN_SCHEMA_VERSION = 1
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


@dataclass
class _PackAnalysis:
    """One reusable read/index pass for a pack intelligence workflow."""

    pack_dir: Path
    metadata: dict[str, Any]
    metadata_path: Path | None
    records: list[dict[str, Any]]
    expected_domains: list[str]
    _source_scores: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)
    _citation_data: tuple[list[dict[str, Any]], dict[str, str], list[str]] | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _record_citations: dict[str, str] | None = field(default=None, init=False, repr=False)
    _entities_by_limit: dict[int, list[dict[str, Any]]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def source_scores(self) -> list[dict[str, Any]]:
        if self._source_scores is None:
            entries = _pack_source_entries(self.metadata, self.records)
            self._source_scores = score_source_entries(entries, expected_domains=self.expected_domains)
        return self._source_scores

    def citation_data(self) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
        if self._citation_data is None:
            self._citation_data = _citation_sources(
                self.pack_dir,
                self.metadata,
                self.records,
                self.expected_domains,
            )
        return self._citation_data

    def record_citations(self) -> dict[str, str]:
        if self._record_citations is None:
            sources, _citation_by_url, _expected = self.citation_data()
            self._record_citations = _record_citation_lookup(sources)
        return self._record_citations

    def entities(self, *, limit: int) -> list[dict[str, Any]]:
        cached = self._entities_by_limit.get(limit)
        if cached is None:
            _sources, citation_by_url, _expected = self.citation_data()
            cached = _extract_entities(
                self.records,
                citation_by_url,
                self.record_citations(),
                limit=limit,
            )
            self._entities_by_limit[limit] = cached
        return cached


def _analyze_pack(pack_dir: Path, required_domains: list[str] | None) -> _PackAnalysis:
    root = pack_dir.resolve()
    metadata, metadata_path = _read_pack_metadata_entry(root)
    records = _read_pack_records(root)
    return _PackAnalysis(
        pack_dir=root,
        metadata=metadata,
        metadata_path=metadata_path,
        records=records,
        expected_domains=required_domains or _expected_domains(metadata),
    )


def _analysis_from_local_pack(pack: Any, required_domains: list[str] | None) -> _PackAnalysis:
    """Reuse an already loaded LocalPack without another corpus read."""
    metadata = pack.metadata if isinstance(pack.metadata, dict) else {}
    return _PackAnalysis(
        pack_dir=pack.pack_dir,
        metadata=metadata,
        metadata_path=pack.metadata_path,
        records=[record.model_dump(mode="json", exclude_none=True) for record in pack.documents],
        expected_domains=required_domains or _expected_domains(metadata),
    )


def create_pack_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull pack",
        description="Inspect, score, and diff docpull context packs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a pack against the output contract")
    validate.add_argument("pack_dir", type=Path, help="Context pack directory")
    validate.add_argument("--level", choices=VALIDATION_LEVELS, default="raw")
    validate.add_argument("--format", choices=["text", "json"], default="text")
    validate.add_argument("--output", type=Path, help="Optional validation report output path")

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
    audit.add_argument("--json", action="store_true", dest="json_output", help="Print audit JSON")
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
    audit.add_argument("--redaction", action="store_true", help="Scan pack for sensitive content patterns")
    audit.add_argument("--redaction-policy", type=Path, help="Optional redaction policy YAML/JSON")
    audit.add_argument(
        "--redaction-backend",
        choices=["regex", "presidio", "hybrid"],
        help="Override redaction backend from policy",
    )

    publish = subparsers.add_parser("publish", help="Write agent-readable pack publishing artifacts")
    publish.add_argument("pack_dir", type=Path, help="Context pack directory")
    publish.add_argument("--target", choices=["agent-docs"], default="agent-docs")

    basis = subparsers.add_parser("basis", help="Write evidence basis artifacts for a context pack")
    basis.add_argument("pack_dir", type=Path, help="Context pack directory")
    basis.add_argument("--claim", help="Claim or objective to ground against local evidence")
    basis.add_argument("--claim-path", default="pack.objective", help="Claim path label")
    basis.add_argument("--limit", type=int, default=5, help="Maximum evidence records")
    basis.add_argument(
        "--min-supported-ratio",
        type=float,
        default=0.80,
        help="Minimum supported evidence ratio for basis.report.json",
    )
    basis.add_argument("--output", type=Path, help="Basis NDJSON output path")

    redact = subparsers.add_parser("redact", help="Write a redacted copy of a context pack")
    redact.add_argument("pack_dir", type=Path, help="Context pack directory")
    redact.add_argument("--policy", type=Path, help="Redaction policy YAML/JSON")
    redact.add_argument("--backend", choices=["regex", "presidio", "hybrid"], help="Override policy backend")
    redact.add_argument("--output-dir", "-o", type=Path, required=True)

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
        "--eval-grade",
        action="store_true",
        help="Write rights, provenance, citation index, and pack card artifacts",
    )
    prepare.add_argument(
        "--require-domain",
        action="append",
        dest="required_domains",
        default=[],
        help="Expected source domain or suffix. Repeat as needed.",
    )
    prepare.add_argument("--no-markdown", action="store_true", help="Write JSON artifacts only")

    for command_name in ("intelligence-bundle", "company-brain"):
        bundle = subparsers.add_parser(
            command_name,
            help=(
                "Write deterministic intelligence.bundle.v1 tracker import"
                if command_name == "intelligence-bundle"
                else "Compatibility alias for intelligence-bundle"
            ),
        )
        bundle.add_argument("pack_dir", type=Path, help="Context pack directory")
        bundle.add_argument("--objective")
        bundle.add_argument("--market")
        bundle.add_argument("--search-query", action="append", dest="search_queries", default=[])
        bundle.add_argument("--no-search", action="store_true")
        bundle.add_argument("--require-domain", action="append", dest="required_domains", default=[])
        bundle.add_argument("--max-excerpts", type=int, default=DEFAULT_BRIEF_EXCERPTS)
        bundle.add_argument("--entity-limit", type=int, default=DEFAULT_BRIEF_ENTITY_LIMIT)
        bundle.add_argument("--search-limit", type=int, default=DEFAULT_SEARCH_LIMIT)
        bundle.add_argument("--output", type=Path)
        bundle.add_argument("--markdown", type=Path)

    return parser


def run_pack_cli(argv: list[str] | None = None) -> int:
    parser = create_pack_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.command == "validate":
            payload = validate_pack_contract(args.pack_dir, level=args.level)
            if args.output:
                _write_json(args.output, payload)
            if args.format == "json":
                console.print_json(data=payload)
            else:
                console.print(validation_report_text(payload).rstrip())
            return 0 if payload["status"] == "pass" else 1
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
            from .redaction import scan_sensitive_content

            pack_dir = args.pack_dir.resolve()
            audit_json_path = args.output or (pack_dir / "pack.audit.json")
            audit_markdown_path = args.markdown or (pack_dir / "PACK_AUDIT.md")
            payload = audit_pack(
                pack_dir,
                required_domains=args.required_domains,
                fail_under=args.fail_under,
                json_path=audit_json_path,
                markdown_path=audit_markdown_path,
            )
            if args.redaction:
                payload["redaction"] = scan_sensitive_content(
                    pack_dir,
                    policy_path=args.redaction_policy,
                    backend=args.redaction_backend,
                )
                _write_json(audit_json_path, payload)
            if args.json_output:
                console.print_json(data=payload)
            else:
                console.print(
                    f"[green]Pack audit:[/green] {payload['score']}/100 ({payload['grade']}) "
                    f"-> {payload['artifacts']['json']}"
                )
            return 0
        if args.command == "publish":
            from .agent_publish import publish_agent_docs

            payload = publish_agent_docs(args.pack_dir, target=args.target)
            console.print(f"[green]Pack published:[/green] {payload['artifacts']['agent_context']}")
            return 0
        if args.command == "basis":
            from .basis import build_pack_basis, write_basis

            pack_dir = args.pack_dir.resolve()
            pack = _read_pack_metadata(pack_dir)
            claim = args.claim or str(pack.get("objective") or "Review local DocPull context pack")
            output = args.output or (pack_dir / "basis.ndjson")
            records = build_pack_basis(
                pack_dir,
                claim_path=args.claim_path,
                claim=claim,
                limit=args.limit,
                producer="docpull.pack.basis",
            )
            payload = write_basis(output, records, min_supported_ratio=args.min_supported_ratio)
            summary = payload["summary"]
            console.print(
                "[green]Pack basis:[/green] "
                f"{summary['supported_count']}/{summary['basis_count']} supported -> {output}"
            )
            return 0
        if args.command == "redact":
            from .redaction import redact_pack

            payload = redact_pack(
                args.pack_dir,
                policy_path=args.policy,
                output_dir=args.output_dir,
                backend=args.backend,
            )
            console.print(
                f"[green]Pack redacted:[/green] {payload['output_dir']} findings={payload['finding_count']}"
            )
            return 0
        if args.command == "diff":
            payload = diff_packs(args.old_pack_dir, args.new_pack_dir)
            output = args.output or (args.new_pack_dir / "pack.diff.json")
            _write_json(output, payload)
            write_change_events(
                args.new_pack_dir / "change.events.jsonl",
                list(payload.get("change_events") or []),
            )
            semantic = payload.get("semantic_diff")
            if isinstance(semantic, dict):
                _write_json(args.new_pack_dir / "semantic.diff.json", semantic)
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
                eval_grade=args.eval_grade,
                markdown=not args.no_markdown,
                output=args.output,
            )
            console.print(
                "[green]Prepared pack:[/green] "
                f"{payload['summary']['artifact_count']} artifacts -> "
                f"{payload['artifacts']['prepare']}"
            )
            return 0
        if args.command in {"intelligence-bundle", "company-brain"}:
            if args.command == "company-brain":
                payload = build_company_brain_bundle(
                    args.pack_dir,
                    objective=args.objective,
                    market=args.market,
                    search_queries=[] if args.no_search else (args.search_queries or None),
                    default_search=not args.no_search,
                    required_domains=args.required_domains,
                    max_excerpts=args.max_excerpts,
                    entity_limit=args.entity_limit,
                    search_limit=args.search_limit,
                    output=args.output,
                    markdown_path=args.markdown,
                )
            else:
                payload = build_intelligence_bundle(
                    args.pack_dir,
                    objective=args.objective,
                    market=args.market,
                    search_queries=[] if args.no_search else (args.search_queries or None),
                    default_search=not args.no_search,
                    required_domains=args.required_domains,
                    max_excerpts=args.max_excerpts,
                    entity_limit=args.entity_limit,
                    search_limit=args.search_limit,
                    output=args.output,
                    markdown_path=args.markdown,
                )
            console.print(
                f"[green]Intelligence bundle:[/green] {payload['bundle_id']} -> "
                f"{payload['artifacts']['intelligence_bundle']}"
            )
            return 0
        parser.error(f"Unknown command: {args.command}")
    except (PackToolError, OutputContractError) as err:
        console.print("[red]Pack error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Pack command failed:[/red] " + escape(str(err)))
        return 1
    return 1


def score_pack(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    _analysis: _PackAnalysis | None = None,
) -> dict[str, Any]:
    analysis = _analysis or _analyze_pack(pack_dir, required_domains)
    pack_dir = analysis.pack_dir
    manifest = _read_json(pack_dir / "corpus.manifest.json", required=False) or {}
    parallel_pack = analysis.metadata
    metadata_path = analysis.metadata_path
    metadata_label = metadata_path.name if metadata_path else "pack metadata"
    records = analysis.records

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

    expected = analysis.expected_domains
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


def score_pack_sources(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    _analysis: _PackAnalysis | None = None,
) -> dict[str, Any]:
    analysis = _analysis or _analyze_pack(pack_dir, required_domains)
    scored = analysis.source_scores()
    return {
        "schema_version": SOURCE_SCORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(analysis.pack_dir),
        "expected_domains": analysis.expected_domains,
        "source_count": len(scored),
        "sources": scored,
    }


def build_citation_map(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    _analysis: _PackAnalysis | None = None,
) -> dict[str, Any]:
    analysis = _analysis or _analyze_pack(pack_dir, required_domains)
    sources, _citation_by_url, expected = analysis.citation_data()
    return {
        "schema_version": CITATION_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(analysis.pack_dir),
        "expected_domains": expected,
        "source_count": len(sources),
        "record_count": len(analysis.records),
        "sources": sources,
    }


def extract_pack_entities(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    limit: int = DEFAULT_ENTITY_LIMIT,
    _analysis: _PackAnalysis | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise PackToolError("--limit must be at least 1.")
    analysis = _analysis or _analyze_pack(pack_dir, required_domains)
    sources, _citation_by_url, expected = analysis.citation_data()
    entities = analysis.entities(limit=limit)
    return {
        "schema_version": ENTITY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(analysis.pack_dir),
        "expected_domains": expected,
        "source_count": len(sources),
        "record_count": len(analysis.records),
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
    _analysis: _PackAnalysis | None = None,
) -> dict[str, Any]:
    if max_excerpts < 1:
        raise PackToolError("--max-excerpts must be at least 1.")
    if entity_limit < 0:
        raise PackToolError("--entity-limit cannot be negative.")
    analysis = _analysis or _analyze_pack(pack_dir, required_domains)
    sources, citation_by_url, expected = analysis.citation_data()
    brief_objective = objective or str(
        analysis.metadata.get("objective") or "Review local DocPull context pack"
    )
    record_citation_by_key = analysis.record_citations()
    entities = analysis.entities(limit=max(1, entity_limit)) if entity_limit else []
    return {
        "schema_version": BRIEF_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(analysis.pack_dir),
        "objective": brief_objective,
        "expected_domains": expected,
        "summary": {
            "source_count": len(sources),
            "record_count": len(analysis.records),
            "entity_count": len(entities),
            "total_tokens": sum(_safe_int(record.get("token_count")) for record in analysis.records),
        },
        "load_plan": sources[: min(len(sources), 12)],
        "key_excerpts": _select_key_excerpts(
            analysis.records,
            citation_by_url,
            record_citation_by_key,
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


def build_intelligence_bundle(
    pack_dir: Path,
    *,
    objective: str | None = None,
    market: str | None = None,
    search_queries: list[str] | None = None,
    default_search: bool = True,
    required_domains: list[str] | None = None,
    max_excerpts: int = DEFAULT_BRIEF_EXCERPTS,
    entity_limit: int = DEFAULT_BRIEF_ENTITY_LIMIT,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    output: Path | None = None,
    compatibility_output: Path | None = None,
    markdown_path: Path | None = None,
) -> dict[str, Any]:
    """Write a deterministic ``intelligence.bundle.v1`` tracker import."""
    if max_excerpts < 1:
        raise PackToolError("--max-excerpts must be at least 1.")
    if entity_limit < 0:
        raise PackToolError("--entity-limit cannot be negative.")
    if search_limit < 1:
        raise PackToolError("--search-limit must be at least 1.")

    analysis = _analyze_pack(pack_dir, required_domains)
    pack_dir = analysis.pack_dir
    pack = analysis.metadata
    brain_objective = objective or str(pack.get("objective") or "Review local DocPull context pack")
    workspace_label = (market or str(pack.get("market") or "")).strip() or "Company Brain workspace"
    queries = _prepare_search_queries(
        search_queries,
        objective=brain_objective,
        default_search=default_search,
    )

    artifacts: dict[str, str] = {}
    score_payload, source_scores_payload = _write_prepare_score_artifacts(
        pack_dir,
        required_domains=required_domains,
        artifacts=artifacts,
        analysis=analysis,
    )
    citations_payload = _write_prepare_citation_artifacts(
        pack_dir,
        required_domains=required_domains,
        markdown=True,
        artifacts=artifacts,
        analysis=analysis,
    )
    entities_payload = _write_prepare_entity_artifacts(
        pack_dir,
        required_domains=required_domains,
        citations_payload=citations_payload,
        entity_limit=entity_limit,
        markdown=True,
        artifacts=artifacts,
        analysis=analysis,
    )
    search_payloads = _write_prepare_search_artifacts(
        pack_dir,
        queries=queries,
        required_domains=required_domains,
        search_limit=search_limit,
        markdown=True,
        artifacts=artifacts,
        analysis=analysis,
    )
    brief_payload = _write_prepare_brief_artifacts(
        pack_dir,
        objective=brain_objective,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
        markdown=True,
        artifacts=artifacts,
        analysis=analysis,
    )

    records = _read_pack_records(pack_dir)
    source_snapshots = _company_brain_source_snapshots(citations_payload)
    claims = _company_brain_claims(brief_payload)
    entities = _company_brain_entities(entities_payload)
    signals = _company_brain_signals(search_payloads)
    bundle_path = (output or (pack_dir / "intelligence.bundle.v1.json")).resolve()
    compatibility_path = (
        compatibility_output.resolve()
        if compatibility_output
        else (pack_dir / "company_brain.bundle.json").resolve()
    )
    summary_path = (markdown_path or (pack_dir / "COMPANY_BRAIN.md")).resolve()
    artifacts["intelligence_bundle"] = _artifact_ref(pack_dir, bundle_path)
    artifacts["bundle"] = _artifact_ref(pack_dir, compatibility_path)
    artifacts["company_brain_compatibility_alias"] = _artifact_ref(pack_dir, compatibility_path)
    artifacts["company_brain_markdown"] = _artifact_ref(pack_dir, summary_path)
    content_hashes = sorted(
        str(record.get("content_hash")) for record in records if record.get("content_hash")
    )
    pack_identity = {
        "pack_id": stable_id("pack", {"content_hashes": content_hashes}),
        "content_hash": canonical_sha256(content_hashes),
        "record_count": len(records),
    }
    run_seed = {
        "pack_id": pack_identity["pack_id"],
        "objective": brain_objective,
        "market": workspace_label,
        "queries": queries,
    }
    run_identity = {
        "run_id": stable_id("run", run_seed),
        "scheduler": None,
        "replay": {
            "objective": brain_objective,
            "market": workspace_label,
            "search_queries": queries,
            "required_domains": sorted(required_domains or []),
            "max_excerpts": max_excerpts,
            "entity_limit": entity_limit,
            "search_limit": search_limit,
        },
    }
    canonical_snapshots = _intelligence_source_snapshots(source_snapshots, records)
    observations = _intelligence_observations(claims, records, citations_payload)
    document_versions = _intelligence_document_versions(records)
    change_candidates = _intelligence_change_candidates(pack_dir)
    bundle_core = {
        "contract_version": "intelligence.bundle.v1",
        "schema_version": COMPANY_BRAIN_SCHEMA_VERSION,
        "pack_identity": pack_identity,
        "run_identity": run_identity,
        "workspace": {
            "name": workspace_label,
            "market": workspace_label,
            "objective": brain_objective,
        },
        "summary": {
            "score": score_payload["score"],
            "grade": score_payload["grade"],
            "source_count": citations_payload["source_count"],
            "record_count": citations_payload["record_count"],
            "entity_count": len(entities),
            "claim_count": len(claims),
            "search_query_count": len(search_payloads),
            "search_result_count": sum(
                _safe_int(search_payload.get("result_count")) for search_payload in search_payloads
            ),
            "expected_domains": citations_payload["expected_domains"],
        },
        "source_snapshots": canonical_snapshots,
        "document_versions": document_versions,
        "observations": observations,
        "change_candidates": change_candidates,
        "warnings": _intelligence_warnings(score_payload, source_scores_payload),
        "artifacts": artifacts,
    }
    bundle_hash = canonical_sha256(bundle_core)
    payload = {
        **bundle_core,
        "bundle_id": f"bundle_{bundle_hash[:24]}",
        "bundle_hash": bundle_hash,
        # Compatibility envelope for company_brain.bundle.json readers.
        "pack_dir": str(pack_dir),
        "records": {
            "sources": citations_payload["sources"],
            "source_snapshots": source_snapshots,
            "entities": entities,
            "source_supported_claims": claims,
            "signals": signals,
            "brief": {
                "objective": brief_payload["objective"],
                "summary": brief_payload["summary"],
                "key_excerpts": brief_payload["key_excerpts"],
            },
            "gate_inputs": {
                "pack_score": _deterministic_contract_value(score_payload),
                "source_scores": _deterministic_contract_value(source_scores_payload),
                "required_domains": citations_payload["expected_domains"],
                "claim_policy": "Every promoted claim must carry a citation_id and source URL.",
            },
        },
        "agent_run_seed": {
            "trigger": "docpull.pack.intelligence-bundle",
            "tool": "docpull",
            "status": "ready_for_control_plane_import",
            "policy_checks": [
                "source_domain_boundary",
                "citation_coverage",
                "claim_source_support",
                "pack_integrity_score",
            ],
        },
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(_company_brain_markdown(payload), encoding="utf-8")
    _write_json(bundle_path, payload)
    if compatibility_path != bundle_path:
        _write_json(compatibility_path, payload)
    return payload


def build_company_brain_bundle(
    pack_dir: Path,
    *,
    objective: str | None = None,
    market: str | None = None,
    search_queries: list[str] | None = None,
    default_search: bool = True,
    required_domains: list[str] | None = None,
    max_excerpts: int = DEFAULT_BRIEF_EXCERPTS,
    entity_limit: int = DEFAULT_BRIEF_ENTITY_LIMIT,
    search_limit: int = DEFAULT_SEARCH_LIMIT,
    output: Path | None = None,
    markdown_path: Path | None = None,
) -> dict[str, Any]:
    """Compatibility alias for :func:`build_intelligence_bundle`."""

    return build_intelligence_bundle(
        pack_dir,
        objective=objective,
        market=market,
        search_queries=search_queries,
        default_search=default_search,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
        search_limit=search_limit,
        compatibility_output=output,
        markdown_path=markdown_path,
    )


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
    eval_grade: bool = False,
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

    analysis = _analyze_pack(pack_dir, required_domains)
    pack_dir = analysis.pack_dir
    pack = analysis.metadata
    from .pack_reader import _local_pack_from_records, load_pack

    local_pack = (
        _local_pack_from_records(
            pack_dir,
            analysis.records,
            metadata=analysis.metadata,
            metadata_path=analysis.metadata_path,
        )
        if (pack_dir / "documents.ndjson").exists()
        else load_pack(pack_dir)
    )
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
        analysis=analysis,
    )
    citations_payload = _write_prepare_citation_artifacts(
        pack_dir,
        required_domains=required_domains,
        markdown=markdown,
        artifacts=artifacts,
        analysis=analysis,
    )
    entities_payload = _write_prepare_entity_artifacts(
        pack_dir,
        required_domains=required_domains,
        citations_payload=citations_payload,
        entity_limit=entity_limit,
        markdown=markdown,
        artifacts=artifacts,
        analysis=analysis,
    )
    search_payloads = _write_prepare_search_artifacts(
        pack_dir,
        queries=queries,
        required_domains=required_domains,
        search_limit=search_limit,
        markdown=markdown,
        artifacts=artifacts,
        analysis=analysis,
    )
    brief_payload = _write_prepare_brief_artifacts(
        pack_dir,
        objective=prepare_objective,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
        markdown=markdown,
        artifacts=artifacts,
        analysis=analysis,
    )
    basis_payload = _write_prepare_basis_artifacts(
        pack_dir,
        objective=prepare_objective,
        max_excerpts=max_excerpts,
        artifacts=artifacts,
        local_pack=local_pack,
    )
    graph_payload = (
        _write_prepare_graph_artifacts(
            pack_dir,
            entity_limit=graph_entity_limit,
            markdown=markdown,
            artifacts=artifacts,
            analysis=analysis,
            local_pack=local_pack,
        )
        if graph
        else None
    )
    _write_prepare_agent_contract_artifacts(
        pack_dir,
        required_domains=required_domains,
        citations_payload=citations_payload,
        artifacts=artifacts,
        analysis=analysis,
    )
    eval_grade_payload = None
    if eval_grade:
        from .eval_grade import prepare_eval_grade_pack

        eval_grade_payload = prepare_eval_grade_pack(
            pack_dir,
            required_domains=required_domains,
            markdown=markdown,
            artifacts=artifacts,
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
            "basis_count": basis_payload["summary"]["basis_count"],
            "basis_supported_ratio": basis_payload["summary"]["supported_ratio"],
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
            "eval_grade_artifact_count": (
                len(eval_grade_payload.get("artifacts", {})) if isinstance(eval_grade_payload, dict) else 0
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
    analysis: _PackAnalysis,
) -> tuple[dict[str, Any], dict[str, Any]]:
    score_payload = score_pack(
        pack_dir,
        required_domains=required_domains,
        _analysis=analysis,
    )
    score_path = pack_dir / "pack.score.json"
    _write_json(score_path, score_payload)
    artifacts["score"] = _artifact_ref(pack_dir, score_path)

    source_scores_payload = score_pack_sources(
        pack_dir,
        required_domains=required_domains,
        _analysis=analysis,
    )
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
    analysis: _PackAnalysis,
) -> dict[str, Any]:
    citations_payload = build_citation_map(
        pack_dir,
        required_domains=required_domains,
        _analysis=analysis,
    )
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
    analysis: _PackAnalysis,
) -> dict[str, Any]:
    if entity_limit:
        entities_payload = extract_pack_entities(
            pack_dir,
            required_domains=required_domains,
            limit=entity_limit,
            _analysis=analysis,
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
    analysis: _PackAnalysis,
) -> list[dict[str, Any]]:
    search_payloads = [
        search_pack(
            pack_dir,
            query,
            required_domains=required_domains,
            limit=search_limit,
            _analysis=analysis,
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
    analysis: _PackAnalysis,
) -> dict[str, Any]:
    brief_payload = build_research_brief(
        pack_dir,
        objective=objective,
        required_domains=required_domains,
        max_excerpts=max_excerpts,
        entity_limit=entity_limit,
        _analysis=analysis,
    )
    brief_json_path = pack_dir / "research.brief.json"
    _write_json(brief_json_path, brief_payload)
    artifacts["brief_json"] = _artifact_ref(pack_dir, brief_json_path)
    if markdown:
        brief_md_path = pack_dir / "RESEARCH_BRIEF.md"
        brief_md_path.write_text(_brief_markdown(brief_payload), encoding="utf-8")
        artifacts["brief_markdown"] = _artifact_ref(pack_dir, brief_md_path)
    return brief_payload


def _write_prepare_basis_artifacts(
    pack_dir: Path,
    *,
    objective: str,
    max_excerpts: int,
    artifacts: dict[str, str],
    local_pack: Any,
) -> dict[str, Any]:
    from .basis import build_pack_basis, write_basis

    basis_path = pack_dir / "basis.ndjson"
    records = build_pack_basis(
        pack_dir,
        claim_path="pack.objective",
        claim=objective,
        limit=max(1, max_excerpts),
        producer="docpull.pack.prepare",
        _pack=local_pack,
    )
    payload = write_basis(basis_path, records)
    artifacts["basis"] = _artifact_ref(pack_dir, basis_path)
    artifacts["basis_report"] = "basis.report.json"
    artifacts["basis_markdown"] = "BASIS.md"
    return payload


def _write_prepare_agent_contract_artifacts(
    pack_dir: Path,
    *,
    required_domains: list[str] | None,
    citations_payload: dict[str, Any],
    artifacts: dict[str, str],
    analysis: _PackAnalysis,
) -> None:
    from .eval_grade import build_citation_index
    from .local_workflows import audit_pack

    for key, path in ensure_agent_contract_sidecars(pack_dir, records=analysis.records).items():
        artifacts[key] = _artifact_ref(pack_dir, path)

    audit_payload = audit_pack(
        pack_dir,
        required_domains=required_domains,
        _analysis=analysis,
    )
    audit_artifacts = audit_payload.get("artifacts")
    if isinstance(audit_artifacts, dict):
        for key, value in audit_artifacts.items():
            if isinstance(value, str):
                artifacts[f"audit_{key}"] = value

    citation_index_path = pack_dir / "citation.index.json"
    _write_json(
        citation_index_path,
        build_citation_index(
            pack_dir,
            records=analysis.records,
            citations_payload=citations_payload,
        ),
    )
    artifacts["citation_index"] = _artifact_ref(pack_dir, citation_index_path)


def _write_prepare_graph_artifacts(
    pack_dir: Path,
    *,
    entity_limit: int,
    markdown: bool,
    artifacts: dict[str, str],
    analysis: _PackAnalysis,
    local_pack: Any,
) -> dict[str, Any]:
    from .graph import build_graph

    graph_payload = build_graph(
        pack_dir,
        entity_limit=entity_limit,
        markdown=markdown,
        _analysis=analysis,
        _pack=local_pack,
    )
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


def _company_brain_source_snapshots(citations_payload: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for index, source in enumerate(citations_payload.get("sources", []), start=1):
        if not isinstance(source, dict):
            continue
        snapshots.append(
            {
                "source_id": f"source_{index:03d}",
                "source_snapshot_id": f"source_snapshot_{index:03d}",
                "citation_id": source.get("citation_id"),
                "title": source.get("title"),
                "url": source.get("url"),
                "domain": source.get("domain"),
                "path": source.get("path"),
                "score": source.get("score"),
                "grade": source.get("grade"),
                "record_count": source.get("record_count"),
                "chunk_count": source.get("chunk_count"),
                "token_count": source.get("token_count"),
                "content_hashes": source.get("content_hashes") or [],
                "latest_fetched_at": source.get("latest_fetched_at"),
            }
        )
    return snapshots


def _company_brain_entities(entities_payload: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, entity in enumerate(entities_payload.get("entities", []), start=1):
        if not isinstance(entity, dict):
            continue
        records.append(
            {
                "entity_id": f"entity_{index:03d}",
                "type": entity.get("type"),
                "value": entity.get("value"),
                "normalized": entity.get("normalized"),
                "count": entity.get("count"),
                "source_count": entity.get("source_count"),
                "evidence": entity.get("citations") or [],
            }
        )
    return records


def _company_brain_claims(brief_payload: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for index, excerpt in enumerate(brief_payload.get("key_excerpts", []), start=1):
        if not isinstance(excerpt, dict):
            continue
        citation_id = excerpt.get("citation_id")
        url = excerpt.get("url")
        text = str(excerpt.get("excerpt") or "").strip()
        if not citation_id or not url or not text:
            continue
        claims.append(
            {
                "claim_id": f"claim_{index:03d}",
                "status": "source_supported",
                "text": text,
                "citation_id": citation_id,
                "record_citation_id": excerpt.get("record_citation_id"),
                "url": url,
                "title": excerpt.get("title"),
                "chunk_id": excerpt.get("chunk_id"),
                "chunk_heading": excerpt.get("chunk_heading"),
                "support_score": excerpt.get("score"),
            }
        )
    return claims


def _company_brain_signals(search_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    signal_index = 1
    for search_payload in search_payloads:
        query = search_payload.get("query")
        for result in search_payload.get("results", []):
            if not isinstance(result, dict):
                continue
            signals.append(
                {
                    "signal_id": f"signal_{signal_index:03d}",
                    "type": "local_pack_search_result",
                    "query": query,
                    "rank": result.get("rank"),
                    "score": result.get("score"),
                    "citation_id": result.get("citation_id"),
                    "record_citation_id": result.get("record_citation_id"),
                    "url": result.get("url"),
                    "title": result.get("title"),
                    "matched_terms": result.get("matched_terms") or [],
                    "excerpt": result.get("excerpt"),
                }
            )
            signal_index += 1
    return signals


def _intelligence_source_snapshots(
    snapshots: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    official_domain = str(snapshots[0].get("domain") or "") if snapshots else ""
    records_by_url = {
        str(record.get("url")): record for record in records if isinstance(record, dict) and record.get("url")
    }
    output: list[dict[str, Any]] = []
    for snapshot in snapshots:
        url = str(snapshot.get("url") or "")
        record = records_by_url.get(url, {})
        content_hashes = sorted(str(item) for item in snapshot.get("content_hashes") or [])
        output.append(
            {
                "source_snapshot_id": str(snapshot.get("source_snapshot_id") or ""),
                "source_id": str(snapshot.get("source_id") or ""),
                "url": url,
                "document_id": record.get("document_id"),
                "document_version": record.get("content_hash"),
                "content_hash": canonical_sha256(content_hashes) if content_hashes else None,
                "fetched_at": snapshot.get("latest_fetched_at"),
                "authority": classify_source_authority(
                    url,
                    official_domain=official_domain,
                ).model_dump(mode="json"),
            }
        )
    return output


def _intelligence_document_versions(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [
            {
                "document_id": record.get("document_id"),
                "document_version": record.get("content_hash"),
                "url": record.get("url"),
                "fetched_at": record.get("fetched_at"),
                "title": record.get("title"),
            }
            for record in records
            if record.get("document_id") and record.get("content_hash") and record.get("url")
        ],
        key=lambda item: (str(item["url"]), str(item["document_id"])),
    )


def _intelligence_observations(
    claims: list[dict[str, Any]],
    records: list[dict[str, Any]],
    citations_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    expected_domains = [str(item) for item in citations_payload.get("expected_domains") or []]
    official_domain = expected_domains[0] if expected_domains else ""
    by_url = {
        str(record.get("url")): record for record in records if isinstance(record, dict) and record.get("url")
    }
    observations: list[dict[str, Any]] = []
    for claim in claims:
        url = str(claim.get("url") or "")
        text = str(claim.get("text") or "").strip()
        record = by_url.get(url)
        if not url or not text or record is None:
            continue
        content = str(record.get("content") or "")
        authority = classify_source_authority(url, official_domain=official_domain)
        evidence = evidence_span_payload(
            url=url,
            content=content,
            exact_text=text,
            citation_id=str(claim.get("citation_id") or "S0"),
            record_citation_id=(
                str(claim.get("record_citation_id")) if claim.get("record_citation_id") else None
            ),
        )
        observation_seed = {
            "type": "source_excerpt",
            "text": text,
            "document_version": evidence["document_version"],
            "char_start": evidence["char_start"],
            "char_end": evidence["char_end"],
        }
        observations.append(
            {
                "observation_id": stable_id("observation", observation_seed),
                "type": "source_excerpt",
                "text": text,
                "status": "observation",
                "evidence_strength": "strong" if claim.get("citation_id") else "moderate",
                "confidence": 0.9 if claim.get("citation_id") else 0.65,
                "source_authority": authority.model_dump(mode="json"),
                "evidence": [evidence],
                "warnings": [],
            }
        )
    return observations


def _intelligence_change_candidates(pack_dir: Path) -> list[dict[str, Any]]:
    path = pack_dir / "change.events.jsonl"
    if not path.exists():
        return []
    candidates: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        classifications = [str(item) for item in event.get("classifications") or []]
        classification = classifications[0] if classifications else "other"
        if classification not in {"pricing", "positioning", "product", "security", "policy", "other"}:
            classification = "other"
        candidate = {
            "classification": classification,
            "status": "candidate",
            "before": list(event.get("old_evidence") or []),
            "after": list(event.get("new_evidence") or []),
            "confidence": 0.7,
            "warnings": ["Semantic classification is a review candidate, not an approved claim."],
        }
        candidate["change_candidate_id"] = stable_id("change_candidate", candidate)
        candidates.append(candidate)
    return candidates


def _intelligence_warnings(
    score_payload: dict[str, Any],
    source_scores_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for item in [*list(score_payload.get("issues") or []), *list(score_payload.get("warnings") or [])]:
        if not isinstance(item, dict):
            continue
        warnings.append(
            {
                "code": str(item.get("code") or "pack_quality"),
                "message": str(item.get("message") or "Pack quality warning"),
                "metadata": {"severity": item.get("severity")},
            }
        )
    weak_sources = [
        source
        for source in source_scores_payload.get("sources") or []
        if isinstance(source, dict) and _safe_int(source.get("score")) < 60
    ]
    if weak_sources:
        warnings.append(
            {
                "code": "weak_source_authority",
                "message": f"{len(weak_sources)} source records scored below 60.",
                "metadata": {},
            }
        )
    return warnings


def _deterministic_contract_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _deterministic_contract_value(item)
            for key, item in value.items()
            if key not in {"generated_at", "pack_dir", "output_dir"}
        }
    if isinstance(value, list):
        return [_deterministic_contract_value(item) for item in value]
    return value


def search_pack(
    pack_dir: Path,
    query: str,
    *,
    required_domains: list[str] | None = None,
    limit: int = DEFAULT_SEARCH_LIMIT,
    _analysis: _PackAnalysis | None = None,
) -> dict[str, Any]:
    if not query.strip():
        raise PackToolError("query must be non-empty.")
    if limit < 1:
        raise PackToolError("--limit must be at least 1.")
    analysis = _analysis or _analyze_pack(pack_dir, required_domains)
    sources, citation_by_url, expected = analysis.citation_data()
    results = _search_records(
        analysis.records,
        citation_by_url,
        analysis.record_citations(),
        query=query,
        limit=limit,
    )
    citation_ids = {str(result["citation_id"]) for result in results}
    return {
        "schema_version": SEARCH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(analysis.pack_dir),
        "query": query,
        "expected_domains": expected,
        "source_count": len(sources),
        "record_count": len(analysis.records),
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
    payload = {
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
    from .eval_grade import classify_semantic_diff

    payload["semantic_diff"] = classify_semantic_diff(
        old_pack_dir,
        new_pack_dir,
        diff_payload=payload,
    )
    change_events = build_change_events(
        old_records,
        new_records,
        workflow="pack-diff",
    )
    payload["change_events"] = change_events
    payload["change_event_count"] = len(change_events)
    return payload


def _read_json(path: Path, *, required: bool = True) -> Any:
    if not path.exists():
        if required:
            raise PackToolError(f"Missing required file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise PackToolError(f"Invalid JSON in {path}: {err}") from err


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _read_pack_records(pack_dir: Path) -> list[dict[str, Any]]:
    ndjson = pack_dir / "documents.ndjson"
    if ndjson.exists():
        return _read_ndjson(ndjson)
    try:
        from .pack_reader import load_pack

        pack = load_pack(pack_dir)
    except Exception as err:  # noqa: BLE001
        raise PackToolError(f"Unable to load pack records from {pack_dir}: {err}") from err
    return [record.model_dump(mode="json", exclude_none=True) for record in pack.documents]


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
        record_citations = [
            {
                "record_citation_id": f"{citation_id}.{record_index}",
                "source_citation_id": citation_id,
                "record_key": _record_key(record),
                "document_id": record.get("document_id"),
                "chunk_id": record.get("chunk_id"),
                "content_hash": record.get("content_hash"),
                "title": record.get("title") or source.get("title") or url,
            }
            for record_index, record in enumerate(url_records, start=1)
        ]
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
                "record_citations": record_citations,
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
    record_citation_by_key: dict[str, str],
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
        record_citation_id = record_citation_by_key.get(_record_key(record))
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
                        "_citation_ids": set(),
                    },
                )
                item["count"] = _safe_int(item.get("count")) + 1
                citations = item["citations"]
                if isinstance(citations, list) and citation_id:
                    existing_ids = item["_citation_ids"]
                    if citation_id not in existing_ids:
                        existing_ids.add(citation_id)
                        citations.append(
                            {
                                "citation_id": citation_id,
                                "record_citation_id": record_citation_id,
                                "url": url,
                                "title": str(record.get("title") or url),
                                "excerpt": _nearest_sentence(content, match.start(), match.end()),
                            }
                        )
                        item["source_count"] = len(citations)

    for item in entities.values():
        item.pop("_citation_ids", None)
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
    record_citation_by_key: dict[str, str],
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
        record_citation_id = record_citation_by_key.get(_record_key(record))
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
                "record_citation_id": record_citation_id,
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
    record_citation_by_key: dict[str, str],
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
        record_citation_id = record_citation_by_key.get(_record_key(record))
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
                "record_citation_id": record_citation_id,
                "url": url,
                "title": title,
                "chunk_id": record.get("chunk_id"),
                "chunk_heading": record.get("chunk_heading"),
                "content_hash": record.get("content_hash"),
                "token_count": _safe_int(record.get("token_count")),
                "matched_terms": matched_terms,
                "_content": content or title,
            }
        )

    top_results = heapq.nsmallest(
        limit,
        scored,
        key=lambda item: (
            -_safe_int(item.get("score")),
            str(item.get("citation_id") or ""),
            str(item.get("chunk_id") or ""),
            str(item.get("url") or ""),
        ),
    )
    for result in top_results:
        content = str(result.pop("_content"))
        result["excerpt"] = _best_search_excerpt(content, terms, phrase)
    return [
        {
            "rank": rank,
            **result,
        }
        for rank, result in enumerate(top_results, start=1)
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


def _company_brain_markdown(payload: dict[str, Any]) -> str:
    workspace = _dict_value(payload.get("workspace"))
    summary = _dict_value(payload.get("summary"))
    records = _dict_value(payload.get("records"))
    claims = records.get("source_supported_claims") if isinstance(records, dict) else []
    entities = records.get("entities") if isinstance(records, dict) else []
    signals = records.get("signals") if isinstance(records, dict) else []
    claim_items = _list_value(claims)
    entity_items = _list_value(entities)
    signal_items = _list_value(signals)

    lines = [
        "# Company Brain Import Bundle",
        "",
        f"Workspace: {workspace.get('name')}",
        f"Objective: {workspace.get('objective')}",
        f"Generated: {payload.get('generated_at')}",
        "",
        "## Import Summary",
        "",
        f"- Pack score: {summary.get('score')}/100 ({summary.get('grade')})",
        f"- Sources: {summary.get('source_count', 0)}",
        f"- Records: {summary.get('record_count', 0)}",
        f"- Entities: {summary.get('entity_count', 0)}",
        f"- Source-supported claims: {summary.get('claim_count', 0)}",
        f"- Search signals: {summary.get('search_result_count', 0)}",
        "",
        "## Source-Supported Claims",
        "",
    ]
    for claim in claim_items[:12]:
        if not isinstance(claim, dict):
            continue
        lines.append(f"- [{claim.get('citation_id')}] {_truncate_text(str(claim.get('text') or ''), 220)}")

    lines.extend(["", "## Entities", ""])
    for entity in entity_items[:12]:
        if not isinstance(entity, dict):
            continue
        lines.append(
            f"- {entity.get('type')}: {entity.get('value')} "
            f"(sources {entity.get('source_count')}, count {entity.get('count')})"
        )

    lines.extend(["", "## Signals", ""])
    for signal in signal_items[:12]:
        if not isinstance(signal, dict):
            continue
        lines.append(
            f"- {signal.get('query')}: [{signal.get('citation_id')}] "
            f"{_truncate_text(str(signal.get('title') or signal.get('url') or ''), 140)}"
        )

    lines.extend(
        [
            "",
            "## Control Plane Contract",
            "",
            "- Import `records.sources` and `records.source_snapshots` as public context.",
            "- Import `records.entities` as watchlist or dossier seeds.",
            "- Import `records.source_supported_claims` as brief claims that require citation coverage.",
            "- Import `records.gate_inputs` as policy/eval inputs before showing or acting on a brief.",
        ]
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


def _record_key(record: dict[str, Any]) -> str:
    return str(record.get("chunk_id") or record.get("document_id") or record.get("content_hash") or "")


def _record_citation_lookup(sources: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for source in sources:
        citations = source.get("record_citations")
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            key = str(citation.get("record_key") or "")
            value = str(citation.get("record_citation_id") or "")
            if key and value:
                lookup[key] = value
    return lookup


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
    semantic = payload.get("semantic_diff")
    if isinstance(semantic, dict):
        summary = _dict_value(semantic.get("summary"))
        category_lines = [
            f"- {key}: {value}"
            for key, value in sorted(summary.items())
            if isinstance(value, int)
            and key
            in {
                "breaking_change_candidate",
                "deprecation_candidate",
                "new_feature_candidate",
                "removed_section",
                "auth_security_change",
                "pricing_or_limit_change",
                "ambiguous_change",
            }
        ]
        if category_lines:
            lines.extend(["", "## Semantic Diff", "", *category_lines])
    return "\n".join(lines).rstrip() + "\n"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_pack_cli())
