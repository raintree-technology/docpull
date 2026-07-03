"""Local-first pack workflows used by CLI, SDK, and MCP surfaces."""

from __future__ import annotations

import argparse
import asyncio
import re
from collections import Counter
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from .core.fetcher import Fetcher
from .models.config import DocpullConfig, ProfileName
from .models.document import DocumentRecord
from .models.run import RunIdentity
from .pack_tools import (
    PackToolError,
    _artifact_ref,
    _brief_markdown,
    _clean_passage,
    _diff_markdown,
    _domain,
    _expected_domains,
    _issue,
    _optional_int,
    _read_json,
    _read_ndjson,
    _read_pack_metadata,
    _read_pack_records,
    _safe_int,
    _search_markdown,
    _write_json,
    build_citation_map,
    build_research_brief,
    diff_packs,
    score_pack,
    search_pack,
)
from .source_scoring import score_source_entries
from .time_utils import utc_now_iso

REFRESH_SCHEMA_VERSION = 1
AUDIT_SCHEMA_VERSION = 1
ANSWER_SCHEMA_VERSION = 1
LOCAL_PACK_SCHEMA_VERSION = 1


class LocalWorkflowError(RuntimeError):
    """User-facing local workflow error."""


def create_refresh_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull refresh",
        description="Refresh a local DocPull pack and write change reports",
    )
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--output-dir", "-o", type=Path, help="Directory for the refreshed pack snapshot")
    parser.add_argument("--changed-only", action="store_true", help="Highlight changed URLs in the report")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan refresh from the current manifest without making network requests",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        help="Markdown report path (default: <pack>/refresh.report.md)",
    )
    return parser


def run_refresh_cli(argv: list[str] | None = None) -> int:
    parser = create_refresh_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = refresh_pack(
            args.pack_dir,
            output_dir=args.output_dir,
            changed_only=args.changed_only,
            dry_run=args.dry_run,
            markdown_path=args.markdown,
        )
        if payload["dry_run"]:
            console.print(
                "[green]Refresh dry run:[/green] "
                f"{payload['summary']['planned_url_count']} URL(s) from {args.pack_dir}"
            )
        else:
            console.print(
                "[green]Refresh report:[/green] "
                f"{payload['artifacts']['json']} "
                f"(changed {len(payload['diff'].get('changed_urls', []))})"
            )
        return 0
    except (LocalWorkflowError, PackToolError) as err:
        console.print("[red]Refresh error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Refresh failed:[/red] " + escape(str(err)))
        return 1


def create_answer_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull answer-pack",
        description="Answer from a local DocPull pack with citations and refusal on missing evidence",
    )
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("question")
    parser.add_argument("--output", type=Path, help="Markdown report path")
    parser.add_argument("--json-output", type=Path, help="JSON result path")
    parser.add_argument("--limit", type=int, default=8, help="Maximum cited search results")
    parser.add_argument("--require-domain", action="append", dest="required_domains", default=[])
    return parser


def run_answer_cli(argv: list[str] | None = None) -> int:
    parser = create_answer_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = answer_pack(
            args.pack_dir,
            args.question,
            limit=args.limit,
            required_domains=args.required_domains,
            markdown_path=args.output,
            json_path=args.json_output,
        )
        console.print(
            f"[green]Answer pack:[/green] {payload['artifacts']['markdown']} ({payload['answer']['status']})"
        )
        return 0 if payload["answer"]["status"] != "insufficient_evidence" else 2
    except (LocalWorkflowError, PackToolError) as err:
        console.print("[red]Answer error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Answer failed:[/red] " + escape(str(err)))
        return 1


