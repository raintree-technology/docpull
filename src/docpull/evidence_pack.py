"""Filing-aware evidence pack command."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from rich.console import Console
from rich.markup import escape

from .conversion.chunking import Chunk, TokenCounter, chunk_markdown
from .core.fetcher import Fetcher
from .models.config import DocpullConfig, ProfileName
from .models.document import DocumentRecord
from .pipeline.manifest import CorpusManifest
from .time_utils import utc_now_iso

EVIDENCE_SCHEMA_VERSION = 1
DIAGNOSTIC_SCHEMA_VERSION = 1
SEC_USER_AGENT_ENV = "DOCPULL_SEC_USER_AGENT"


class EvidencePackError(RuntimeError):
    """User-facing evidence-pack error."""


@dataclass(frozen=True)
class FilingSource:
    """Normalized filing row from the input NDJSON."""

    source_url: str
    metadata: dict[str, Any]
    raw: dict[str, Any]


@dataclass(frozen=True)
class EvidencePattern:
    """Compiled evidence rule."""

    category: str
    pattern: str
    regex: re.Pattern[str]
    method: str
    base_confidence: float


@dataclass(frozen=True)
class EvidenceRules:
    """Evidence rule set loaded from YAML/JSON."""

    profile: str
    patterns: list[EvidencePattern]


def create_evidence_pack_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull evidence-pack",
        description="Build a filing-aware evidence pack from filing URL NDJSON",
    )
    parser.add_argument("filings_ndjson", type=Path, help="Input filing rows as NDJSON")
    parser.add_argument("--rules", type=Path, required=True, help="YAML/JSON evidence rule file")
    parser.add_argument(
        "--profile",
        choices=["sec-filing", "gov-evidence"],
        default="sec-filing",
        help="Pack profile defaults (default: sec-filing)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("evidence-pack"),
        help="Output directory for documents, evidence, manifest, and context",
    )
    parser.add_argument(
        "--sec-user-agent",
        "--user-agent",
        dest="sec_user_agent",
        help=(
            "SEC/EDGAR User-Agent identifying your app or organization and contact. "
            f"Defaults to ${SEC_USER_AGENT_ENV} when set."
        ),
    )
    parser.add_argument(
        "--extractor",
        choices=["auto", "trafilatura", "default"],
        default="auto",
        help="Content extractor. auto prefers trafilatura when installed, then falls back to default.",
    )
    parser.add_argument("--chunk-tokens", type=int, default=2500, help="Target tokens per filing chunk")
    parser.add_argument("--max-filings", type=int, help="Limit number of input filings processed")
    parser.add_argument("--rate-limit", type=float, help="Seconds between requests to the same host")
    parser.add_argument("--max-concurrent", type=int, help="Maximum concurrent requests")
    parser.add_argument("--per-host-concurrent", type=int, help="Maximum concurrent requests per host")
    return parser


def run_evidence_pack_cli(argv: list[str] | None = None) -> int:
    parser = create_evidence_pack_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        summary = asyncio.run(
            build_evidence_pack(
                filings_path=args.filings_ndjson,
                rules_path=args.rules,
                output_dir=args.output_dir,
                profile=args.profile,
                sec_user_agent=args.sec_user_agent,
                extractor=args.extractor,
                chunk_tokens=args.chunk_tokens,
                max_filings=args.max_filings,
                rate_limit=args.rate_limit,
                max_concurrent=args.max_concurrent,
                per_host_concurrent=args.per_host_concurrent,
            )
        )
    except EvidencePackError as err:
        console.print("[red]Evidence pack error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Evidence pack failed:[/red] " + escape(str(err)))
        return 1

    console.print(
        "[green]Evidence pack:[/green] "
        f"{summary['document_count']} documents, "
        f"{summary['record_count']} records, "
        f"{summary['evidence_count']} evidence hits -> {summary['output_dir']}"
    )
    return 0


async def build_evidence_pack(
    *,
    filings_path: Path,
    rules_path: Path,
    output_dir: Path,
    profile: str = "sec-filing",
    sec_user_agent: str | None = None,
    extractor: str = "auto",
    chunk_tokens: int = 2500,
    max_filings: int | None = None,
    rate_limit: float | None = None,
    max_concurrent: int | None = None,
    per_host_concurrent: int | None = None,
) -> dict[str, Any]:
    if chunk_tokens < 100:
        raise EvidencePackError("--chunk-tokens must be at least 100")

    sources = _read_filings_ndjson(filings_path)
    if max_filings is not None:
        sources = sources[:max_filings]
    if not sources:
        raise EvidencePackError(f"No filing rows found in {filings_path}")

    rules = load_evidence_rules(rules_path)
    if not rules.patterns:
        raise EvidencePackError(f"No evidence patterns found in {rules_path}")

    output_dir = output_dir.resolve()
    previous_hashes = _read_previous_hashes(output_dir / "corpus.manifest.json")
    output_dir.mkdir(parents=True, exist_ok=True)

    selected_extractor, extractor_diagnostic = _resolve_extractor(extractor)
    user_agent = sec_user_agent or os.environ.get(SEC_USER_AGENT_ENV)
    pack_diagnostics: list[dict[str, Any]] = []
    if extractor_diagnostic is not None:
        pack_diagnostics.append(extractor_diagnostic)
    if _targets_sec(sources) and not user_agent:
        pack_diagnostics.append(
            _diagnostic(
                "sec_user_agent_not_set",
                "SEC user agent was not set; pass --sec-user-agent or DOCPULL_SEC_USER_AGENT.",
                severity="warning",
            )
        )

    documents_path = output_dir / "documents.ndjson"
    evidence_path = output_dir / "evidence.ndjson"
    diagnostics_path = output_dir / "diagnostics.ndjson"

    manifest = CorpusManifest(output_dir, output_format="evidence-pack")
    counter = TokenCounter()
    source_summaries: list[dict[str, Any]] = []
    document_count = 0
    record_count = 0
    evidence_count = 0
    diagnostics_count = 0

    config = _evidence_fetch_config(
        sources[0].source_url,
        output_dir=output_dir,
        profile=profile,
        extractor=selected_extractor,
        chunk_tokens=chunk_tokens,
        user_agent=user_agent,
        rate_limit=rate_limit,
        max_concurrent=max_concurrent,
        per_host_concurrent=per_host_concurrent,
    )

    with (
        documents_path.open("w", encoding="utf-8") as documents_fp,
        evidence_path.open("w", encoding="utf-8") as evidence_fp,
        diagnostics_path.open("w", encoding="utf-8") as diagnostics_fp,
    ):
        for diagnostic in pack_diagnostics:
            _write_jsonl(diagnostics_fp, diagnostic)
            diagnostics_count += 1

        async with Fetcher(config) as fetcher:
            for source in sources:
                summary = _source_summary(source)
                source_diagnostics: list[dict[str, Any]] = []
                source_hit_count = 0
                source_record_count = 0

                try:
                    ctx = await fetcher.fetch_one(source.source_url, save=False)
                except Exception as err:  # noqa: BLE001
                    ctx = None
                    source_diagnostics.append(
                        _source_diagnostic(
                            "no_readable_content",
                            "no readable content: fetch failed",
                            source,
                            severity="error",
                            error=str(err),
                        )
                    )

                if ctx is None or ctx.error or ctx.should_skip or not (ctx.markdown or "").strip():
                    if not source_diagnostics:
                        detail = ctx.error if ctx and ctx.error else (ctx.skip_reason if ctx else None)
                        source_diagnostics.append(
                            _source_diagnostic(
                                "no_readable_content",
                                "no readable content",
                                source,
                                severity="error",
                                detail=detail,
                            )
                        )
                    summary["diagnostics"] = [item["code"] for item in source_diagnostics]
                    source_summaries.append(summary)
                    for diagnostic in source_diagnostics:
                        _write_jsonl(diagnostics_fp, diagnostic)
                        diagnostics_count += 1
                    continue

                assert ctx is not None
                markdown = ctx.markdown or ""
                source_hash = _sha256_text(markdown)
                retrieved_at = utc_now_iso()
                previous_hash = previous_hashes.get(source.source_url)
                metadata = {
                    **source.metadata,
                    "primary_document_url": source.source_url,
                    "retrieved_at": retrieved_at,
                    "source_document_hash": source_hash,
                }
                chunks = _chunks_for_context(ctx.chunks, markdown, chunk_tokens, counter)

                for chunk in chunks:
                    record = DocumentRecord.from_page(
                        url=source.source_url,
                        title=ctx.title or metadata.get("issuer_name"),
                        content=chunk.text,
                        metadata=metadata,
                        extraction=ctx.extraction_info,
                        source_type=ctx.source_type,
                        chunk_index=chunk.index,
                        chunk_heading=chunk.heading,
                        token_count=chunk.token_count,
                    )
                    manifest.add_record(record, documents_path)
                    payload = record.model_dump(mode="json", exclude_none=True)
                    payload["source_document_hash"] = source_hash
                    _write_jsonl(documents_fp, payload)
                    record_count += 1
                    source_record_count += 1

                    for hit in extract_evidence_from_text(
                        chunk.text,
                        rules,
                        source=source,
                        record=record,
                        source_hash=source_hash,
                    ):
                        _write_jsonl(evidence_fp, hit)
                        evidence_count += 1
                        source_hit_count += 1

                document_count += 1
                source_diagnostics.extend(
                    _quality_diagnostics(
                        ctx_html=ctx.html,
                        markdown=markdown,
                        extraction=ctx.extraction_info,
                        source=source,
                        source_hash=source_hash,
                        previous_hash=previous_hash,
                        evidence_hits=source_hit_count,
                    )
                )
                for diagnostic in source_diagnostics:
                    _write_jsonl(diagnostics_fp, diagnostic)
                    diagnostics_count += 1

                summary.update(
                    {
                        "content_hash": source_hash,
                        "record_count": source_record_count,
                        "evidence_count": source_hit_count,
                        "diagnostics": [item["code"] for item in source_diagnostics],
                    }
                )
                source_summaries.append(summary)

    manifest_path = manifest.finalize()
    sources_path = _write_sources_index(
        output_dir,
        sources=source_summaries,
        evidence_count=evidence_count,
        rules=rules,
    )
    context_path = _write_evidence_context(
        output_dir,
        sources=source_summaries,
        rules=rules,
        selected_extractor=selected_extractor,
        profile=profile,
        diagnostics_count=diagnostics_count,
        evidence_count=evidence_count,
        record_count=record_count,
    )
    agent_context_path = _write_agent_context_alias(context_path)
    pack_path = _write_pack_metadata(
        output_dir,
        sources=source_summaries,
        rules=rules,
        profile=profile,
        selected_extractor=selected_extractor,
        chunk_tokens=chunk_tokens,
        rate_limit=rate_limit,
        max_concurrent=max_concurrent,
        per_host_concurrent=per_host_concurrent,
        document_count=document_count,
        record_count=record_count,
        evidence_count=evidence_count,
        diagnostics_count=diagnostics_count,
    )

    return {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "output_dir": str(output_dir),
        "pack_path": str(pack_path),
        "documents_path": str(documents_path),
        "evidence_path": str(evidence_path),
        "diagnostics_path": str(diagnostics_path),
        "manifest_path": str(manifest_path),
        "sources_path": str(sources_path),
        "context_path": str(context_path),
        "agent_context_path": str(agent_context_path),
        "document_count": document_count,
        "record_count": record_count,
        "evidence_count": evidence_count,
        "diagnostics_count": diagnostics_count,
    }


def load_evidence_rules(path: Path) -> EvidenceRules:
    if not path.exists():
        raise EvidencePackError(f"Rules file does not exist: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw) if path.suffix.lower() == ".json" else yaml.safe_load(raw)
    except Exception as err:  # noqa: BLE001
        raise EvidencePackError(f"Could not parse rules file {path}: {err}") from err
    if not isinstance(data, dict):
        raise EvidencePackError("Rules file must contain an object")

    profile = str(data.get("profile") or path.stem)
    categories = data.get("categories")
    if not isinstance(categories, dict):
        raise EvidencePackError("Rules file must contain a categories object")

    patterns: list[EvidencePattern] = []
    for category, spec in categories.items():
        category_name = str(category)
        entries = _category_patterns(spec)
        for entry in entries:
            patterns.append(_compile_pattern(category_name, entry))
    return EvidenceRules(profile=profile, patterns=patterns)


def extract_evidence_from_text(
    text: str,
    rules: EvidenceRules,
    *,
    source: FilingSource,
    record: DocumentRecord,
    source_hash: str,
) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    extraction_method = str(record.extraction.get("method") or "unknown")
    extraction_confidence = _float_or_none(record.extraction.get("confidence"))
    for pattern in rules.patterns:
        for match in pattern.regex.finditer(text):
            quote, context = _quote_and_context(text, match.start(), match.end())
            section_heading = record.chunk_heading or _heading_before(text, match.start())
            confidence = _evidence_confidence(pattern, extraction_confidence)
            evidence_id = _stable_id(
                "ev",
                source.source_url,
                record.chunk_id or record.document_id,
                pattern.category,
                pattern.pattern,
                quote,
                source_hash,
            )
            payload: dict[str, Any] = {
                "schema_version": EVIDENCE_SCHEMA_VERSION,
                "evidence_id": evidence_id,
                "profile": rules.profile,
                "category": pattern.category,
                "pattern": pattern.pattern,
                "source_url": source.source_url,
                "document_id": record.document_id,
                "chunk_id": record.chunk_id,
                "section_heading": section_heading,
                "quote": quote,
                "snippet": quote,
                "surrounding_context": context,
                "source_hash": source_hash,
                "content_hash": record.content_hash,
                "confidence": confidence,
                "extraction_method": extraction_method,
                "match_method": pattern.method,
                "retrieved_at": record.retrieved_at,
            }
            payload.update({key: value for key, value in source.metadata.items() if value is not None})
            hits.append({key: value for key, value in payload.items() if value is not None})
    return hits


def _read_filings_ndjson(path: Path) -> list[FilingSource]:
    if not path.exists():
        raise EvidencePackError(f"Input filing NDJSON does not exist: {path}")
    sources: list[FilingSource] = []
    seen_urls: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as err:
            raise EvidencePackError(f"Invalid NDJSON in {path} line {line_number}: {err}") from err
        if not isinstance(row, dict):
            raise EvidencePackError(f"Invalid NDJSON in {path} line {line_number}: expected object")
        source_url = _source_url(row)
        if source_url is None:
            raise EvidencePackError(f"Filing row {line_number} has no primary document URL")
        if source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        metadata = _filing_metadata(row, source_url)
        sources.append(FilingSource(source_url=source_url, metadata=metadata, raw=row))
    return sources


def _source_url(row: dict[str, Any]) -> str | None:
    for key in (
        "primary_document_url",
        "primaryDocumentUrl",
        "primary_doc_url",
        "document_url",
        "documentUrl",
        "source_url",
        "url",
        "href",
    ):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _filing_metadata(row: dict[str, Any], source_url: str) -> dict[str, Any]:
    return {
        "cik": _first_string(row, "cik", "CIK"),
        "accession_number": _first_string(
            row,
            "accession_number",
            "accessionNumber",
            "accession_no",
            "accessionNo",
        ),
        "form": _first_string(row, "form", "form_type", "formType"),
        "filing_date": _first_string(row, "filing_date", "filingDate", "filed_at", "filedAt"),
        "issuer_name": _first_string(row, "issuer_name", "issuerName", "company_name", "companyName", "name"),
        "primary_document_url": source_url,
    }


def _first_string(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _category_patterns(spec: Any) -> list[Any]:
    if isinstance(spec, list):
        return spec
    if isinstance(spec, dict):
        patterns = spec.get("patterns")
        if isinstance(patterns, list):
            return patterns
    raise EvidencePackError("Each evidence category must be a list or contain a patterns list")


def _compile_pattern(category: str, entry: Any) -> EvidencePattern:
    if isinstance(entry, str):
        pattern_text = entry
        regex = re.compile(re.escape(pattern_text), re.IGNORECASE)
        return EvidencePattern(
            category=category,
            pattern=pattern_text,
            regex=regex,
            method="literal",
            base_confidence=0.82,
        )
    if not isinstance(entry, dict):
        raise EvidencePackError(f"Invalid pattern for {category}: expected string or object")
    if "pattern" in entry:
        pattern_text = str(entry.get("pattern") or "").strip()
        is_regex = bool(entry.get("regex"))
    else:
        pattern_text = str(entry.get("regex") or "").strip()
        is_regex = True
    if not pattern_text:
        raise EvidencePackError(f"Invalid pattern for {category}: missing pattern")
    method = "regex" if is_regex else "literal"
    expression = pattern_text if is_regex else re.escape(pattern_text)
    try:
        compiled = re.compile(expression, re.IGNORECASE)
    except re.error as err:
        raise EvidencePackError(f"Invalid regex for {category}: {err}") from err
    return EvidencePattern(
        category=category,
        pattern=pattern_text,
        regex=compiled,
        method=method,
        base_confidence=_pattern_confidence(
            entry.get("confidence"),
            default=0.76 if is_regex else 0.82,
            category=category,
            pattern=pattern_text,
        ),
    )


def _pattern_confidence(value: Any, *, default: float, category: str, pattern: str) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise EvidencePackError(f"Invalid confidence for {category}/{pattern}: expected number")
    try:
        confidence = float(value)
    except (TypeError, ValueError) as err:
        raise EvidencePackError(f"Invalid confidence for {category}/{pattern}: expected number") from err
    if not 0 <= confidence <= 1:
        raise EvidencePackError(f"Invalid confidence for {category}/{pattern}: expected 0..1")
    return confidence


def _evidence_fetch_config(
    first_url: str,
    *,
    output_dir: Path,
    profile: str,
    extractor: str,
    chunk_tokens: int,
    user_agent: str | None,
    rate_limit: float | None,
    max_concurrent: int | None,
    per_host_concurrent: int | None,
) -> DocpullConfig:
    crawl: dict[str, Any] = {}
    if rate_limit is not None:
        crawl["rate_limit"] = rate_limit
    if max_concurrent is not None:
        crawl["max_concurrent"] = max_concurrent
    if per_host_concurrent is not None:
        crawl["per_host_concurrent"] = per_host_concurrent

    network: dict[str, Any] = {}
    if user_agent:
        network["user_agent"] = user_agent

    config_kwargs: dict[str, Any] = {
        "url": first_url,
        "profile": ProfileName.SEC_FILING
        if profile in {"sec-filing", "gov-evidence"}
        else ProfileName.CUSTOM,
        "content_filter": {
            "extractor": extractor,
            "clean_inline_xbrl": True,
            "enable_special_cases": True,
        },
        "output": {
            "directory": output_dir,
            "format": "ndjson",
            "rich_metadata": True,
            "max_tokens_per_file": chunk_tokens,
            "emit_chunks": True,
        },
    }
    if crawl:
        config_kwargs["crawl"] = crawl
    if network:
        config_kwargs["network"] = network
    return DocpullConfig(**config_kwargs)


def _resolve_extractor(requested: str) -> tuple[str, dict[str, Any] | None]:
    if requested == "default":
        return "default", None
    if requested == "trafilatura":
        return "trafilatura", None
    if importlib.util.find_spec("trafilatura") is not None:
        return "trafilatura", None
    return (
        "default",
        _diagnostic(
            "trafilatura_unavailable",
            "trafilatura is not installed; fell back to the default extractor.",
            severity="warning",
        ),
    )


def _chunks_for_context(
    ctx_chunks: list[object],
    markdown: str,
    chunk_tokens: int,
    counter: TokenCounter,
) -> list[Chunk]:
    chunks = [chunk for chunk in ctx_chunks if isinstance(chunk, Chunk)]
    if chunks:
        return chunks
    generated = chunk_markdown(markdown, max_tokens=chunk_tokens, counter=counter)
    if generated:
        return generated
    return [Chunk(index=0, text=markdown, token_count=counter.count(markdown), heading=None)]


def _quality_diagnostics(
    *,
    ctx_html: bytes | None,
    markdown: str,
    extraction: dict[str, Any],
    source: FilingSource,
    source_hash: str,
    previous_hash: str | None,
    evidence_hits: int,
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if _xbrl_noise_score(ctx_html, markdown, extraction) >= 0.012:
        diagnostics.append(
            _source_diagnostic(
                "high_xbrl_noise",
                "high XBRL noise",
                source,
                source_hash=source_hash,
                severity="warning",
            )
        )
    if _tables_look_degraded(ctx_html, markdown):
        diagnostics.append(
            _source_diagnostic(
                "tables_degraded",
                "tables degraded",
                source,
                source_hash=source_hash,
                severity="warning",
            )
        )
    if evidence_hits == 0:
        diagnostics.append(
            _source_diagnostic(
                "no_matching_evidence_categories",
                "no matching evidence categories",
                source,
                source_hash=source_hash,
                severity="warning",
            )
        )
    if previous_hash and previous_hash != source_hash:
        diagnostics.append(
            _source_diagnostic(
                "source_hash_changed",
                "source hash changed since last run",
                source,
                source_hash=source_hash,
                previous_hash=previous_hash,
                severity="warning",
            )
        )
    return diagnostics


def _xbrl_noise_score(ctx_html: bytes | None, markdown: str, extraction: dict[str, Any]) -> float:
    text = (ctx_html or b"").decode("utf-8", errors="ignore") + "\n" + markdown
    markers = (
        text.lower().count("contextref")
        + text.lower().count("unitref")
        + text.lower().count("ix:")
        + text.lower().count("us-gaap")
        + text.lower().count("dei:")
    )
    cleanup = extraction.get("inline_xbrl_cleanup")
    if isinstance(cleanup, dict):
        markers += int(cleanup.get("hidden_inline_xbrl_removed") or 0)
    return markers / max(1, len(text))


def _tables_look_degraded(ctx_html: bytes | None, markdown: str) -> bool:
    html = (ctx_html or b"").lower()
    table_count = html.count(b"<table")
    if table_count == 0:
        return False
    markdown_table_lines = sum(1 for line in markdown.splitlines() if line.strip().startswith("|"))
    return markdown_table_lines < table_count


def _source_summary(source: FilingSource) -> dict[str, Any]:
    return {
        "source_url": source.source_url,
        "cik": source.metadata.get("cik"),
        "accession_number": source.metadata.get("accession_number"),
        "form": source.metadata.get("form"),
        "filing_date": source.metadata.get("filing_date"),
        "issuer_name": source.metadata.get("issuer_name"),
        "record_count": 0,
        "evidence_count": 0,
        "diagnostics": [],
    }


def _write_sources_index(
    output_dir: Path,
    *,
    sources: list[dict[str, Any]],
    evidence_count: int,
    rules: EvidenceRules,
) -> Path:
    lines = [
        "# Evidence Pack Sources",
        "",
        f"Generated: `{utc_now_iso()}`",
        f"Rules profile: `{rules.profile}`",
        f"Evidence hits: `{evidence_count}`",
        "",
        "## Sources",
        "",
    ]
    if not sources:
        lines.append("_No sources were processed._")
    for index, source in enumerate(sources, start=1):
        title = source.get("issuer_name") or source["source_url"]
        lines.append(f"{index}. [{title}]({source['source_url']})")
        for key, label in (
            ("cik", "CIK"),
            ("accession_number", "Accession"),
            ("form", "Form"),
            ("filing_date", "Filing date"),
            ("content_hash", "Source hash"),
        ):
            if source.get(key):
                lines.append(f"   - {label}: `{source[key]}`")
        lines.append(f"   - Records: `{source.get('record_count', 0)}`")
        lines.append(f"   - Evidence hits: `{source.get('evidence_count', 0)}`")
        diagnostics = source.get("diagnostics") or []
        if diagnostics:
            lines.append(f"   - Diagnostics: `{', '.join(diagnostics)}`")
    path = output_dir / "sources.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_evidence_context(
    output_dir: Path,
    *,
    sources: list[dict[str, Any]],
    rules: EvidenceRules,
    selected_extractor: str,
    profile: str,
    diagnostics_count: int,
    evidence_count: int,
    record_count: int,
) -> Path:
    categories = sorted({pattern.category for pattern in rules.patterns})
    lines = [
        "# Evidence Context",
        "",
        "## Read First",
        "",
        (
            "1. `evidence.ndjson` - citation-ready matched evidence with source URL, "
            "chunk id, quote, context, hash, and confidence."
        ),
        "2. `sources.md` - filing/source index with CIK, accession, form, source hash, and diagnostics.",
        "3. `documents.ndjson` - chunked filing text records for deeper review.",
        "4. `diagnostics.ndjson` - quality gates and rerun warnings.",
        "5. `corpus.manifest.json` - stable document/chunk manifest for diffing.",
        "",
        "## Pack Signals",
        "",
        f"- Profile: `{profile}`",
        f"- Rules profile: `{rules.profile}`",
        f"- Extractor: `{selected_extractor}`",
        f"- Sources: `{len(sources)}`",
        f"- Records: `{record_count}`",
        f"- Evidence hits: `{evidence_count}`",
        f"- Diagnostics: `{diagnostics_count}`",
        f"- Categories: `{', '.join(categories)}`",
        "",
        "## Source Priority",
        "",
    ]
    ranked = sorted(sources, key=lambda item: int(item.get("evidence_count") or 0), reverse=True)
    for index, source in enumerate(ranked[:20], start=1):
        title = source.get("issuer_name") or source["source_url"]
        lines.append(
            f"{index}. {title} - `{source.get('evidence_count', 0)}` hits, "
            f"`{source.get('record_count', 0)}` records"
        )
    if not ranked:
        lines.append("_No readable sources._")
    path = output_dir / "EVIDENCE_CONTEXT.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_agent_context_alias(context_path: Path) -> Path:
    agent_context_path = context_path.with_name("AGENT_CONTEXT.md")
    agent_context_path.write_text(context_path.read_text(encoding="utf-8"), encoding="utf-8")
    return agent_context_path


def _write_pack_metadata(
    output_dir: Path,
    *,
    sources: list[dict[str, Any]],
    rules: EvidenceRules,
    profile: str,
    selected_extractor: str,
    chunk_tokens: int,
    rate_limit: float | None,
    max_concurrent: int | None,
    per_host_concurrent: int | None,
    document_count: int,
    record_count: int,
    evidence_count: int,
    diagnostics_count: int,
) -> Path:
    path = output_dir / "evidence.pack.json"
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "workflow": "evidence-pack",
        "generated_at": utc_now_iso(),
        "profile": profile,
        "rules_profile": rules.profile,
        "document_count": document_count,
        "record_count": record_count,
        "evidence_count": evidence_count,
        "diagnostics_count": diagnostics_count,
        "artifacts": {
            "pack": "evidence.pack.json",
            "documents": "documents.ndjson",
            "evidence": "evidence.ndjson",
            "diagnostics": "diagnostics.ndjson",
            "sources": "sources.md",
            "manifest": "corpus.manifest.json",
            "evidence_context": "EVIDENCE_CONTEXT.md",
            "agent_context": "AGENT_CONTEXT.md",
        },
        "request_options": {
            "profile": profile,
            "extractor": selected_extractor,
            "chunk_tokens": chunk_tokens,
            "rate_limit": rate_limit,
            "max_concurrent": max_concurrent,
            "per_host_concurrent": per_host_concurrent,
        },
        "sources": [_pack_source(index, source) for index, source in enumerate(sources, start=1)],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _pack_source(index: int, source: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "index": index,
        "url": source.get("source_url"),
        "title": source.get("issuer_name") or source.get("source_url"),
        "cik": source.get("cik"),
        "accession_number": source.get("accession_number"),
        "form": source.get("form"),
        "filing_date": source.get("filing_date"),
        "source_hash": source.get("content_hash"),
        "record_count": source.get("record_count", 0),
        "evidence_count": source.get("evidence_count", 0),
        "diagnostics": source.get("diagnostics", []),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _read_previous_hashes(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    records = payload.get("records")
    if not isinstance(records, list):
        return {}
    hashes: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        url = record.get("primary_document_url") or record.get("url")
        source_hash = record.get("source_document_hash") or record.get("content_hash")
        if isinstance(url, str) and isinstance(source_hash, str):
            hashes[url] = source_hash
    return hashes


def _quote_and_context(text: str, start: int, end: int) -> tuple[str, str]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end == -1:
        line_end = len(text)
    quote = _compact(text[line_start:line_end])[:500]

    context_start = max(0, start - 350)
    context_end = min(len(text), end + 350)
    context = _compact(text[context_start:context_end])[:900]
    return quote, context


def _heading_before(text: str, offset: int) -> str | None:
    heading: str | None = None
    for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*#*\s*$", text[:offset], re.MULTILINE):
        heading = match.group(2).strip()
    return heading


def _evidence_confidence(pattern: EvidencePattern, extraction_confidence: float | None) -> float:
    if extraction_confidence is None:
        return round(pattern.base_confidence, 2)
    return round(min(0.99, max(0.1, (pattern.base_confidence + extraction_confidence) / 2)), 2)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _source_diagnostic(
    code: str,
    message: str,
    source: FilingSource,
    *,
    severity: str,
    **extra: Any,
) -> dict[str, Any]:
    payload = _diagnostic(code, message, severity=severity, source_url=source.source_url, **extra)
    payload.update({key: value for key, value in source.metadata.items() if value is not None})
    return payload


def _diagnostic(code: str, message: str, *, severity: str, **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "code": code,
        "message": message,
        "severity": severity,
        "created_at": utc_now_iso(),
    }
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _targets_sec(sources: list[FilingSource]) -> bool:
    for source in sources:
        hostname = urlparse(source.source_url).hostname or ""
        if hostname.lower().endswith("sec.gov"):
            return True
    return False


def _write_jsonl(handle: Any, payload: dict[str, Any]) -> None:
    handle.write(json.dumps(payload, ensure_ascii=False))
    handle.write("\n")


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256(chr(31).join(parts).encode('utf-8')).hexdigest()[:24]}"


__all__ = [
    "EvidencePackError",
    "EvidenceRules",
    "build_evidence_pack",
    "create_evidence_pack_parser",
    "extract_evidence_from_text",
    "load_evidence_rules",
    "run_evidence_pack_cli",
]
