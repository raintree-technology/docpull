"""Tool implementations for the docpull MCP server.

Each tool returns a plain dict that the server wraps into an MCP response.
Tools share state via module-level caches (source config, docs dir); the
server wires them up at startup.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.fetcher import Fetcher
from ..models.config import DocpullConfig, ProfileName
from .sources import (
    all_sources,
    default_docs_dir,
    resolve_source,
    sources_config_path,
)

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 7 * 24 * 60 * 60  # 7 days


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
    return (time.time() - fetched_at) < CACHE_TTL_SECONDS


def _write_meta(meta_path: Path, source: str, url: str, pages: int) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
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


_PROFILE_ALIASES: dict[str, ProfileName] = {
    "rag": ProfileName.RAG,
    "mirror": ProfileName.MIRROR,
    "quick": ProfileName.QUICK,
    "llm": ProfileName.LLM,
}


def _resolve_profile(profile: str | None) -> ProfileName:
    if profile is None:
        return ProfileName.RAG
    name = profile.strip().lower()
    if name not in _PROFILE_ALIASES:
        valid = ", ".join(sorted(_PROFILE_ALIASES))
        raise ValueError(f"Unknown profile '{profile}'. Valid: {valid}")
    return _PROFILE_ALIASES[name]


async def ensure_docs(
    source: str,
    *,
    force: bool = False,
    profile: str | None = None,
    docs_dir: Path | None = None,
) -> ToolResult:
    """Fetch docs for a configured alias; use cached content if fresh.

    Args:
        source: alias name from sources.yaml.
        force: re-fetch even if a fresh cached copy exists.
        profile: docpull profile name (rag/mirror/quick/llm). Defaults
            to rag — the right answer for most agent loops, but mirror
            or llm may be preferable for specific use cases.
    """
    docs_dir = docs_dir or default_docs_dir()
    resolved = resolve_source(source)
    if resolved is None:
        if re.match(r"^[a-z][a-z0-9+.-]*://", source, re.IGNORECASE):
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

    if not force and _cache_fresh(meta_path) and target_dir.exists():
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
    async with Fetcher(config) as fetcher:
        async for _ in fetcher.run():
            pass
        stats = fetcher.stats

    _write_meta(meta_path, source, resolved.url, stats.pages_fetched)
    return ToolResult(
        f"Fetched {source}: {stats.pages_fetched} pages saved to {target_dir} "
        f"({stats.pages_skipped} skipped, {stats.pages_failed} failed)."
    )


async def fetch_url(url: str, *, max_tokens: int | None = None) -> ToolResult:
    """Fetch a single arbitrary URL and return its Markdown.

    This is the agent-friendly tool: no discovery, no crawling, just one page.
    """
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
    """Matches collected from a single file before ranking."""

    rel_path: str
    matches: list[tuple[int, str, str, str]]  # (lineno, before, line, after)


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
    signal results appear first. Each match is rendered with one line of
    surrounding context above and below by default — controlled by
    ``context`` (set to 0 for no context).
    """
    docs_dir = docs_dir or default_docs_dir()
    if not docs_dir.exists():
        return ToolResult("No docs fetched yet. Run ensure_docs first.", is_error=True)

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

    file_hits: list[_FileHits] = []
    total = 0
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for file in root.rglob("*.md"):
            try:
                lines = file.read_text(errors="replace").splitlines()
            except OSError as err:
                logger.debug("skip %s: %s", file, err)
                continue
            matches: list[tuple[int, str, str, str]] = []
            for idx, line in enumerate(lines):
                if regex.search(line):
                    before = lines[idx - 1].rstrip() if context and idx > 0 else ""
                    after = (
                        lines[idx + 1].rstrip()
                        if context and idx + 1 < len(lines)
                        else ""
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

    if not file_hits:
        return ToolResult(f"No matches for '{pattern}'.")

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
            if before and context:
                chunk.append(f"  {lineno - 1:>4}- {before}")
            chunk.append(f"> {lineno:>4}  {hit}")
            if after and context:
                chunk.append(f"  {lineno + 1:>4}- {after}")
            block_lines.append("\n".join(chunk))
            rendered += 1
        blocks.append("\n\n".join(block_lines))

    truncated_note = (
        f"\n\n_({total - rendered} more match(es) hidden — increase `limit` to see them.)_"
        if total > rendered
        else ""
    )
    header = f"{total} match(es) for '{pattern}' across {len(file_hits)} file(s):\n\n"
    return ToolResult(header + "\n\n---\n\n".join(blocks) + truncated_note)