def refresh_pack(
    pack_dir: Path,
    *,
    output_dir: Path | None = None,
    changed_only: bool = False,
    dry_run: bool = False,
    markdown_path: Path | None = None,
) -> dict[str, Any]:
    """Refresh the URLs in a local pack and write refresh report sidecars."""
    pack_dir = pack_dir.resolve()
    records = _read_pack_records(pack_dir)
    if not records:
        raise LocalWorkflowError("Cannot refresh an empty pack.")
    urls = _unique_record_urls(records)
    if not urls:
        raise LocalWorkflowError("Pack has no record URLs to refresh.")

    old_pack_metadata = _read_pack_metadata(pack_dir)
    generated_at = utc_now_iso()
    report_path = pack_dir / "refresh.report.json"
    markdown_report_path = markdown_path or (pack_dir / "refresh.report.md")

    if dry_run:
        dry_run_output_dir = (output_dir or _default_refresh_output_dir(pack_dir)).resolve()
        payload = {
            "schema_version": REFRESH_SCHEMA_VERSION,
            "generated_at": generated_at,
            "dry_run": True,
            "pack_dir": str(pack_dir),
            "output_dir": str(dry_run_output_dir),
            "changed_only": changed_only,
            "summary": {
                "planned_url_count": len(urls),
                "expected_domains": _expected_domains(old_pack_metadata),
            },
            "planned_urls": urls,
            "diff": {
                "schema_version": 1,
                "generated_at": generated_at,
                "old_pack_dir": str(pack_dir),
                "new_pack_dir": str(dry_run_output_dir),
                "added_urls": [],
                "removed_urls": [],
                "changed_urls": [],
                "unchanged_urls": urls,
                "old_record_count": len(records),
                "new_record_count": len(records),
            },
            "artifacts": {
                "json": _artifact_ref(pack_dir, report_path),
                "markdown": _artifact_ref(pack_dir, markdown_report_path),
            },
        }
        _write_json(report_path, payload)
        markdown_report_path.write_text(_refresh_markdown(payload), encoding="utf-8")
        return payload

    refreshed_dir = (output_dir or _default_refresh_output_dir(pack_dir)).resolve()
    refreshed_dir.mkdir(parents=True, exist_ok=True)
    fetch_result = asyncio.run(_fetch_urls_to_pack(urls, refreshed_dir, old_pack_metadata))
    diff_payload = (
        diff_packs(pack_dir, refreshed_dir)
        if fetch_result["record_count"]
        else _empty_diff(pack_dir, refreshed_dir)
    )
    payload = {
        "schema_version": REFRESH_SCHEMA_VERSION,
        "generated_at": generated_at,
        "dry_run": False,
        "pack_dir": str(pack_dir),
        "output_dir": str(refreshed_dir),
        "changed_only": changed_only,
        "summary": {
            "planned_url_count": len(urls),
            "fetched_count": fetch_result["record_count"],
            "failed_count": len(fetch_result["errors"]),
            "skipped_count": len(fetch_result["skips"]),
            "changed_count": len(diff_payload.get("changed_urls", [])),
            "added_count": len(diff_payload.get("added_urls", [])),
            "removed_count": len(diff_payload.get("removed_urls", [])),
            "unchanged_count": len(diff_payload.get("unchanged_urls", [])),
            "expected_domains": _expected_domains(old_pack_metadata),
        },
        "diff": diff_payload,
        "errors": fetch_result["errors"],
        "skips": fetch_result["skips"],
        "artifacts": {
            "json": _artifact_ref(pack_dir, report_path),
            "markdown": _artifact_ref(pack_dir, markdown_report_path),
            "refreshed_pack": str(refreshed_dir),
        },
    }
    _write_json(report_path, payload)
    markdown_report_path.write_text(_refresh_markdown(payload), encoding="utf-8")
    return payload


