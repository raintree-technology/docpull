"""Tool implementations for the docpull MCP server.

Each tool returns a plain dict that the server wraps into an MCP response.
Tools share state via module-level caches (source config, docs dir); the
server wires them up at startup.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.fetcher import Fetcher
from ..models.config import DocpullConfig, ProfileName
from ..security.url_validator import UrlValidator
from .sources import (
    _URL_SCHEME_RE,
    all_sources,
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
    """Structured tool result. ``is_error`` controls how the server formats it."""

    text: str
    is_error: bool = False


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

    if (
        not force
        and _cache_fresh(meta_path)
        and target_dir.exists()
        and any(target_dir.rglob("*.md"))
    ):
        files = list(target_dir.rglob("*.md"))
        return ToolResult(
            f"Cached: {source} ({len(files)} files at {target_dir}). "
            "Call with force=true to refresh."
        )

    config = DocpullConfig(
        url=resolved.url,
        profile=profile_enum,
        crawl={"max_pages": resolved.max_pages} if resolved.max_pages else {},
        output={"directory": target_dir},
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
        f"({stats.pages_skipped} skipped, {stats.pages_failed} failed)."
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

    output_kwargs: dict[str, Any] = {}
    if max_tokens:
        output_kwargs["max_tokens_per_file"] = max_tokens
    config = DocpullConfig(
        url=url,
        profile=ProfileName.CUSTOM,
        output=output_kwargs or None,
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
    header = (
        f"# {ctx.title or url}\n"
        f"_source: {url}_ _type: {ctx.source_type or 'generic'}_{chunks_meta}\n\n"
    )
    return ToolResult(header + body)


def list_sources(category: str | None = None) -> ToolResult:
    """List all configured sources, optionally filtered by category."""
    sources = all_sources()
    rows: list[str] = []
    for name, cfg in sorted(sources.items()):
        if category and cfg.category != category:
            continue
        rows.append(f"- **{name}** ({cfg.category}) — {cfg.description} → {cfg.url}")
    if not rows:
        return ToolResult(f"No sources found for category '{category}'.")
    header = f"Sources ({len(rows)}):\n\n"
    return ToolResult(header + "\n".join(rows))


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
        return ToolResult("No docs fetched yet.")
    rows: list[str] = []
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
    if not rows:
        return ToolResult(f"No fetched docs under {docs_dir}.")
    return ToolResult(f"Fetched docs at {docs_dir}:\n\n" + "\n".join(rows))


@dataclass
class _FileHits:
    """Matches collected from a single file before ranking.

    Each match is ``(lineno, before_lines, hit_line, after_lines)`` where
    ``before_lines`` / ``after_lines`` are 0..context lines of context.
    """

    rel_path: str
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
                    before = (
                        [lines[i].rstrip() for i in range(max(0, idx - context), idx)]
                        if context
                        else []
                    )
                    after = (
                        [
                            lines[i].rstrip()
                            for i in range(idx + 1, min(len(lines), idx + 1 + context))
                        ]
                        if context
                        else []
                    )
                    matches.append((idx + 1, before, line.rstrip(), after))
            if matches:
                file_hits.append(
                    _FileHits(
                        rel_path=str(file.relative_to(docs_dir)),
                        matches=matches,
                    )
                )
                total += len(matches)
        if timed_out:
            break

    if not file_hits:
        suffix = " (search timed out)" if timed_out else ""
        return ToolResult(f"No matches for '{pattern}'{suffix}.")

    # Rank by raw count; tie-break alphabetically so output is stable.
    file_hits.sort(key=lambda fh: (-len(fh.matches), fh.rel_path))

    blocks: list[str] = []
    rendered = 0
    for fh in file_hits:
        if rendered >= limit:
            break
        block_lines = [f"## {fh.rel_path} ({len(fh.matches)} matches)"]
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
            rendered += 1
        blocks.append("\n\n".join(block_lines))

    truncated_note = (
        f"\n\n_({total - rendered} more match(es) hidden — increase `limit` to see them.)_"
        if total > rendered
        else ""
    )
    timeout_note = "\n\n_(search timed out before all libraries were scanned)_" if timed_out else ""
    header = f"{total} match(es) for '{pattern}' across {len(file_hits)} file(s):\n\n"
    return ToolResult(header + "\n\n---\n\n".join(blocks) + truncated_note + timeout_note)


def read_doc(
    library: str,
    path: str,
    *,
    docs_dir: Path | None = None,
    line_start: int | None = None,
    line_end: int | None = None,
) -> ToolResult:
    """Read a Markdown file from a fetched library, optionally line-sliced.

    The natural follow-up to ``grep_docs``: once you have ``library/path.md``
    and a line number, ``read_doc(library, path, line_start=N-20, line_end=N+20)``
    pulls the surrounding context without filesystem access. Path is validated
    against ``docs_dir / library`` to block traversal.
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
    if line_start is None and line_end is None:
        return ToolResult(f"# {library}/{path}\n\n{text}")
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
    return ToolResult(header + sliced)
