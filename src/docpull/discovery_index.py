"""Discovery index: downloadable SQLite shards of page URLs, titles, and headings.

Maintainers build shards from synced DocPull projects. Users download shards once,
verify each against the manifest's sha256, and search them offline, so queries
never leave the machine. Shards hold no page bodies; fetch a hit through the local
pipeline to read it.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp

from . import __version__
from .context_query import _STOPWORDS
from .conversion.chunking import _split_on_headings, _strip_frontmatter
from .http.client import _ValidatedResolver
from .project import (
    ProjectError,
    _read_context_lock,
    _read_jsonl,
    _run_dir_for_id,
    find_project_root,
    project_paths,
)
from .security.url_validator import UrlValidator
from .web_search import SearchHit, WebSearchError, _allowed

SCHEMA_VERSION = 1
INDEX_DIR_ENV = "DOCPULL_INDEX_DIR"
INDEX_URL_ENV = "DOCPULL_INDEX_URL"
MANIFEST_NAME = "manifest.json"
MAX_MANIFEST_BYTES = 1_000_000
DEFAULT_MAX_BYTES = 500_000_000
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TIMEOUT = aiohttp.ClientTimeout(total=600, sock_read=60)


class DiscoveryIndexError(ValueError):
    """Invalid shard, manifest, or download."""


def default_index_dir() -> Path:
    configured = os.environ.get(INDEX_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "docpull" / "index"


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _headings(content: str) -> list[str]:
    _, body = _strip_frontmatter(content)
    return [heading.lstrip("#").strip() for heading, _ in _split_on_headings(body) if heading]


def _locked_rows(root: Path) -> list[tuple[str, str, str, str, str, str | None, str | None]]:
    paths = project_paths(root)
    lock = _read_context_lock(paths.context_lock)
    if not lock or not isinstance(lock.get("run_id"), str):
        raise DiscoveryIndexError(f"No locked run in {root}; run `docpull sync` first")
    _, run_dir = _run_dir_for_id(paths, lock["run_id"])
    rows = []
    for record in _read_jsonl(run_dir / "documents.jsonl"):
        url = record.get("url")
        content = record.get("content")
        if not isinstance(url, str) or urlsplit(url).scheme != "https" or not isinstance(content, str):
            continue
        metadata = record.get("metadata")
        source = metadata.get("docpull_project_source") if isinstance(metadata, dict) else None
        rows.append(
            (
                url,
                str(record.get("title") or ""),
                "\n".join(_headings(content)),
                urlsplit(url).hostname or "",
                str(record.get("content_hash") or ""),
                record.get("fetched_at") if isinstance(record.get("fetched_at"), str) else None,
                source if isinstance(source, str) else None,
            )
        )
    return rows


def build_shard(project_roots: list[Path], out_dir: Path, name: str) -> dict[str, Any]:
    """Write ``<name>.sqlite`` from locked project runs and record it in ``manifest.json``."""
    if not _NAME.fullmatch(name):
        raise DiscoveryIndexError("shard name must be 1-64 lowercase letters, digits, '.', '_' or '-'")
    if not project_roots:
        raise DiscoveryIndexError("build needs at least one --project")
    rows: dict[str, tuple[str, str, str, str, str, str | None, str | None]] = {}
    try:
        for root in project_roots:
            for row in _locked_rows(find_project_root(root)):
                rows.setdefault(row[0], row)
    except ProjectError as err:
        raise DiscoveryIndexError(str(err)) from err
    if not rows:
        raise DiscoveryIndexError("No HTTPS documents found in the locked runs")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.sqlite"
    with tempfile.NamedTemporaryFile(dir=out_dir, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with closing(sqlite3.connect(temporary)) as conn, conn:
            conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                "CREATE TABLE pages (url TEXT PRIMARY KEY, title TEXT, headings TEXT, host TEXT, "
                "content_hash TEXT, fetched_at TEXT, source TEXT)"
            )
            conn.execute(
                "CREATE VIRTUAL TABLE pages_fts USING fts5(title, headings, url, content='pages', "
                "content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')"
            )
            conn.executemany(
                "INSERT INTO meta VALUES (?, ?)", [("name", name), ("docpull_version", __version__)]
            )
            conn.executemany("INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)", sorted(rows.values()))
            conn.execute("INSERT INTO pages_fts(pages_fts) VALUES ('rebuild')")
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        with closing(sqlite3.connect(temporary)) as conn:
            conn.execute("VACUUM")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    entry = {
        "name": name,
        "url": target.name,
        "sha256": _sha256_file(target),
        "bytes": target.stat().st_size,
        "documents": len(rows),
        "domains": sorted({row[3] for row in rows.values() if row[3]}),
    }
    manifest_path = out_dir / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    shards = [s for s in manifest.get("shards", []) if isinstance(s, dict) and s.get("name") != name]
    manifest = {"schema_version": SCHEMA_VERSION, "shards": sorted([*shards, entry], key=lambda s: s["name"])}
    _write_atomic(manifest_path, (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode())
    return entry


async def _fetch_bytes(url: str, limit: int) -> bytes:
    """GET an HTTPS URL through the DNS-pinned private-network guard, refusing redirects."""
    validator = UrlValidator(allowed_schemes={"https"})
    result = validator.validate(url)
    if not result.is_valid:
        raise DiscoveryIndexError(f"Refusing {url}: {result.rejection_reason}")
    headers = {"User-Agent": f"docpull/{__version__} (+https://github.com/raintree-technology/docpull)"}
    connector = aiohttp.TCPConnector(resolver=_ValidatedResolver(validator))
    try:
        async with (
            aiohttp.ClientSession(connector=connector, timeout=_TIMEOUT, headers=headers) as session,
            session.get(url, allow_redirects=False) as response,
        ):
            if response.status != 200:
                raise DiscoveryIndexError(f"{url} returned HTTP {response.status}")
            if (response.content_length or 0) > limit:
                raise DiscoveryIndexError(f"{url} is larger than the {limit}-byte limit")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise DiscoveryIndexError(f"{url} is larger than the {limit}-byte limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except (aiohttp.ClientError, OSError, TimeoutError) as err:
        raise DiscoveryIndexError(f"Download failed for {url}: {type(err).__name__}: {err}") from err


def _manifest_shards(manifest: Any, manifest_url: str) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise DiscoveryIndexError(f"Unsupported discovery manifest; expected schema_version {SCHEMA_VERSION}")
    shards = manifest.get("shards")
    if not isinstance(shards, list):
        raise DiscoveryIndexError("Discovery manifest has no shard list")
    checked = []
    for shard in shards:
        if (
            not isinstance(shard, dict)
            or not isinstance(shard.get("name"), str)
            or not _NAME.fullmatch(shard["name"])
            or not isinstance(shard.get("url"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", str(shard.get("sha256")))
            or type(shard.get("bytes")) is not int
            or shard["bytes"] < 1
        ):
            raise DiscoveryIndexError(f"Invalid shard entry in manifest: {shard!r}")
        checked.append({**shard, "url": urljoin(manifest_url, shard["url"])})
    return checked


async def pull(
    manifest_url: str,
    index_dir: Path | None = None,
    *,
    shards: list[str] | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Download manifest shards that are missing or stale, verifying each sha256."""
    index_dir = index_dir or default_index_dir()
    raw = await _fetch_bytes(manifest_url, MAX_MANIFEST_BYTES)
    try:
        entries = _manifest_shards(json.loads(raw), manifest_url)
    except json.JSONDecodeError as err:
        raise DiscoveryIndexError("Discovery manifest is not valid JSON") from err
    if shards:
        unknown = sorted(set(shards) - {entry["name"] for entry in entries})
        if unknown:
            raise DiscoveryIndexError(f"Manifest has no shard named: {', '.join(unknown)}")
        entries = [entry for entry in entries if entry["name"] in shards]
    targets = [(entry, index_dir / f"{entry['name']}.sqlite") for entry in entries]
    pending = [(e, t) for e, t in targets if not (t.is_file() and _sha256_file(t) == e["sha256"])]
    needed = sum(entry["bytes"] for entry, _ in pending)
    if needed > max_bytes:
        raise DiscoveryIndexError(
            f"Shards need {needed} bytes, over the {max_bytes}-byte limit; raise --max-bytes"
        )
    downloaded = []
    for entry, target in pending:
        data = await _fetch_bytes(entry["url"], entry["bytes"])
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise DiscoveryIndexError(f"sha256 mismatch for shard {entry['name']}; nothing installed for it")
        _write_atomic(target, data)
        downloaded.append(entry["name"])
    local = {"schema_version": SCHEMA_VERSION, "source": manifest_url, "shards": [e for e, _ in targets]}
    _write_atomic(index_dir / MANIFEST_NAME, (json.dumps(local, indent=2, sort_keys=True) + "\n").encode())
    return {
        "index_dir": str(index_dir),
        "downloaded": downloaded,
        "up_to_date": [e["name"] for e, _ in targets if e["name"] not in downloaded],
        "bytes": needed,
    }