async def _fetch_urls_to_pack(
    urls: list[str],
    output_dir: Path,
    old_pack_metadata: dict[str, Any],
) -> dict[str, Any]:
    records: list[DocumentRecord] = []
    source_entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    run_identity = RunIdentity.from_config(DocpullConfig(url=urls[0], profile=ProfileName.CUSTOM))

    async with Fetcher(DocpullConfig(url=urls[0], profile=ProfileName.CUSTOM)) as fetcher:
        for index, url in enumerate(urls, start=1):
            ctx = await fetcher.fetch_one(url, save=False)
            if ctx.error:
                errors.append({"url": url, "error": ctx.error})
                continue
            if ctx.should_skip:
                skips.append({"url": url, "reason": ctx.skip_reason, "code": str(ctx.skip_code or "")})
                continue
            content = ctx.markdown or ""
            if not content.strip():
                skips.append({"url": url, "reason": "empty content"})
                continue
            record = DocumentRecord.from_page(
                url=url,
                title=ctx.title,
                content=content,
                metadata=ctx.metadata,
                extraction=ctx.extraction_info,
                source_type=ctx.source_type or "local_refresh",
                run_identity=run_identity,
            )
            records.append(record)
            source_path = output_dir / "sources" / f"{index:03d}.md"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(content, encoding="utf-8")
            source_entries.append(
                {
                    "index": index,
                    "url": url,
                    "title": ctx.title or url,
                    "path": _artifact_ref(output_dir, source_path),
                }
            )

    _write_refreshed_pack(output_dir, records, source_entries, old_pack_metadata)
    return {"record_count": len(records), "errors": errors, "skips": skips}


