"""Utilities for inspecting docpull context packs."""

from __future__ import annotations

import argparse
import json
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
    parallel_pack = _read_pack_metadata(pack_dir)
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
                        "parallel pack record_count "
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
            warnings.append(_issue("missing_artifact_index", "parallel pack has no artifacts index."))
        if parallel_pack.get("extract_error_count", 0):
            count = int(parallel_pack.get("extract_error_count", 0))
            score -= min(15, count * 5)
            warnings.append(_issue("extract_errors", f"Pack preserved {count} extract errors."))
        if not parallel_pack.get("request_options"):
            score -= 5
            warnings.append(_issue("missing_request_options", "parallel.pack.json has no request_options."))
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


def diff_packs(old_pack_dir: Path, new_pack_dir: Path) -> dict[str, Any]:
    old_records = _records_by_url(_read_ndjson(old_pack_dir / "documents.ndjson"))
    new_records = _records_by_url(_read_ndjson(new_pack_dir / "documents.ndjson"))

    old_urls = set(old_records)
    new_urls = set(new_records)
    shared_urls = sorted(old_urls & new_urls)
    changed_urls = [url for url in shared_urls if _hashes(old_records[url]) != _hashes(new_records[url])]
    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "old_pack_dir": str(old_pack_dir.resolve()),
        "new_pack_dir": str(new_pack_dir.resolve()),
        "added_urls": sorted(new_urls - old_urls),
        "removed_urls": sorted(old_urls - new_urls),
        "changed_urls": changed_urls,
        "unchanged_urls": [url for url in shared_urls if url not in changed_urls],
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
    direct = _read_json(pack_dir / "parallel.pack.json", required=False)
    if isinstance(direct, dict):
        return direct
    candidates = sorted(pack_dir.glob("*.pack.json"))
    for candidate in candidates:
        parsed = _read_json(candidate, required=False)
        if isinstance(parsed, dict):
            return parsed
    return {}


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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _expected_domains(parallel_pack: dict[str, Any]) -> list[str]:
    request_options = parallel_pack.get("request_options") if isinstance(parallel_pack, dict) else {}
    if not isinstance(request_options, dict):
        metadata = parallel_pack.get("metadata") if isinstance(parallel_pack, dict) else {}
        request_options = metadata.get("request_options") if isinstance(metadata, dict) else {}
    source_policy = request_options.get("source_policy") if isinstance(request_options, dict) else {}
    include_domains = source_policy.get("include_domains") if isinstance(source_policy, dict) else []
    return [str(domain).lower().removeprefix("www.") for domain in include_domains or []]


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
