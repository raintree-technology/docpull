"""Tool implementations for the docpull MCP server.

Each function returns a ``ToolResult`` with a human-readable ``text`` and
an optional structured ``data`` dict. The server wraps both into an MCP
``CallToolResult`` (with ``structuredContent`` validated against the
tool's ``outputSchema``).

Tools share state via module-level caches (source config, docs dir); the
server wires them up at startup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..core.fetcher import Fetcher
from ..models.config import CrawlConfig, DocpullConfig, OutputConfig, ProfileName
from ..security.url_validator import UrlValidator
from .sources import (
    _URL_SCHEME_RE,
    BUILTIN_SOURCES,
    all_sources,
    default_config_dir,
    default_docs_dir,
    is_safe_library_name,
    resolve_source,
    sources_config_path,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days
MAX_GREP_PATTERN_LEN = 1000
GREP_TIMEOUT_SECONDS = 10.0
MAX_READ_DOC_BYTES = 1_000_000

_FETCH_URL_VALIDATOR = UrlValidator(allowed_schemes={"https"})


@dataclass
class ToolResult:
    """Tool return value.

    ``text`` is the human-readable rendering — always present, always shown.
    ``data`` is the optional machine-parseable payload for clients that
    consume ``structuredContent``. ``is_error`` flips the MCP ``isError``
    flag and suppresses ``data`` (errors don't need to validate against
    ``outputSchema``).
    """

    text: str
    is_error: bool = False
    data: dict[str, Any] | None = None


def _meta_path(docs_dir: Path, source: str) -> Path:
    return docs_dir / f".{source}.meta.json"


def _source_dir(docs_dir: Path, source: str) -> Path:
    return docs_dir / source


def _cache_fresh(meta_path: Path) -> bool:
    if not meta_path.exists():
        return False
    try:
        data = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    fetched_at = data.get("fetched_at_epoch")
    if not isinstance(fetched_at, (int, float)):
        return False
    if data.get("partial") is True:
        return False
    return (time.time() - fetched_at) < CACHE_TTL_SECONDS


def _write_meta(meta_path: Path, source: str, url: str, pages: int) -> None:
    """Atomic-ish meta write: tmp file + rename so a crash mid-write
    never leaves a half-parsed JSON behind."""
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "source": source,
                "url": url,
                "fetched_at_epoch": time.time(),
                "fetched_at": datetime.now().isoformat(),
                "page_count": pages,
            },
            indent=2,
        )
    )
    os.replace(tmp, meta_path)


def _write_partial_meta(meta_path: Path, source: str, url: str, pages: int) -> None:
    """Mark a fetch as partial. ``_cache_fresh`` treats partial as stale."""
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(
            {
                "source": source,
                "url": url,
                "fetched_at_epoch": time.time(),
                "fetched_at": datetime.now().isoformat(),
                "page_count": pages,
                "partial": True,
            },
            indent=2,
        )
    )
    os.replace(tmp, meta_path)


# Profiles the MCP server exposes. CUSTOM is intentionally excluded — it is
# the marker for "ad-hoc config", not something an agent should opt into.
_AGENT_PROFILES: tuple[ProfileName, ...] = (
    ProfileName.RAG,
    ProfileName.MIRROR,
    ProfileName.QUICK,
    ProfileName.LLM,
)


def _resolve_profile(profile: str | None) -> ProfileName:
    if profile is None:
        return ProfileName.RAG
    name = profile.strip().lower()
    try:
        resolved = ProfileName(name)
    except ValueError:
        valid = ", ".join(p.value for p in _AGENT_PROFILES)
        raise ValueError(f"Unknown profile '{profile}'. Valid: {valid}") from None
    if resolved not in _AGENT_PROFILES:
        valid = ", ".join(p.value for p in _AGENT_PROFILES)
        raise ValueError(f"Profile '{profile}' is not exposed to agents. Valid: {valid}")
    return resolved


async def ensure_docs(
    source: str,
    *,
    force: bool = False,
    profile: str | None = None,
    docs_dir: Path | None = None,
    on_progress: Callable[[int, int | None], Awaitable[None]] | None = None,
) -> ToolResult:
    """Fetch docs for a configured alias; use cached content if fresh.

    Args:
        source: alias name from sources.yaml.
        force: re-fetch even if a fresh cached copy exists.
        profile: docpull profile name (rag/mirror/quick/llm). Defaults
            to rag — the right answer for most agent loops, but mirror
            or llm may be preferable for specific use cases.
        on_progress: optional async callback ``(pages_done, max_pages)``
            invoked once per FETCH_COMPLETED event. Used by the MCP
            server to forward progress notifications to clients.
    """
    docs_dir = docs_dir or default_docs_dir()
    resolved = resolve_source(source)
    if resolved is None:
        if _URL_SCHEME_RE.match(source):
            return ToolResult(
                f"Direct URLs are disabled. Add an alias in {sources_config_path()} "
                "and call ensure_docs with that name.",
                is_error=True,
            )
        available = ", ".join(sorted(all_sources().keys()))
        return ToolResult(
            f"Unknown source '{source}'. Available: {available}",
            is_error=True,
        )
    try:
        profile_enum = _resolve_profile(profile)
    except ValueError as err:
        return ToolResult(str(err), is_error=True)

    target_dir = _source_dir(docs_dir, source)
    meta_path = _meta_path(docs_dir, source)

    if not force and _cache_fresh(meta_path) and target_dir.exists() and any(target_dir.rglob("*.md")):
        files = list(target_dir.rglob("*.md"))
        return ToolResult(
            f"Cached: {source} ({len(files)} files at {target_dir}). Call with force=true to refresh.",
            data={
                "source": source,
                "cached": True,
                "file_count": len(files),
                "target_dir": str(target_dir),
            },
        )

    config = DocpullConfig(
        url=resolved.url,
        profile=profile_enum,
        crawl=CrawlConfig(max_pages=resolved.max_pages) if resolved.max_pages else CrawlConfig(),
        output=OutputConfig(directory=target_dir),
    )
    fetched = 0
    crashed = False
    try:
        async with Fetcher(config) as fetcher:
            async for event in fetcher.run():
                if event.type.value == "fetch_completed":
                    fetched += 1
                    if on_progress is not None:
                        await on_progress(fetched, resolved.max_pages)
            stats = fetcher.stats
    except Exception:
        crashed = True
        # Mark whatever made it to disk as a partial fetch so future
        # ensure_docs calls re-fetch instead of trusting half a crawl.
        _write_partial_meta(meta_path, source, resolved.url, fetched)
        raise

    if not crashed:
        _write_meta(meta_path, source, resolved.url, stats.pages_fetched)
    return ToolResult(
        f"Fetched {source}: {stats.pages_fetched} pages saved to {target_dir} "
        f"({stats.pages_skipped} skipped, {stats.pages_failed} failed).",
        data={
            "source": source,
            "cached": False,
            "pages_fetched": stats.pages_fetched,
            "pages_skipped": stats.pages_skipped,
            "pages_failed": stats.pages_failed,
            "target_dir": str(target_dir),
        },
    )


async def fetch_url(url: str, *, max_tokens: int | None = None) -> ToolResult:
    """Fetch a single arbitrary URL and return its Markdown.

    This is the agent-friendly tool: no discovery, no crawling, just one page.
    Validates URL upfront against the same SSRF rules the crawler uses
    (HTTPS only, no private IPs, no localhost, no link-local) so a confused
    agent can't aim it at cloud-metadata or internal services.
    """
    validation = _FETCH_URL_VALIDATOR.validate(url)
    if not validation.is_valid:
        return ToolResult(f"URL rejected: {validation.rejection_reason}", is_error=True)

    output_cfg = OutputConfig(max_tokens_per_file=max_tokens) if max_tokens else OutputConfig()
    config = DocpullConfig(
        url=url,
        profile=ProfileName.CUSTOM,
        output=output_cfg,
    )
    async with Fetcher(config) as fetcher:
        ctx = await fetcher.fetch_one(url, save=False)

    if ctx.error:
        return ToolResult(f"Failed: {ctx.error}", is_error=True)
    if ctx.should_skip:
        return ToolResult(f"Skipped: {ctx.skip_reason}", is_error=True)
    body = ctx.markdown or ""
    if ctx.chunks:
        parts = [
            f"## Chunk {getattr(c, 'index', 0)} "
            f"(tokens={getattr(c, 'token_count', '?')})\n\n{getattr(c, 'text', '')}"
            for c in ctx.chunks
        ]
        body = "\n\n".join(parts)
    chunks_meta = f" _chunks: {len(ctx.chunks)}_" if ctx.chunks else ""
    header = f"# {ctx.title or url}\n_source: {url}_ _type: {ctx.source_type or 'generic'}_{chunks_meta}\n\n"
    return ToolResult(header + body)


def list_sources(category: str | None = None) -> ToolResult:
    """List all configured sources, optionally filtered by category."""
    sources = all_sources()
    rows: list[str] = []
    payload: list[dict[str, Any]] = []
    for name, cfg in sorted(sources.items()):
        if category and cfg.category != category:
            continue
        rows.append(f"- **{name}** ({cfg.category}) — {cfg.description} → {cfg.url}")
        entry: dict[str, Any] = {
            "name": name,
            "url": cfg.url,
            "description": cfg.description,
            "category": cfg.category,
        }
        if cfg.max_pages is not None:
            entry["max_pages"] = cfg.max_pages
        payload.append(entry)
    if not rows:
        return ToolResult(
            f"No sources found for category '{category}'.",
            data={"sources": []},
        )
    header = f"Sources ({len(rows)}):\n\n"
    return ToolResult(header + "\n".join(rows), data={"sources": payload})


def _read_meta_fetched_at(meta_path: Path) -> tuple[float | None, str | None]:
    """Return (epoch, iso) for a meta file, or (None, None) on error."""
    if not meta_path.exists():
        return (None, None)
    try:
        data = json.loads(meta_path.read_text())
    except (OSError, json.JSONDecodeError):
        return (None, None)
    epoch = data.get("fetched_at_epoch")
    iso = data.get("fetched_at")
    return (epoch if isinstance(epoch, (int, float)) else None, iso if isinstance(iso, str) else None)


def _humanize_age(seconds: float) -> str:
    """Compact human-readable age string."""
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    return f"{int(seconds / 86400)}d ago"


def list_indexed(docs_dir: Path | None = None) -> ToolResult:
    """List sources that have local fetched docs, with last-fetched age."""
    docs_dir = docs_dir or default_docs_dir()
    if not docs_dir.exists():
        return ToolResult("No docs fetched yet.", data={"libraries": []})
    rows: list[str] = []
    payload: list[dict[str, Any]] = []
    now = time.time()
    for sub in sorted(docs_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        files = list(sub.rglob("*.md"))
        meta = _meta_path(docs_dir, sub.name)
        epoch, iso = _read_meta_fetched_at(meta)
        if epoch is None:
            age_str = "unknown age"
            fresh = "stale"
        else:
            age_str = _humanize_age(now - epoch)
            fresh = "fresh" if _cache_fresh(meta) else "stale"
        when = f" — fetched {age_str}" if iso else ""
        rows.append(f"- **{sub.name}**: {len(files)} files ({fresh}){when}")
        entry: dict[str, Any] = {
            "name": sub.name,
            "file_count": len(files),
            "fresh": fresh == "fresh",
        }
        if iso is not None:
            entry["fetched_at"] = iso
        if epoch is not None:
            entry["age_seconds"] = int(now - epoch)
        payload.append(entry)
    if not rows:
        return ToolResult(f"No fetched docs under {docs_dir}.", data={"libraries": []})
    return ToolResult(
        f"Fetched docs at {docs_dir}:\n\n" + "\n".join(rows),
        data={"libraries": payload},
    )


@dataclass
class _FileHits:
    """Matches collected from a single file before ranking.

    Each match is ``(lineno, before_lines, hit_line, after_lines)`` where
    ``before_lines`` / ``after_lines`` are 0..context lines of context.

    ``library`` and ``path`` are split so that ``path`` is relative to the
    library root and can be passed straight into ``read_doc`` alongside
    ``library``. Human-readable rendering still uses ``library/path``.
    """

    library: str
    path: str
    matches: list[tuple[int, list[str], str, list[str]]]


def grep_docs(
    pattern: str,
    *,
    library: str | None = None,
    limit: int = 20,
    docs_dir: Path | None = None,
    case_sensitive: bool = False,
    context: int = 1,
) -> ToolResult:
    """Grep through fetched Markdown and return ranked matches with context.

    Files are ranked by match density (matches per file) so the highest-
    signal results appear first. Each match is rendered with ``context``
    lines of surrounding context above and below (default 1, max 3).

    Hardened against (a) path traversal via ``library`` (rejected by
    ``is_safe_library_name``) and (b) catastrophic regex via a pattern
    length cap and a wall-clock budget. Python's ``re`` has no built-in
    timeout, so the budget is checked between files; a single pathological
    pattern+line combination can still wedge for one file's worth of work.
    """
    docs_dir = docs_dir or default_docs_dir()
    if not docs_dir.exists():
        return ToolResult("No docs fetched yet. Run ensure_docs first.", is_error=True)

    if len(pattern) > MAX_GREP_PATTERN_LEN:
        return ToolResult(
            f"Pattern too long ({len(pattern)} > {MAX_GREP_PATTERN_LEN} chars).",
            is_error=True,
        )
    if library is not None and not is_safe_library_name(library):
        return ToolResult(
            f"Invalid library name '{library}'. Use the names from list_indexed.",
            is_error=True,
        )
    context = max(0, min(context, 3))

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as err:
        return ToolResult(f"Invalid pattern: {err}", is_error=True)

    roots = (
        [docs_dir / library]
        if library
        else [p for p in docs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")]
    )

    deadline = time.monotonic() + GREP_TIMEOUT_SECONDS
    file_hits: list[_FileHits] = []
    total = 0
    timed_out = False
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for file in root.rglob("*.md"):
            if time.monotonic() > deadline:
                timed_out = True
                break
            try:
                lines = file.read_text(errors="replace").splitlines()
            except OSError as err:
                logger.debug("skip %s: %s", file, err)
                continue
            matches: list[tuple[int, list[str], str, list[str]]] = []
            for idx, line in enumerate(lines):
                if regex.search(line):
                    before = [lines[i].rstrip() for i in range(max(0, idx - context), idx)] if context else []
                    after = (
                        [lines[i].rstrip() for i in range(idx + 1, min(len(lines), idx + 1 + context))]
                        if context
                        else []
                    )
                    matches.append((idx + 1, before, line.rstrip(), after))
            if matches:
                file_hits.append(
                    _FileHits(
                        library=root.name,
                        path=str(file.relative_to(root)),
                        matches=matches,
                    )
                )
                total += len(matches)
        if timed_out:
            break

    if not file_hits:
        suffix = " (search timed out)" if timed_out else ""
        return ToolResult(
            f"No matches for '{pattern}'{suffix}.",
            data={
                "pattern": pattern,
                "total_matches": 0,
                "files": [],
                "truncated": False,
                "timed_out": timed_out,
            },
        )

    # Rank by raw count; tie-break alphabetically so output is stable.
    file_hits.sort(key=lambda fh: (-len(fh.matches), fh.library, fh.path))

    blocks: list[str] = []
    files_payload: list[dict[str, Any]] = []
    rendered = 0
    for fh in file_hits:
        if rendered >= limit:
            break
        qualified = f"{fh.library}/{fh.path}"
        block_lines = [f"## {qualified} ({len(fh.matches)} matches)"]
        rendered_matches: list[dict[str, Any]] = []
        for lineno, before, hit, after in fh.matches:
            if rendered >= limit:
                break
            chunk = []
            for off, line in enumerate(before):
                chunk.append(f"  {lineno - len(before) + off:>4}- {line}")
            chunk.append(f"> {lineno:>4}  {hit}")
            for off, line in enumerate(after, start=1):
                chunk.append(f"  {lineno + off:>4}- {line}")
            block_lines.append("\n".join(chunk))
            rendered_matches.append({"lineno": lineno, "before": before, "line": hit, "after": after})
            rendered += 1
        blocks.append("\n\n".join(block_lines))
        files_payload.append(
            {
                "library": fh.library,
                "path": fh.path,
                "match_count": len(fh.matches),
                "matches": rendered_matches,
            }
        )

    truncated = total > rendered
    truncated_note = (
        f"\n\n_({total - rendered} more match(es) hidden — increase `limit` to see them.)_"
        if truncated
        else ""
    )
    timeout_note = "\n\n_(search timed out before all libraries were scanned)_" if timed_out else ""
    header = f"{total} match(es) for '{pattern}' across {len(file_hits)} file(s):\n\n"
    return ToolResult(
        header + "\n\n---\n\n".join(blocks) + truncated_note + timeout_note,
        data={
            "pattern": pattern,
            "total_matches": total,
            "files": files_payload,
            "truncated": truncated,
            "timed_out": timed_out,
        },
    )


def read_doc(
    library: str,
    path: str,
    *,
    docs_dir: Path | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
) -> ToolResult:
    """Read a Markdown file from a fetched library, optionally line-sliced.

    The natural follow-up to ``grep_docs``: each grep result returns
    ``library`` and ``path`` (path relative to the library root), so
    ``read_doc(library=..., path=..., line_start=N-20, line_end=N+20)``
    pulls the surrounding context. Path is validated against
    ``docs_dir / library`` to block traversal.
    """
    docs_dir = docs_dir or default_docs_dir()
    if not is_safe_library_name(library):
        return ToolResult(
            f"Invalid library name '{library}'. Use names from list_indexed.",
            is_error=True,
        )
    library_root = (docs_dir / library).resolve()
    if not library_root.exists() or not library_root.is_dir():
        return ToolResult(
            f"Library '{library}' not found. Run ensure_docs first.",
            is_error=True,
        )
    target = (library_root / path).resolve()
    try:
        target.relative_to(library_root)
    except ValueError:
        return ToolResult(f"Path '{path}' escapes library '{library}'.", is_error=True)
    if not target.exists() or not target.is_file():
        return ToolResult(f"File not found: {library}/{path}", is_error=True)
    if target.stat().st_size > MAX_READ_DOC_BYTES:
        return ToolResult(
            f"File too large ({target.stat().st_size} bytes > {MAX_READ_DOC_BYTES}). "
            "Use line_start/line_end to slice.",
            is_error=True,
        )
    text = target.read_text(errors="replace")
    total_lines = text.count("\n") + 1 if text else 0
    if line_start is None and line_end is None:
        return ToolResult(
            f"# {library}/{path}\n\n{text}",
            data={
                "library": library,
                "path": path,
                "line_start": 1,
                "line_end": total_lines,
                "total_lines": total_lines,
                "text": text,
            },
        )
    lines = text.splitlines()
    start = max(1, line_start or 1)
    end = min(len(lines), line_end or len(lines))
    if start > end:
        return ToolResult(
            f"Empty slice: line_start={line_start} > line_end={line_end}.",
            is_error=True,
        )
    sliced = "\n".join(lines[start - 1 : end])
    header = f"# {library}/{path} (lines {start}–{end} of {len(lines)})\n\n"
    return ToolResult(
        header + sliced,
        data={
            "library": library,
            "path": path,
            "line_start": start,
            "line_end": end,
            "total_lines": len(lines),
            "text": sliced,
        },
    )


# --- Write tools: extending / shrinking the source registry ----------

MAX_DESCRIPTION_LEN = 500
ALLOWED_USER_CATEGORIES = {"frontend", "backend", "ai", "database", "user"}
_ADD_SOURCE_VALIDATOR = UrlValidator(allowed_schemes={"https"})


def _user_sources_path(config_dir: Path | None = None) -> Path:
    """Where the writable user sources.yaml lives."""
    return (config_dir or default_config_dir()) / "sources.yaml"


def _write_user_sources(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Atomic write of the user sources.yaml. Last writer wins — there is
    no cross-process lock; that's fine for the single-process MCP server
    but worth knowing if anyone else is editing the file by hand."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump({"sources": entries}, sort_keys=True))
    os.replace(tmp, path)


def _read_user_sources_raw(path: Path) -> dict[str, dict[str, Any]]:
    """Load the user sources.yaml as raw dicts (not SourceConfig).

    We need the raw dicts because we round-trip the file on every write,
    and ``load_user_sources`` lossily coerces to SourceConfig.
    """
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as err:
        logger.warning("user sources.yaml is malformed; treating as empty: %s", err)
        return {}
    entries = raw.get("sources") or {}
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in entries.items():
        if isinstance(cfg, dict) and isinstance(cfg.get("url"), str):
            out[str(name)] = dict(cfg)
    return out


def add_source(
    name: str,
    url: str,
    *,
    description: str | None = None,
    category: str | None = None,
    max_pages: int | None = None,
    force: bool = False,
    config_dir: Path | None = None,
) -> ToolResult:
    """Add or update a user source alias in ``sources.yaml``.

    Refuses to shadow a builtin alias unless ``force=True`` (the agent is
    explicitly choosing to override it). URL is validated with the same
    SSRF rules ``fetch_url`` uses.
    """
    if not is_safe_library_name(name):
        return ToolResult(
            f"Invalid source name '{name}'. Use alnum + ``_ . -``, max 128 chars.",
            is_error=True,
        )
    validation = _ADD_SOURCE_VALIDATOR.validate(url)
    if not validation.is_valid:
        return ToolResult(f"URL rejected: {validation.rejection_reason}", is_error=True)
    if description is not None and len(description) > MAX_DESCRIPTION_LEN:
        return ToolResult(f"Description too long (>{MAX_DESCRIPTION_LEN} chars).", is_error=True)
    if category is not None and category not in ALLOWED_USER_CATEGORIES:
        valid = ", ".join(sorted(ALLOWED_USER_CATEGORIES))
        return ToolResult(f"Unknown category '{category}'. Valid: {valid}", is_error=True)
    if max_pages is not None and (max_pages < 1 or max_pages > 100_000):
        return ToolResult("max_pages must be between 1 and 100000.", is_error=True)

    is_builtin = name in BUILTIN_SOURCES
    if is_builtin and not force:
        return ToolResult(
            f"'{name}' is a builtin source. Pass force=true to shadow it with a user override.",
            is_error=True,
        )

    path = _user_sources_path(config_dir)
    existing = _read_user_sources_raw(path)
    replaced = name in existing

    entry: dict[str, Any] = {"url": url}
    if description is not None:
        entry["description"] = description
    if category is not None:
        entry["category"] = category
    if max_pages is not None:
        entry["max_pages"] = max_pages

    existing[name] = entry
    _write_user_sources(path, existing)

    verb = "Updated" if replaced else "Added"
    note = " (overrides builtin)" if is_builtin else ""
    return ToolResult(
        f"{verb} source '{name}' → {url}{note}.",
        data={
            "name": name,
            "url": url,
            "replaced": replaced,
            "shadowed_builtin": is_builtin,
            "config_path": str(path),
        },
    )


def remove_source(
    name: str,
    *,
    delete_cache: bool = False,
    config_dir: Path | None = None,
    docs_dir: Path | None = None,
) -> ToolResult:
    """Remove a user source alias. Optionally delete its cached docs.

    Cannot remove a builtin (no force flag — builtins are part of the
    package, not the user's config). To stop using a builtin, shadow it
    via ``add_source(force=True)`` or just don't call ``ensure_docs`` on it.
    """
    if not is_safe_library_name(name):
        return ToolResult(
            f"Invalid source name '{name}'. Use alnum + ``_ . -``, max 128 chars.",
            is_error=True,
        )
    if name in BUILTIN_SOURCES:
        return ToolResult(
            f"'{name}' is a builtin source and cannot be removed. To stop using "
            "it, just don't call ensure_docs on it.",
            is_error=True,
        )

    path = _user_sources_path(config_dir)
    existing = _read_user_sources_raw(path)
    removed = name in existing
    if removed:
        del existing[name]
        _write_user_sources(path, existing)

    cache_deleted = False
    cache_dir = (docs_dir or default_docs_dir()) / name
    meta_file = _meta_path(docs_dir or default_docs_dir(), name)
    if delete_cache:
        if cache_dir.exists() and cache_dir.is_dir():
            # Defense in depth: confirm the resolved path is still under docs_dir
            # before rmtree-ing — is_safe_library_name should already have
            # blocked traversal, but a belt is cheap.
            base = (docs_dir or default_docs_dir()).resolve()
            try:
                cache_dir.resolve().relative_to(base)
            except ValueError:
                return ToolResult(
                    f"Refusing to delete '{cache_dir}' — outside docs_dir.",
                    is_error=True,
                )
            shutil.rmtree(cache_dir)
            cache_deleted = True
        if meta_file.exists():
            meta_file.unlink()

    if not removed and not cache_deleted:
        return ToolResult(
            f"No user source '{name}' to remove and no cache to delete.",
            data={"name": name, "removed": False, "cache_deleted": False},
        )

    parts = []
    if removed:
        parts.append(f"removed '{name}' from {path}")
    if cache_deleted:
        parts.append(f"deleted cached docs at {cache_dir}")
    return ToolResult(
        "Done: " + " and ".join(parts) + ".",
        data={
            "name": name,
            "removed": removed,
            "cache_deleted": cache_deleted,
            "config_path": str(path),
        },
    )