def _write_refreshed_pack(
    output_dir: Path,
    records: list[DocumentRecord],
    sources: list[dict[str, Any]],
    old_pack_metadata: dict[str, Any],
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
            "chunk_count": 0,
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
    source_policy = _source_policy_from_metadata(old_pack_metadata, sources)
    _write_json(output_dir / "source_policy.json", source_policy)
    _write_json(
        output_dir / "local.pack.json",
        {
            "schema_version": LOCAL_PACK_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "provider": "local",
            "workflow": "refresh-pack",
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


def audit_pack(
    pack_dir: Path,
    *,
    required_domains: list[str] | None = None,
    fail_under: float | None = None,
    markdown_path: Path | None = None,
    json_path: Path | None = None,
) -> dict[str, Any]:
    """Write an actionable local pack quality audit."""
    pack_dir = pack_dir.resolve()
    records = _read_pack_records(pack_dir)
    score_payload = score_pack(pack_dir, required_domains=required_domains)
    citation_payload = build_citation_map(pack_dir, required_domains=required_domains)
    dimensions = _audit_dimensions(records, score_payload, citation_payload)
    stale_sidecar_issues = _stale_sidecar_issues(pack_dir, records, score_payload)
    weighted_score = max(
        0,
        _weighted_audit_score(dimensions, score_payload["score"]) - min(30, len(stale_sidecar_issues) * 15),
    )
    issues = [*score_payload["issues"], *stale_sidecar_issues]
    payload = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "score": weighted_score,
        "grade": _audit_grade(weighted_score),
        "base_pack_score": score_payload["score"],
        "fail_under": fail_under,
        "passed": fail_under is None or (weighted_score / 100) >= fail_under,
        "dimensions": dimensions,
        "issues": issues,
        "warnings": score_payload["warnings"],
        "summary": {
            **score_payload["summary"],
            "source_count": citation_payload["source_count"],
            "citation_coverage": dimensions["citation_coverage"]["value"],
            "stale_sidecar_count": len(stale_sidecar_issues),
        },
    }
    output = json_path or (pack_dir / "pack.audit.json")
    markdown = markdown_path or (pack_dir / "PACK_AUDIT.md")
    _write_json(output, payload)
    markdown.write_text(_audit_markdown(payload), encoding="utf-8")
    payload["artifacts"] = {
        "json": _artifact_ref(pack_dir, output),
        "markdown": _artifact_ref(pack_dir, markdown),
    }
    _write_json(output, payload)
    if fail_under is not None and not payload["passed"]:
        raise LocalWorkflowError(
            f"Pack audit score {weighted_score / 100:.2f} is below fail_under {fail_under:.2f}"
        )
    return payload


def _stale_sidecar_issues(
    pack_dir: Path,
    records: list[dict[str, Any]],
    score_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    current_record_count = len(records)
    current_document_count = _optional_int(score_payload.get("summary", {}).get("document_count"))
    if current_document_count is None:
        current_document_count = len(
            {
                str(record.get("document_id") or record.get("url") or "")
                for record in records
                if record.get("document_id") or record.get("url")
            }
        )
    issues: list[dict[str, Any]] = []
    for filename, code in (
        ("pack.score.json", "stale_pack_score_sidecar"),
        ("pack.audit.json", "stale_pack_audit_sidecar"),
    ):
        path = pack_dir / filename
        if not path.exists():
            continue
        payload = _read_json(path, required=False)
        if not isinstance(payload, dict):
            continue
        summary_raw = payload.get("summary")
        summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
        sidecar_record_count = _optional_int(summary.get("record_count"))
        sidecar_document_count = _optional_int(summary.get("document_count"))
        record_stale = sidecar_record_count is not None and sidecar_record_count != current_record_count
        document_stale = (
            sidecar_document_count is not None and sidecar_document_count != current_document_count
        )
        if not record_stale and not document_stale:
            continue
        issues.append(
            _issue(
                code,
                (
                    f"{filename} summary counts do not match the current context corpus: "
                    f"record_count {sidecar_record_count} vs {current_record_count}, "
                    f"document_count {sidecar_document_count} vs {current_document_count}. "
                    "Regenerate pack trust artifacts."
                ),
                severity="error",
                paths=[filename],
            )
        )
    return issues


def answer_pack(
    pack_dir: Path,
    question: str,
    *,
    limit: int = 8,
    required_domains: list[str] | None = None,
    markdown_path: Path | None = None,
    json_path: Path | None = None,
) -> dict[str, Any]:
    """Produce a deterministic cited answer from local pack evidence."""
    if not question.strip():
        raise LocalWorkflowError("question must be non-empty.")
    if limit < 1:
        raise LocalWorkflowError("limit must be at least 1.")
    pack_dir = pack_dir.resolve()
    search_payload = search_pack(pack_dir, question, required_domains=required_domains, limit=limit)
    brief_payload = build_research_brief(
        pack_dir,
        objective=question,
        required_domains=required_domains,
        max_excerpts=max(limit, 1),
        entity_limit=10,
    )
    result_count = _safe_int(search_payload.get("result_count"))
    if result_count == 0:
        answer = {
            "status": "insufficient_evidence",
            "text": "The local pack does not contain enough cited evidence to answer this question.",
            "citations": [],
        }
    else:
        answer = {
            "status": "answered_from_local_pack",
            "text": _synthesize_local_answer(question, search_payload),
            "citations": search_payload.get("citations", []),
        }

    output = json_path or (pack_dir / "answer.result.json")
    markdown = markdown_path or (pack_dir / "answer.report.md")
    payload = {
        "schema_version": ANSWER_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack_dir),
        "question": question,
        "answer": answer,
        "search": search_payload,
        "brief": {
            "summary": brief_payload.get("summary"),
            "key_excerpts": brief_payload.get("key_excerpts"),
        },
        "artifacts": {
            "json": _artifact_ref(pack_dir, output),
            "markdown": _artifact_ref(pack_dir, markdown),
        },
    }
    _write_json(output, payload)
    markdown.write_text(_answer_markdown(payload), encoding="utf-8")
    return payload


def _unique_record_urls(records: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for record in records:
        url = str(record.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _default_refresh_output_dir(pack_dir: Path) -> Path:
    safe_stamp = re.sub(r"[^0-9A-Za-z]+", "-", utc_now_iso()).strip("-")
    return pack_dir.parent / f"{pack_dir.name}.refresh-{safe_stamp}"


def _empty_diff(old_pack_dir: Path, new_pack_dir: Path) -> dict[str, Any]:
    old_records = _read_ndjson(old_pack_dir / "documents.ndjson")
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "old_pack_dir": str(old_pack_dir.resolve()),
        "new_pack_dir": str(new_pack_dir.resolve()),
        "added_urls": [],
        "removed_urls": _unique_record_urls(old_records),
        "changed_urls": [],
        "unchanged_urls": [],
        "old_record_count": len(old_records),
        "new_record_count": 0,
    }


def _source_policy_from_metadata(
    pack_metadata: dict[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    expected = _expected_domains(pack_metadata)
    if not expected:
        expected = sorted({_domain(str(source.get("url") or "")) for source in sources if source.get("url")})
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "source": "local_refresh",
        "include_domains": expected,
        "render": {"mode": "off", "backend": None},
        "auth": {"allow_authenticated_sources": False},
        "freshness": {"force_live": True, "cache_allowed": True},
    }


def _sources_markdown(sources: list[dict[str, Any]]) -> str:
    lines = ["# Sources", ""]
    for source in sources:
        title = source.get("title") or source.get("url")
        lines.append(f"- {source.get('index')}. [{title}]({source.get('url')})")
    return "\n".join(lines).rstrip() + "\n"


def _audit_dimensions(
    records: list[dict[str, Any]],
    score_payload: dict[str, Any],
    citation_payload: dict[str, Any],
) -> dict[str, Any]:
    urls = [str(record.get("url") or "") for record in records if record.get("url")]
    domains = Counter(_domain(url) for url in urls if _domain(url))
    content_hashes = [
        str(record.get("content_hash") or "") for record in records if record.get("content_hash")
    ]
    duplicate_count = len(content_hashes) - len(set(content_hashes))
    duplicate_rate = duplicate_count / len(content_hashes) if content_hashes else 0.0
    token_counts = [
        _safe_int(record.get("token_count")) for record in records if _safe_int(record.get("token_count"))
    ]
    citation_urls = {
        str(source.get("url")) for source in citation_payload.get("sources", []) if source.get("url")
    }
    citation_covered = sum(1 for url in urls if url in citation_urls)
    citation_coverage = citation_covered / len(urls) if urls else 0.0
    source_scores = score_source_entries(
        [{"url": url, "title": url} for url in sorted(set(urls))],
        expected_domains=score_payload.get("expected_domains") or [],
    )
    primary_source_count = sum(1 for source in source_scores if source.get("grade") == "primary")
    return {
        "source_diversity": {
            "score": min(100, max(20, len(domains) * 35)) if urls else 0,
            "value": len(domains),
            "summary": f"{len(domains)} domain(s)",
        },
        "freshness": {
            "score": _freshness_score(records),
            "value": _freshness_value(records),
            "summary": "Derived from fetched_at metadata; no live checks performed.",
        },
        "duplicate_rate": {
            "score": max(0, int(100 - duplicate_rate * 100)),
            "value": round(duplicate_rate, 4),
            "summary": f"{duplicate_count} duplicate content hash(es)",
        },
        "citation_coverage": {
            "score": int(citation_coverage * 100),
            "value": round(citation_coverage, 4),
            "summary": f"{citation_covered}/{len(urls)} records have citation sources",
        },
        "chunk_size_distribution": {
            "score": _chunk_size_score(token_counts),
            "value": {
                "count": len(token_counts),
                "min": min(token_counts) if token_counts else None,
                "max": max(token_counts) if token_counts else None,
            },
            "summary": "Token counts are evaluated when chunk metadata exists.",
        },
        "required_domain_coverage": {
            "score": 100 if not score_payload.get("issues") else max(0, score_payload["score"]),
            "value": {
                "expected_domains": score_payload.get("expected_domains") or [],
                "primary_source_count": primary_source_count,
            },
            "summary": "Uses the same source policy metadata as pack scoring.",
        },
    }


def _freshness_value(records: list[dict[str, Any]]) -> dict[str, Any]:
    fetched = [str(record.get("fetched_at") or "") for record in records if record.get("fetched_at")]
    return {
        "record_count_with_fetched_at": len(fetched),
        "oldest_fetched_at": min(fetched) if fetched else None,
        "latest_fetched_at": max(fetched) if fetched else None,
    }


def _freshness_score(records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    with_dates = sum(1 for record in records if record.get("fetched_at"))
    if with_dates == len(records):
        return 100
    if with_dates:
        return 75
    return 50


def _chunk_size_score(token_counts: list[int]) -> int:
    if not token_counts:
        return 85
    too_small = sum(1 for value in token_counts if value < 100)
    too_large = sum(1 for value in token_counts if value > 8000)
    penalty = (too_small + too_large) * 8
    return max(0, 100 - penalty)


def _weighted_audit_score(dimensions: dict[str, Any], base_score: int) -> int:
    weights = {
        "source_diversity": 0.12,
        "freshness": 0.16,
        "duplicate_rate": 0.18,
        "citation_coverage": 0.20,
        "chunk_size_distribution": 0.12,
        "required_domain_coverage": 0.12,
    }
    dimension_score = sum(
        _safe_int(dimensions[name].get("score")) * weight for name, weight in weights.items()
    )
    return int(round(dimension_score + base_score * 0.10))


def _audit_grade(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 80:
        return "good"
    if score >= 65:
        return "needs_review"
    return "poor"


def _audit_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Pack Audit",
        "",
        f"Score: {payload['score']}/100 ({payload['grade']})",
        f"Base pack score: {payload['base_pack_score']}/100",
        "",
        "## Dimensions",
        "",
    ]
    for name, dimension in payload.get("dimensions", {}).items():
        lines.append(
            f"- {name.replace('_', ' ').title()}: {dimension.get('score')}/100 - {dimension.get('summary')}"
        )
    if payload.get("issues"):
        lines.extend(["", "## Issues", ""])
        for issue in payload["issues"]:
            lines.append(f"- {issue.get('severity', 'warning')}: {issue.get('message')}")
    if payload.get("warnings"):
        lines.extend(["", "## Warnings", ""])
        for warning in payload["warnings"]:
            lines.append(f"- {warning.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def _refresh_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Refresh Report",
        "",
        f"Pack: `{payload.get('pack_dir')}`",
        f"Dry run: {payload.get('dry_run')}",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    diff_payload = payload.get("diff")
    if isinstance(diff_payload, dict):
        lines.extend(["", "## Diff", "", _diff_markdown(diff_payload)])
    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        for error in payload["errors"]:
            lines.append(f"- {error.get('url')}: {error.get('error')}")
    if payload.get("skips"):
        lines.extend(["", "## Skips", ""])
        for skip in payload["skips"]:
            lines.append(f"- {skip.get('url')}: {skip.get('reason')}")
    return "\n".join(lines).rstrip() + "\n"


def _synthesize_local_answer(question: str, search_payload: dict[str, Any]) -> str:
    parts = []
    for result in search_payload.get("results", [])[:5]:
        if not isinstance(result, dict):
            continue
        excerpt = _clean_passage(str(result.get("excerpt") or ""))
        if not excerpt:
            continue
        citation = result.get("citation_id")
        parts.append(f"[{citation}] {excerpt}")
    if not parts:
        return "The local pack has matching records, but no usable excerpts to summarize."
    return f"Based only on the local pack evidence for `{question}`: " + " ".join(parts)


def _answer_markdown(payload: dict[str, Any]) -> str:
    answer = payload.get("answer", {})
    lines = [
        "# Local Pack Answer",
        "",
        f"Question: {payload.get('question')}",
        f"Status: {answer.get('status')}",
        "",
        str(answer.get("text") or ""),
        "",
    ]
    search_payload = payload.get("search")
    if isinstance(search_payload, dict):
        lines.extend(["## Evidence", "", _search_markdown(search_payload)])
    brief = payload.get("brief")
    if isinstance(brief, dict):
        lines.extend(
            [
                "",
                "## Brief Excerpts",
                "",
                _brief_markdown(
                    {
                        "objective": payload.get("question"),
                        "generated_at": payload.get("generated_at"),
                        "summary": brief.get("summary") or {},
                        "load_plan": [],
                        "key_excerpts": brief.get("key_excerpts") or [],
                        "entities": [],
                    }
                ),
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
