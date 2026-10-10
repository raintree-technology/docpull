"""Offline, budgeted passage retrieval from verified project lock snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from .frozen import FrozenArtifactError, stage_run_artifact
from .pack_tools import _search_score, _term_count
from .project import (
    ProjectError,
    _content_hash_summary,
    _context_lock_hash,
    _read_context_lock,
    _read_json,
    _read_jsonl,
    _safe_run_id,
    _validate_context_lock,
    find_project_root,
    load_project_config,
    project_paths,
)

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "how",
        "to",
        "do",
        "does",
        "is",
        "are",
        "of",
        "for",
        "in",
        "on",
        "and",
        "with",
        "what",
        "i",
        "can",
    ]
)


class ContextQueryError(ValueError):
    """Invalid query input or unverifiable locked evidence."""


def serialize_query_result(payload: dict[str, Any]) -> str:
    """Return the canonical JSON representation covered by the token budget."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _measure(payload: dict[str, Any]) -> int:
    # One UTF-8 byte per budget token is a conservative bound for byte tokenizers.
    # Unlike optional tokenizer initialization, this cannot download model data.
    while True:
        size = len(serialize_query_result(payload).encode("utf-8"))
        if payload["budget"]["used_tokens"] == size:
            return size
        payload["budget"]["used_tokens"] = size


def _spans(content: str) -> Iterator[tuple[int, int]]:
    """Keep paragraphs and fenced code blocks whole, with exact character offsets."""
    start = offset = 0
    fence = ""
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*", line):
                fence = ""
                yield start, offset + len(line)
                start = offset + len(line)
        elif marker:
            if start < offset:
                yield start, offset
            start = offset
            fence = marker.group(1)
        elif not line.strip():
            if start < offset:
                yield start, offset
            start = offset + len(line)
        offset += len(line)
    if start < offset:
        yield start, offset


def _filters(values: list[str] | None, name: str) -> set[str]:
    if values is None:
        return set()
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise ContextQueryError(f"{name} must be a list of non-empty strings")
    return {v.strip() for v in values}


def _locked_records(
    root: Path, artifact_dirs: list[Path], pack_versions: set[str]
) -> Iterator[tuple[dict[str, Any], int, dict[str, Any]]]:
    config = load_project_config(root)
    paths = project_paths(root)
    lock = _read_context_lock(paths.context_lock)
    if not lock:
        raise ContextQueryError(f"No context lockfile in {root}; run `docpull sync` first")
    _validate_context_lock(config, lock)
    if lock.get("lock_hash") != _context_lock_hash(lock) or lock.get("project") != config.name:
        raise ContextQueryError(f"Invalid context lockfile in {root}")
    if not isinstance(lock.get("run_id"), str):
        raise ContextQueryError(f"No run pinned in {root}; run `docpull sync` first")
    run_id = _safe_run_id(lock["run_id"])
    if pack_versions and run_id not in pack_versions:
        return
    artifact = lock.get("artifact")
    if not isinstance(artifact, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(artifact.get("sha256"))):
        raise ContextQueryError(f"No frozen artifact pinned in {root}; run `docpull sync` first")
    stores = [paths.state / "artifacts", *artifact_dirs]
    store = next((p for p in stores if (p / f"{artifact['sha256']}.zip").is_file()), stores[0])
    with tempfile.TemporaryDirectory(prefix="docpull-query-") as temporary:
        staged = Path(temporary)
        stage_run_artifact(artifact, store, staged)
        run = _read_json(staged / "run.json")
        if not isinstance(run, dict) or run.get("run_id") != run_id or run.get("project") != config.name:
            raise ContextQueryError("Frozen artifact does not match the locked run")
        records = _read_jsonl(staged / "documents.ndjson")
        if sorted(str(row["content_hash"]) for row in records if row.get("content_hash")) != lock.get(
            "content_hashes"
        ) or _content_hash_summary(records) != lock.get("content_hash_summary"):
            raise ContextQueryError("Frozen artifact content does not match the lockfile")
        for index, record in enumerate(records, 1):
            if not isinstance(record.get("url"), str) or not record["url"].strip():
                raise ContextQueryError(f"Missing source URL in locked record {index}")
            content = record.get("content")
            if not isinstance(content, str) or hashlib.sha256(
                content.encode("utf-8")
            ).hexdigest() != record.get("content_hash"):
                raise ContextQueryError(f"Invalid content hash in locked record {index}")
            yield lock, index, record


