"""Built-in and user-configured documentation sources for the MCP server."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceConfig:
    """A named documentation source.

    Attributes:
        url: Start URL to crawl from.
        description: Human-readable description.
        category: Grouping hint (frontend, backend, ai, database, internal).
        max_pages: Optional per-source page cap.
    """

    url: str
    description: str
    category: str
    max_pages: int | None = None


# Curated set of popular dev-facing docs. Users can extend via sources.yaml.
BUILTIN_SOURCES: dict[str, SourceConfig] = {
    # Frontend
    "react": SourceConfig("https://react.dev", "React documentation", "frontend", 500),
    "nextjs": SourceConfig("https://nextjs.org/docs", "Next.js documentation", "frontend", 800),
    "tailwindcss": SourceConfig("https://tailwindcss.com/docs", "Tailwind CSS", "frontend", 300),
    "vite": SourceConfig("https://vite.dev/guide", "Vite build tool", "frontend", 200),
    # Backend
    "hono": SourceConfig("https://hono.dev/docs", "Hono web framework", "backend", 200),
    "fastapi": SourceConfig("https://fastapi.tiangolo.com", "FastAPI framework", "backend", 400),
    "express": SourceConfig("https://expressjs.com", "Express.js framework", "backend", 200),
    # AI
    "anthropic": SourceConfig("https://docs.anthropic.com", "Anthropic Claude API", "ai", 200),
    "openai": SourceConfig("https://platform.openai.com/docs", "OpenAI API", "ai", 400),
    "langchain": SourceConfig("https://python.langchain.com/docs", "LangChain framework", "ai", 1000),
    # Database
    "supabase": SourceConfig("https://supabase.com/docs", "Supabase documentation", "database", 600),
    "drizzle": SourceConfig("https://orm.drizzle.team/docs", "Drizzle ORM", "database", 300),
    "prisma": SourceConfig("https://www.prisma.io/docs", "Prisma ORM", "database", 500),
}


_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def default_config_dir() -> Path:
    env = os.environ.get("XDG_CONFIG_HOME")
    base = Path(env) if env else Path.home() / ".config"
    return base / "docpull-mcp"


def default_docs_dir() -> Path:
    env = os.environ.get("DOCPULL_DOCS_DIR") or os.environ.get("DOCS_DIR")
    if env:
        return Path(env)
    env = os.environ.get("XDG_DATA_HOME")
    base = Path(env) if env else Path.home() / ".local" / "share"
    return base / "docpull-mcp" / "docs"


def sources_config_path() -> Path:
    return default_config_dir() / "sources.yaml"


def load_user_sources(path: Path | None = None) -> dict[str, SourceConfig]:
    """Load user-defined sources from ``~/.config/docpull-mcp/sources.yaml``."""
    path = path or sources_config_path()
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError:
        return {}
    entries = raw.get("sources") or {}
    result: dict[str, SourceConfig] = {}
    for name, cfg in entries.items():
        if not isinstance(cfg, dict) or not isinstance(cfg.get("url"), str):
            continue
        result[str(name)] = SourceConfig(
            url=cfg["url"],
            description=str(cfg.get("description", "")),
            category=str(cfg.get("category", "user")),
            max_pages=cfg.get("maxPages") or cfg.get("max_pages"),
        )
    return result


def all_sources() -> dict[str, SourceConfig]:
    merged: dict[str, SourceConfig] = dict(BUILTIN_SOURCES)
    merged.update(load_user_sources())
    return merged


def resolve_source(name: str) -> SourceConfig | None:
    """Resolve an alias or reject a raw URL.

    Per the TS implementation, raw URLs are rejected on purpose — agents should
    be routed through configured aliases so that policy (max_pages, category)
    lives in one place.
    """
    if _URL_SCHEME_RE.match(name):
        return None
    return all_sources().get(name)


__all__ = [
    "BUILTIN_SOURCES",
    "SourceConfig",
    "all_sources",
    "default_config_dir",
    "default_docs_dir",
    "load_user_sources",
    "resolve_source",
    "sources_config_path",
]
