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


async def ensure_docs(
    source: str,
    *,
    force: bool = False,
    docs_dir: Path | None = None,
) -> ToolResult:
    """Fetch docs for a configured alias; use cached content if fresh."""
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
        profile=ProfileName.RAG,
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
    header = f"# {ctx.title or url}\n_source: {url}_ _type: {ctx.source_type or 'generic'}_\n\n"
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


def list_indexed(docs_dir: Path | None = None) -> ToolResult:
    """List sources that have local fetched docs."""
    docs_dir = docs_dir or default_docs_dir()
    if not docs_dir.exists():
        return ToolResult("No docs fetched yet.")
    rows: list[str] = []
    for sub in sorted(docs_dir.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        files = list(sub.rglob("*.md"))
        meta = _meta_path(docs_dir, sub.name)
        fresh = "fresh" if _cache_fresh(meta) else "stale"
        rows.append(f"- **{sub.name}**: {len(files)} files ({fresh})")
    if not rows:
        return ToolResult(f"No fetched docs under {docs_dir}.")
    return ToolResult(f"Fetched docs at {docs_dir}:\n\n" + "\n".join(rows))


def grep_docs(
    pattern: str,
    *,
    library: str | None = None,
    limit: int = 20,
    docs_dir: Path | None = None,
    case_sensitive: bool = False,
) -> ToolResult:
    """Grep through fetched Markdown files and return matching lines."""
    docs_dir = docs_dir or default_docs_dir()
    if not docs_dir.exists():
        return ToolResult("No docs fetched yet. Run ensure_docs first.", is_error=True)

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as err:
        return ToolResult(f"Invalid pattern: {err}", is_error=True)

    roots = [docs_dir / library] if library else [
        p for p in docs_dir.iterdir() if p.is_dir() and not p.name.startswith(".")
    ]
    hits: list[str] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for file in root.rglob("*.md"):
            try:
                for lineno, line in enumerate(file.read_text(errors="replace").splitlines(), 1):
                    if regex.search(line):
                        rel = file.relative_to(docs_dir)
                        hits.append(f"{rel}:{lineno}: {line.strip()}")
                        if len(hits) >= limit:
                            break
            except OSError as err:
                logger.debug("skip %s: %s", file, err)
                continue
            if len(hits) >= limit:
                break
        if len(hits) >= limit:
            break

    if not hits:
        return ToolResult(f"No matches for '{pattern}'.")
    return ToolResult(
        f"{len(hits)} match(es) for '{pattern}':\n\n```\n" + "\n".join(hits) + "\n```"
    )