def query_context(
    query: str,
    *,
    project_roots: Sequence[Path | str] | None = None,
    max_tokens: int = 4000,
    versions: list[str] | None = None,
    pack_versions: list[str] | None = None,
    sources: list[str] | None = None,
    limit: int = 10,
    artifact_dirs: Sequence[Path | str] | None = None,
) -> dict[str, Any]:
    """Retrieve cited passages from locked archives without acquiring or changing sources.

    ``versions`` matches explicit document ``metadata.version`` values; unknown
    versions never match. ``pack_versions`` matches exact locked run IDs.
    The complete canonical JSON result fits ``max_tokens`` using a conservative
    UTF-8 byte bound, including citations and budget metadata. Oversized passages
    are omitted whole, so code blocks and source offsets remain intact.
    """
    if not isinstance(query, str) or not query.strip():
        raise ContextQueryError("query must be a non-empty string")
    if type(max_tokens) is not int or max_tokens < 256:
        raise ContextQueryError("max_tokens must be an integer of at least 256")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ContextQueryError("limit must be an integer between 1 and 100")
    for values, name in ((project_roots, "project_roots"), (artifact_dirs, "artifact_dirs")):
        if values is not None and (
            isinstance(values, (str, bytes))
            or not isinstance(values, Sequence)
            or not all(isinstance(p, (str, Path)) and str(p).strip() for p in values)
        ):
            raise ContextQueryError(f"{name} must be a sequence of non-empty paths")
    source_versions = _filters(versions, "versions")
    runs = _filters(pack_versions, "pack_versions")
    source_names = _filters(sources, "sources")
    terms = sorted(set(re.findall(r"[^\W_]+(?:[_-][^\W_]+)*", query.casefold())) - _STOPWORDS)
    if not terms:
        raise ContextQueryError("query must contain a searchable term")
    groups: dict[str, dict[str, Any]] = {}
    try:
        roots = project_roots if project_roots is not None else [find_project_root(Path.cwd())]
        if not roots:
            raise ContextQueryError("project_roots must contain at least one project")
        stores = [Path(p).expanduser().resolve() for p in artifact_dirs or []]
        for root in sorted({Path(p).expanduser().resolve() for p in roots}):
            for lock, record_index, record in _locked_records(root, stores, runs):
                metadata = record.get("metadata") or {}
                version = metadata.get("version") if isinstance(metadata, dict) else None
                source = metadata.get("docpull_project_source") if isinstance(metadata, dict) else None
                if not isinstance(version, str):
                    version = None
                if not isinstance(source, str):
                    source = None
                if source_versions and version not in source_versions:
                    continue
                if source_names and source not in source_names:
                    continue
                content = record["content"]
                for start, end in _spans(content):
                    # Trim outside whitespace only; never rewrite captured passage text.
                    raw = content[start:end]
                    start += len(raw) - len(raw.lstrip())
                    end -= len(raw) - len(raw.rstrip())
                    text = content[start:end]
                    if not text or not any(_term_count(text, term) for term in terms):
                        continue
                    score, _ = _search_score(
                        query_terms=terms,
                        phrase=query.strip().lower(),
                        title=str(record.get("title") or ""),
                        heading="",
                        url=str(record.get("url") or ""),
                        content=text,
                    )
                    digest = lock["artifact"]["sha256"]
                    identity = f"{digest}:{record_index}:{start}:{end}"
                    citation = {
                        "id": "ctx_" + hashlib.sha256(identity.encode()).hexdigest()[:24],
                        "pack": lock["project"],
                        "pack_version": lock["run_id"],
                        "artifact_sha256": digest,
                        "source": source,
                        "version": version,
                        "url": record["url"],
                        "document_id": record.get("document_id"),
                        "record_index": record_index,
                        "content_hash": record["content_hash"],
                        "char_start": start,
                        "char_end": end,
                        "line_start": content.count("\n", 0, start) + 1,
                        "line_end": content.count("\n", 0, end - 1) + 1,
                    }
                    group = groups.setdefault(text, {"score": score, "text": text, "citations": {}})
                    group["score"] = max(group["score"], score)
                    group["citations"][citation["id"]] = citation
    except (ProjectError, FrozenArtifactError, OSError, UnicodeError) as err:
        raise ContextQueryError(str(err)) from err
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": "ok",
        "reason": None,
        "budget": {"max_tokens": max_tokens, "used_tokens": 0, "method": "utf8_bytes_upper_bound"},
        "results": [],
        "omitted_passages": 0,
    }
    ranked = sorted(groups.values(), key=lambda g: (-g["score"], g["text"]))
    deferred: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    payload["omitted_passages"] = len(ranked)
    for group in ranked:
        citations = sorted(group["citations"].values(), key=lambda c: c["id"])
        result = {"text": group["text"], "citations": citations[:1], "omitted_citations": len(citations) - 1}
        if len(payload["results"]) >= limit:
            break
        payload["results"].append(result)
        payload["omitted_passages"] -= 1
        if _measure(payload) > max_tokens:
            payload["results"].pop()
            payload["omitted_passages"] += 1
        else:
            deferred.append((result, citations[1:]))
    # Spend remaining space on duplicate provenance after selecting distinct passages.
    for result, citations in deferred:
        for citation in citations:
            result["citations"].append(citation)
            result["omitted_citations"] -= 1
            if _measure(payload) > max_tokens:
                result["citations"].pop()
                result["omitted_citations"] += 1
    if not payload["results"]:
        payload["status"] = "insufficient_evidence"
        payload["reason"] = "budget_exhausted" if ranked else "no_matches"
    if _measure(payload) > max_tokens:
        raise ContextQueryError("max_tokens is too small for response metadata")
    return payload


def run_query_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull query", description="Query verified locked context offline"
    )
    parser.add_argument("query")
    parser.add_argument("--project", action="append", type=Path, dest="project_roots")
    parser.add_argument("--max-tokens", type=int, default=4000)
    parser.add_argument("--version", action="append", dest="versions", help="Exact metadata.version filter")
    parser.add_argument("--pack-version", action="append", dest="pack_versions", help="Exact locked run ID")
    parser.add_argument("--source", action="append", dest="sources", help="Configured source name")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--artifact-dir", action="append", type=Path, dest="artifact_dirs")
    args = parser.parse_args(argv)
    try:
        payload = query_context(**vars(args))
    except ContextQueryError as err:
        print(f"Context query error: {err}", file=sys.stderr)
        return 1
    print(serialize_query_result(payload))
    return 0
