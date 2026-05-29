"""Built-in and user-configured documentation sources for the MCP server."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from ..security.url_validator import UrlValidator

logger = logging.getLogger(__name__)


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
_LIBRARY_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
MAX_LIBRARY_NAME_LENGTH = 128
MAX_USER_SOURCE_PAGES = 100_000
_USER_SOURCE_URL_VALIDATOR = UrlValidator(allowed_schemes={"https"})


def is_safe_library_name(name: str) -> bool:
    """Reject names that could escape ``docs_dir`` via path traversal.

    Allows alnum + ``_ . -``; rejects separators, ``..``, leading dot.
    """
    if not name or name.startswith(".") or name == ".." or len(name) > MAX_LIBRARY_NAME_LENGTH:
        return False
    return bool(_LIBRARY_NAME_RE.fullmatch(name))


def _is_https_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme.lower() == "https" and parsed.hostname is not None


def _is_allowed_source_url(url: str) -> tuple[bool, str | None]:
    if not _is_https_url(url):
        return (False, "url must be an HTTPS URL")
    validation = _USER_SOURCE_URL_VALIDATOR.validate(url)
    if not validation.is_valid:
        return (False, validation.rejection_reason or "url rejected by validator")
    return (True, None)


def _coerce_max_pages(value: object, source_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"source '{source_name}' max_pages must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as err:
            raise ValueError(f"source '{source_name}' max_pages must be an integer") from err
    else:
        raise ValueError(f"source '{source_name}' max_pages must be an integer")
    if parsed < 1 or parsed > MAX_USER_SOURCE_PAGES:
        raise ValueError(f"source '{source_name}' max_pages must be between 1 and {MAX_USER_SOURCE_PAGES}")
    return parsed


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
    except yaml.YAMLError as err:
        logger.warning("Failed to parse %s: %s", path, err)
        return {}
    entries = raw.get("sources") or {}
    result: dict[str, SourceConfig] = {}
    for name, cfg in entries.items():
        source_name = str(name)
        if not is_safe_library_name(source_name):
            logger.warning("Ignoring unsafe source name in %s: %r", path, source_name)
            continue
        if not isinstance(cfg, dict):
            logger.warning("Ignoring source %s in %s: entry must be a mapping", source_name, path)
            continue
        url = cfg.get("url")
        if not isinstance(url, str):
            logger.warning("Ignoring source %s in %s: url must be an HTTPS URL", source_name, path)
            continue
        url_allowed, url_reason = _is_allowed_source_url(url)
        if not url_allowed:
            logger.warning("Ignoring source %s in %s: %s", source_name, path, url_reason)
            continue
        try:
            max_pages = _coerce_max_pages(cfg.get("maxPages") or cfg.get("max_pages"), source_name)
        except ValueError as err:
            logger.warning("Ignoring source %s in %s: %s", source_name, path, err)
            continue
        result[source_name] = SourceConfig(
            url=url,
            description=str(cfg.get("description", "")),
            category=str(cfg.get("category", "user")),
            max_pages=max_pages,
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
    if _URL_SCHEME_RE.match(name) or not is_safe_library_name(name):
        return None
    return all_sources().get(name)


__all__ = [
    "BUILTIN_SOURCES",
    "SourceConfig",
    "_URL_SCHEME_RE",
    "all_sources",
    "default_config_dir",
    "default_docs_dir",
    "is_safe_library_name",
    "load_user_sources",
    "resolve_source",
    "sources_config_path",
]