def search_index(
    query: str,
    *,
    index_dir: Path | None = None,
    max_results: int = 10,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> list[SearchHit]:
    """Rank installed shards' pages by title, heading, and URL matches. Never touches the network."""
    index_dir = index_dir or default_index_dir()
    shard_paths = sorted(index_dir.glob("*.sqlite")) if index_dir.is_dir() else []
    if not shard_paths:
        raise WebSearchError(f"No discovery index in {index_dir}; run `docpull index pull <manifest-url>`")
    terms = sorted(set(re.findall(r"[^\W_]+(?:[_-][^\W_]+)*", query.casefold())) - _STOPWORDS)
    if not terms:
        raise WebSearchError("query must contain a searchable term")
    match = " OR ".join(f'"{term}"' for term in terms)
    include, exclude = include_domains or [], exclude_domains or []
    # Overfetch so domain filters still leave max_results hits.
    limit = max_results * 5 if include or exclude else max_results
    scored: dict[str, tuple[float, SearchHit]] = {}
    for path in shard_paths:
        try:
            with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True)) as conn:
                if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    continue
                # ponytail: bm25 depends on each shard's own statistics, so cross-shard
                # order is approximate; merge into one shard if exact global ranking matters.
                rows = conn.execute(
                    "SELECT p.url, p.title, p.headings, bm25(pages_fts, 5.0, 2.0, 1.0) AS rank "
                    "FROM pages_fts JOIN pages p ON p.rowid = pages_fts.rowid "
                    "WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match, limit),
                ).fetchall()
        except sqlite3.Error as err:
            raise WebSearchError(f"Unreadable discovery shard {path.name}: {err}") from err
        for url, title, headings, rank in rows:
            if not _allowed(url, include, exclude):
                continue
            hit = SearchHit(
                url=url, title=title or "", snippet=" · ".join((headings or "").splitlines()[:8]), score=-rank
            )
            if url not in scored or scored[url][0] < -rank:
                scored[url] = (-rank, hit)
    ranked = sorted(scored.values(), key=lambda item: (-item[0], item[1].url))
    return [hit for _, hit in ranked[:max_results]]


def run_index_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull index", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--index-dir", type=Path, help=f"Installed shard directory (default: ${INDEX_DIR_ENV})"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="Build a shard from synced projects")
    build.add_argument("--project", action="append", type=Path, required=True, dest="projects")
    build.add_argument("--out", type=Path, required=True, help="Directory for the shard and manifest.json")
    build.add_argument("--name", required=True, help="Shard name, e.g. python-docs")
    pull_parser = subparsers.add_parser("pull", help="Download and verify shards from a manifest URL")
    pull_parser.add_argument(
        "manifest_url", nargs="?", help=f"HTTPS manifest URL (default: ${INDEX_URL_ENV})"
    )
    pull_parser.add_argument("--shard", action="append", dest="shards", help="Only this shard (repeatable)")
    pull_parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    subparsers.add_parser("list", help="List installed shards")
    args = parser.parse_args(argv)
    index_dir = args.index_dir or default_index_dir()
    try:
        if args.command == "build":
            entry = build_shard(args.projects, args.out, args.name)
            print(f"Built {entry['name']}: {entry['documents']} pages, {entry['bytes']} bytes -> {args.out}")
        elif args.command == "pull":
            url = args.manifest_url or os.environ.get(INDEX_URL_ENV)
            if not url:
                raise DiscoveryIndexError(f"pull needs a manifest URL or {INDEX_URL_ENV}")
            result = asyncio.run(pull(url, index_dir, shards=args.shards, max_bytes=args.max_bytes))
            print(
                f"Downloaded {len(result['downloaded'])} shard(s), "
                f"{len(result['up_to_date'])} already current -> {result['index_dir']}"
            )
        else:
            for path in sorted(index_dir.glob("*.sqlite")) if index_dir.is_dir() else []:
                print(f"{path.stem}\t{path.stat().st_size} bytes")
    except DiscoveryIndexError as err:
        print(f"docpull index: {err}", file=sys.stderr)
        return 1
    return 0


__all__ = ["DiscoveryIndexError", "build_shard", "default_index_dir", "pull", "run_index_cli", "search_index"]
