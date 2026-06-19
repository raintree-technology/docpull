"""stdio MCP server exposing docpull tools to AI agents.

Requires the optional ``mcp`` Python package (install with
``pip install docpull[mcp]``). The server registers seventeen tools:

Read-only:
- ``fetch_url(url)`` — one-shot fetch, no discovery. Agent-oriented fast path.
- ``list_sources(category?)`` — show available aliases.
- ``list_indexed()`` — show what has been fetched.
- ``grep_docs(pattern, library?, limit?)`` — regex search through cached Markdown.
- ``read_doc(library, path, line_start?, line_end?)`` — read a fetched file.
- ``pack_score(pack_dir, required_domains?)`` — score a context pack.
- ``pack_diff(old_pack_dir, new_pack_dir)`` — compare context packs.
- ``pack_citations(pack_dir, required_domains?)`` — build a stable source map.
- ``pack_entities(pack_dir, limit?, required_domains?)`` — extract cited entities.
- ``pack_search(pack_dir, query, limit?, required_domains?)`` — search pack records.
- ``pack_brief(pack_dir, objective?, ...)`` — generate a cited local research brief.

Write:
- ``ensure_docs(source, force?)`` — fetch (or refresh) a named source.
- ``parallel_context_pack(...)`` — build or dry-run a Parallel context pack.
- ``parallel_api_pack(source, kind?, output_dir?)`` — build an API pack.
- ``pack_prepare(pack_dir, objective?, ...)`` — write standard pack intelligence artifacts.
- ``add_source(name, url, ...)`` — add or update a user source alias.
- ``remove_source(name, delete_cache?)`` — remove a user source alias.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from ..pack_tools import (
    build_citation_map,
    build_research_brief,
    diff_packs,
    extract_pack_entities,
    prepare_pack,
    score_pack,
    search_pack,
)
from ..parallel_workflows import (
    DEFAULT_MAX_ESTIMATED_COST_USD,
    DEFAULT_MAX_TOKENS,
    _build_fetch_policy,
    _build_request_options,
    _build_source_policy,
    estimate_context_pack_cost,
    run_api_pack,
    run_live_context_pack,
)
from .tools import (
    ToolResult,
    add_source,
    ensure_docs,
    fetch_url,
    grep_docs,
    list_indexed,
    list_sources,
    read_doc,
    remove_source,
)

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Call list_sources to discover aliases before ensure_docs. "
    "Use ensure_docs for a whole named source (cached 7 days), fetch_url for one "
    "ad-hoc HTTPS page. After ensure_docs, use grep_docs to find passages "
    "and read_doc to pull the surrounding lines. Use add_source / "
    "remove_source to manage the user-defined registry."
)


# Output schemas — keep these next to the tool list so they stay in sync.
# Tools that return free-form Markdown (fetch_url) intentionally omit a
# schema; the rest expose structured payloads alongside the rendered text.

_LIST_SOURCES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string"},
                    "max_pages": {"type": "integer"},
                },
                "required": ["name", "url", "description", "category"],
            },
        },
    },
    "required": ["sources"],
}

_LIST_INDEXED_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "libraries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file_count": {"type": "integer"},
                    "fresh": {"type": "boolean"},
                    "fetched_at": {"type": "string"},
                    "age_seconds": {"type": "integer"},
                },
                "required": ["name", "file_count", "fresh"],
            },
        },
    },
    "required": ["libraries"],
}

_GREP_DOCS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "total_matches": {"type": "integer"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "library": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Relative to the library root; pass directly to read_doc",
                    },
                    "match_count": {"type": "integer"},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "lineno": {"type": "integer"},
                                "before": {"type": "array", "items": {"type": "string"}},
                                "line": {"type": "string"},
                                "after": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["lineno", "before", "line", "after"],
                        },
                    },
                },
                "required": ["library", "path", "match_count", "matches"],
            },
        },
        "truncated": {"type": "boolean"},
        "timed_out": {"type": "boolean"},
    },
    "required": ["pattern", "total_matches", "files", "truncated", "timed_out"],
}

_READ_DOC_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "library": {"type": "string"},
        "path": {"type": "string"},
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "total_lines": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["library", "path", "line_start", "line_end", "total_lines", "text"],
}

_ENSURE_DOCS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "cached": {"type": "boolean"},
        "file_count": {"type": "integer"},
        "pages_fetched": {"type": "integer"},
        "pages_skipped": {"type": "integer"},
        "pages_failed": {"type": "integer"},
        "target_dir": {"type": "string"},
    },
    "required": ["source", "cached", "target_dir"],
}

_ADD_SOURCE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "url": {"type": "string"},
        "replaced": {"type": "boolean"},
        "shadowed_builtin": {"type": "boolean"},
        "config_path": {"type": "string"},
    },
    "required": ["name", "url", "replaced", "shadowed_builtin", "config_path"],
}

_REMOVE_SOURCE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "removed": {"type": "boolean"},
        "cache_deleted": {"type": "boolean"},
        "config_path": {"type": "string"},
    },
    "required": ["name", "removed", "cache_deleted"],
}

_PARALLEL_PACK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "workflow": {"type": "string"},
        "output_dir": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "estimated_cost_usd": {"type": "number"},
    },
    "required": ["workflow", "output_dir"],
}

_PACK_SCORE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "grade": {"type": "string"},
        "summary": {"type": "object"},
        "issues": {"type": "array"},
        "warnings": {"type": "array"},
    },
    "required": ["score", "grade", "summary", "issues", "warnings"],
}

_PACK_DIFF_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "added_urls": {"type": "array", "items": {"type": "string"}},
        "removed_urls": {"type": "array", "items": {"type": "string"}},
        "changed_urls": {"type": "array", "items": {"type": "string"}},
        "unchanged_urls": {"type": "array", "items": {"type": "string"}},
        "old_record_count": {"type": "integer"},
        "new_record_count": {"type": "integer"},
    },
    "required": ["added_urls", "removed_urls", "changed_urls", "unchanged_urls"],
}

_PACK_CITATIONS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source_count": {"type": "integer"},
        "record_count": {"type": "integer"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["source_count", "record_count", "sources"],
}

_PACK_ENTITIES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entity_count": {"type": "integer"},
        "source_count": {"type": "integer"},
        "record_count": {"type": "integer"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["entity_count", "source_count", "record_count", "entities"],
}

_PACK_BRIEF_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "object"},
        "load_plan": {"type": "array", "items": {"type": "object"}},
        "key_excerpts": {"type": "array", "items": {"type": "object"}},
        "entities": {"type": "array", "items": {"type": "object"}},
        "artifacts": {"type": "object"},
    },
    "required": ["objective", "summary", "load_plan", "key_excerpts", "entities"],
}

_PACK_SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "source_count": {"type": "integer"},
        "record_count": {"type": "integer"},
        "result_count": {"type": "integer"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "results": {"type": "array", "items": {"type": "object"}},
        "citations": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["query", "source_count", "record_count", "result_count", "results", "citations"],
}

_PACK_PREPARE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "object"},
        "artifacts": {"type": "object"},
    },
    "required": ["objective", "search_queries", "summary", "artifacts"],
}


def _coerce_int(value: Any, *, name: str, default: int) -> int:
    """Accept int or numeric string; reject anything else with a clear error."""
    if value is None:
        return default
    if isinstance(value, bool):  # bool is a subclass of int — exclude
        raise ValueError(f"'{name}' must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as err:
            raise ValueError(f"'{name}' must be an integer: {err}") from None
    raise ValueError(f"'{name}' must be an integer, got {type(value).__name__}")


def _require_str(arguments: dict[str, Any], key: str) -> str:
    if key not in arguments:
        raise ValueError(f"Missing required argument: '{key}'")
    value = arguments[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _string_list_arg(arguments: dict[str, Any], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"'{key}' must be a list of non-empty strings")
    return value


def _path_arg(arguments: dict[str, Any], key: str, default: str | None = None) -> Path:
    value = arguments.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty path string")
    return Path(value)


async def _run_stdio() -> int:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
    except ImportError:
        print(
            "docpull mcp requires the 'mcp' package. Install with: pip install docpull[mcp]",
            file=sys.stderr,
        )
        return 1

    server: Server = Server("docpull", instructions=SERVER_INSTRUCTIONS)

    @server.list_tools()  # type: ignore[misc,no-untyped-call]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="fetch_url",
                description=(
                    "Fetch a single HTTPS URL and return clean Markdown. No discovery "
                    "or crawl — the agent-friendly fast path. Returns the page's "
                    "Markdown with source and detected framework in the header. "
                    "Optionally chunk the output with max_tokens. Rejects non-HTTPS "
                    "URLs, localhost, and private IPs. For whole libraries use "
                    "ensure_docs instead."
                ),
                annotations=ToolAnnotations(
                    title="Fetch one HTTPS page",
                    readOnlyHint=True,
                    openWorldHint=True,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "HTTPS URL to fetch",
                            "pattern": "^https://",
                        },
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 200000,
                            "description": "If set, split into chunks of this many tokens",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="ensure_docs",
                description=(
                    "Fetch Markdown for a named source alias (e.g. 'react', "
                    "'nextjs'). Uses a 7-day cache; pass force=true to refresh. "
                    "Optional profile selects fetch behavior: rag (default, "
                    "balanced for retrieval), mirror (full archive), quick "
                    "(fast/shallow), llm (NDJSON chunks). Use list_sources to "
                    "discover aliases first."
                ),
                annotations=ToolAnnotations(
                    title="Fetch a source alias",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "force": {"type": "boolean", "default": False},
                        "profile": {
                            "type": "string",
                            "enum": ["rag", "mirror", "quick", "llm"],
                            "default": "rag",
                        },
                    },
                    "required": ["source"],
                },
                outputSchema=_ENSURE_DOCS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="list_sources",
                description=(
                    "List configured source aliases, optionally "
                    "filtered by category. Use this to discover what ensure_docs "
                    "can fetch."
                ),
                annotations=ToolAnnotations(
                    title="List configured source aliases",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["frontend", "backend", "ai", "database", "user"],
                            "description": "Filter by category",
                        }
                    },
                },
                outputSchema=_LIST_SOURCES_OUTPUT_SCHEMA,
            ),
            Tool(
                name="list_indexed",
                description=(
                    "List sources that have been fetched to the local Markdown "
                    "directory, with last-fetched age. Sorted alphabetically."
                ),
                annotations=ToolAnnotations(
                    title="List locally cached sources",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={"type": "object", "properties": {}},
                outputSchema=_LIST_INDEXED_OUTPUT_SCHEMA,
            ),
            Tool(
                name="grep_docs",
                description=(
                    "Regex search through fetched Markdown. Results are ranked by "
                    "match density (most matches per file first) and rendered with "
                    "lines of surrounding context. Each result returns the library "
                    "and a path relative to the library root, so you can feed both "
                    "fields straight into read_doc. Use ensure_docs first."
                ),
                annotations=ToolAnnotations(
                    title="Regex-search cached Markdown",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "maxLength": 1000},
                        "library": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                            "description": "Restrict to one library (name from list_indexed)",
                        },
                        "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                        "case_sensitive": {"type": "boolean", "default": False},
                        "context": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "maximum": 3,
                            "description": "Lines of context per match (0 = none)",
                        },
                    },
                    "required": ["pattern"],
                },
                outputSchema=_GREP_DOCS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="read_doc",
                description=(
                    "Read a Markdown file from a fetched library, optionally sliced "
                    "by line range. The natural follow-up to grep_docs: pass each "
                    "result's library and path (path is already relative to the "
                    "library root) to pull more surrounding context."
                ),
                annotations=ToolAnnotations(
                    title="Read a cached Markdown file",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "library": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                        },
                        "path": {"type": "string", "description": "Relative path under the library"},
                        "line_start": {"type": "integer", "minimum": 1},
                        "line_end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["library", "path"],
                },
                outputSchema=_READ_DOC_OUTPUT_SCHEMA,
            ),
            Tool(
                name="parallel_context_pack",
                description=(
                    "Build or dry-run a Parallel Search + Extract context pack from "
                    "an objective and search queries. Requires docpull[parallel] and "
                    "a configured Parallel API key when dry_run=false. Run "
                    "`docpull parallel init` or set PARALLEL_API_KEY."
                ),
                annotations=ToolAnnotations(
                    title="Build a Parallel context pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "objective": {"type": "string"},
                        "queries": {"type": "array", "items": {"type": "string"}},
                        "output_dir": {"type": "string", "default": "packs/parallel-context-pack"},
                        "mode": {
                            "type": "string",
                            "enum": ["turbo", "basic", "advanced"],
                            "default": "advanced",
                        },
                        "extract_limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 8},
                        "include_domains": {"type": "array", "items": {"type": "string"}},
                        "exclude_domains": {"type": "array", "items": {"type": "string"}},
                        "after_date": {"type": "string"},
                        "max_search_results": {"type": "integer", "minimum": 1},
                        "client_model": {"type": "string"},
                        "max_estimated_cost": {"type": "number", "default": DEFAULT_MAX_ESTIMATED_COST_USD},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    "required": ["objective"],
                },
                outputSchema=_PARALLEL_PACK_OUTPUT_SCHEMA,
            ),
            Tool(
                name="parallel_api_pack",
                description=(
                    "Turn a local or HTTPS llms.txt/OpenAPI source into a docpull "
                    "context pack. Runs in a worker thread so remote sources work "
                    "inside MCP clients."
                ),
                annotations=ToolAnnotations(
                    title="Build an API context pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "kind": {"type": "string", "enum": ["auto", "llms", "openapi"], "default": "auto"},
                        "output_dir": {"type": "string", "default": "packs/api-pack"},
                    },
                    "required": ["source"],
                },
                outputSchema=_PARALLEL_PACK_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_score",
                description="Score a docpull context pack for agent-readiness without shelling out.",
                annotations=ToolAnnotations(
                    title="Score a context pack",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_SCORE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_diff",
                description="Diff two docpull context packs by URL and content hashes without shelling out.",
                annotations=ToolAnnotations(
                    title="Diff context packs",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "old_pack_dir": {"type": "string"},
                        "new_pack_dir": {"type": "string"},
                    },
                    "required": ["old_pack_dir", "new_pack_dir"],
                },
                outputSchema=_PACK_DIFF_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_citations",
                description="Build a stable citation/source map for a docpull context pack.",
                annotations=ToolAnnotations(
                    title="Build pack citations",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_CITATIONS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_entities",
                description=(
                    "Extract cited entities and structured signals from a "
                    "docpull context pack locally."
                ),
                annotations=ToolAnnotations(
                    title="Extract pack entities",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 100},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_ENTITIES_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_search",
                description="Search a docpull context pack locally and return cited excerpts.",
                annotations=ToolAnnotations(
                    title="Search a context pack",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 10},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir", "query"],
                },
                outputSchema=_PACK_SEARCH_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_brief",
                description=(
                    "Generate a cited local research brief from a docpull context pack, "
                    "including source load order, key excerpts, and structured signals."
                ),
                annotations=ToolAnnotations(
                    title="Build pack research brief",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "objective": {"type": "string"},
                        "max_excerpts": {"type": "integer", "minimum": 1, "default": 8},
                        "entity_limit": {"type": "integer", "minimum": 0, "default": 20},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_BRIEF_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_prepare",
                description=(
                    "Write the standard local pack intelligence bundle: pack score, "
                    "source scores, citations, entities, local search results, and a "
                    "cited research brief."
                ),
                annotations=ToolAnnotations(
                    title="Prepare pack artifacts",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "objective": {"type": "string"},
                        "search_queries": {"type": "array", "items": {"type": "string"}},
                        "default_search": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Use the objective as a local search query when search_queries is omitted"
                            ),
                        },
                        "max_excerpts": {"type": "integer", "minimum": 1, "default": 8},
                        "entity_limit": {"type": "integer", "minimum": 1, "default": 20},
                        "search_limit": {"type": "integer", "minimum": 1, "default": 10},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                        "markdown": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Also write Markdown sidecars such as SEARCH.md and RESEARCH_BRIEF.md"
                            ),
                        },
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_PREPARE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="add_source",
                description=(
                    "Add or update a user source alias in the writable "
                    "sources.yaml. Refuses to shadow a builtin alias unless "
                    "force=true. URL is HTTPS-only and validated against the "
                    "same SSRF rules as fetch_url. Use list_sources to confirm "
                    "the change."
                ),
                annotations=ToolAnnotations(
                    title="Add or update a user source",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                            "description": "Alias name (alnum + _ . -)",
                        },
                        "url": {
                            "type": "string",
                            "pattern": "^https://",
                            "description": "HTTPS URL to crawl",
                        },
                        "description": {"type": "string", "maxLength": 500},
                        "category": {
                            "type": "string",
                            "enum": ["frontend", "backend", "ai", "database", "user"],
                        },
                        "max_pages": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100000,
                        },
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": "Override a builtin alias of the same name",
                        },
                    },
                    "required": ["name", "url"],
                },
                outputSchema=_ADD_SOURCE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="remove_source",
                description=(
                    "Remove a user source alias. Optionally delete its cached "
                    "Markdown cache (delete_cache=true). Cannot remove a builtin source — "
                    "to stop using one, just don't call ensure_docs on it."
                ),
                annotations=ToolAnnotations(
                    title="Remove a user source",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                        },
                        "delete_cache": {
                            "type": "boolean",
                            "default": False,
                            "description": "Also delete the cached Markdown directory",
                        },
                    },
                    "required": ["name"],
                },
                outputSchema=_REMOVE_SOURCE_OUTPUT_SCHEMA,
            ),
        ]

    async def _make_progress_callback() -> Any:
        """Return ``(pages_done, total_or_none) -> awaitable`` bound to the
        current request's progressToken, or ``None`` if the client did not
        request progress."""
        ctx = server.request_context
        if ctx.meta is None or ctx.meta.progressToken is None:
            return None
        token = ctx.meta.progressToken
        session = ctx.session

        async def _cb(done: int, total: int | None) -> None:
            try:
                await session.send_progress_notification(
                    progress_token=token,
                    progress=float(done),
                    total=float(total) if total is not None else None,
                )
            except Exception:  # noqa: BLE001
                logger.debug("progress notification failed", exc_info=True)

        return _cb

    @server.call_tool()  # type: ignore[misc,no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            if name == "fetch_url":
                url = _require_str(arguments, "url")
                max_tokens = _coerce_int(arguments.get("max_tokens"), name="max_tokens", default=0)
                result = await fetch_url(url, max_tokens=max_tokens or None)
            elif name == "ensure_docs":
                source = _require_str(arguments, "source")
                on_progress = await _make_progress_callback()
                result = await ensure_docs(
                    source,
                    force=bool(arguments.get("force", False)),
                    profile=arguments.get("profile"),
                    on_progress=on_progress,
                )
            elif name == "list_sources":
                category = arguments.get("category")
                if category is not None and not isinstance(category, str):
                    raise ValueError("'category' must be a string")
                result = list_sources(category)
            elif name == "list_indexed":
                result = list_indexed()
            elif name == "grep_docs":
                pattern = _require_str(arguments, "pattern")
                library = arguments.get("library")
                if library is not None and not isinstance(library, str):
                    raise ValueError("'library' must be a string")
                result = grep_docs(
                    pattern,
                    library=library,
                    limit=_coerce_int(arguments.get("limit"), name="limit", default=20),
                    case_sensitive=bool(arguments.get("case_sensitive", False)),
                    context=_coerce_int(arguments.get("context"), name="context", default=1),
                )
            elif name == "read_doc":
                library = _require_str(arguments, "library")
                path = _require_str(arguments, "path")
                line_start = arguments.get("line_start")
                line_end = arguments.get("line_end")
                result = read_doc(
                    library,
                    path,
                    line_start=_coerce_int(line_start, name="line_start", default=0) or None,
                    line_end=_coerce_int(line_end, name="line_end", default=0) or None,
                )
            elif name == "parallel_context_pack":
                objective = _require_str(arguments, "objective")
                queries = _string_list_arg(arguments, "queries") or [objective]
                extract_limit = _coerce_int(arguments.get("extract_limit"), name="extract_limit", default=8)
                if extract_limit < 1 or extract_limit > 20:
                    raise ValueError("'extract_limit' must be between 1 and 20")
                max_search_results = (
                    _coerce_int(arguments.get("max_search_results"), name="max_search_results", default=0)
                    or None
                )
                source_policy = _build_source_policy(
                    include_domains=_string_list_arg(arguments, "include_domains"),
                    exclude_domains=_string_list_arg(arguments, "exclude_domains"),
                    after_date=arguments.get("after_date")
                    if isinstance(arguments.get("after_date"), str)
                    else None,
                )
                fetch_policy = _build_fetch_policy(
                    max_age_seconds=None,
                    timeout_seconds=None,
                    disable_cache_fallback=False,
                )
                estimated_cost = estimate_context_pack_cost(
                    extract_limit=extract_limit,
                    max_search_results=max_search_results,
                )
                max_estimated_cost = float(
                    arguments.get("max_estimated_cost", DEFAULT_MAX_ESTIMATED_COST_USD)
                )
                request_options = _build_request_options(
                    source_policy=source_policy,
                    fetch_policy=fetch_policy,
                    excerpt_chars_per_result=None,
                    location=None,
                    max_search_results=max_search_results,
                    max_search_chars_total=None,
                    max_extract_chars_total=None,
                    max_full_content_chars=50000,
                    client_model=arguments.get("client_model")
                    if isinstance(arguments.get("client_model"), str)
                    else None,
                    full_content=True,
                )
                if bool(arguments.get("dry_run", False)):
                    result = ToolResult(
                        "Parallel context pack dry run.",
                        data={
                            "workflow": "context-pack",
                            "output_dir": str(
                                _path_arg(arguments, "output_dir", "packs/parallel-context-pack")
                            ),
                            "dry_run": True,
                            "estimated_cost_usd": estimated_cost,
                            "request_options": request_options,
                        },
                    )
                else:
                    if estimated_cost > max_estimated_cost:
                        raise ValueError(
                            f"Estimated Parallel cost ${estimated_cost:.3f} exceeds max_estimated_cost "
                            f"${max_estimated_cost:.3f}"
                        )
                    output_dir = _path_arg(arguments, "output_dir", "packs/parallel-context-pack")
                    mode_arg = arguments.get("mode")
                    mode = mode_arg if isinstance(mode_arg, str) else "advanced"
                    pack_path = await asyncio.to_thread(
                        run_live_context_pack,
                        objective=objective,
                        queries=queries,
                        output_dir=output_dir,
                        mode=mode,
                        extract_limit=extract_limit,
                        max_tokens_per_file=DEFAULT_MAX_TOKENS,
                        source_policy=source_policy,
                        max_search_results=max_search_results,
                        client_model=arguments.get("client_model")
                        if isinstance(arguments.get("client_model"), str)
                        else None,
                        estimated_cost_usd=estimated_cost,
                    )
                    result = ToolResult(
                        f"Wrote Parallel context pack: {pack_path}",
                        data={
                            "workflow": "context-pack",
                            "output_dir": str(pack_path),
                            "dry_run": False,
                            "estimated_cost_usd": estimated_cost,
                        },
                    )
            elif name == "parallel_api_pack":
                source = _require_str(arguments, "source")
                kind = arguments.get("kind", "auto")
                if kind not in {"auto", "llms", "openapi"}:
                    raise ValueError("'kind' must be one of: auto, llms, openapi")
                output_dir = _path_arg(arguments, "output_dir", "packs/api-pack")
                pack_path = await asyncio.to_thread(
                    run_api_pack,
                    source=source,
                    kind=kind,
                    output_dir=output_dir,
                )
                result = ToolResult(
                    f"Wrote API context pack: {pack_path}",
                    data={"workflow": "api-pack", "output_dir": str(pack_path), "dry_run": False},
                )
            elif name == "pack_score":
                payload = await asyncio.to_thread(
                    score_pack,
                    _path_arg(arguments, "pack_dir"),
                    required_domains=_string_list_arg(arguments, "required_domains"),
                )
                result = ToolResult(
                    f"Pack score: {payload['score']}/100 ({payload['grade']})",
                    data=payload,
                )
            elif name == "pack_diff":
                payload = await asyncio.to_thread(
                    diff_packs,
                    _path_arg(arguments, "old_pack_dir"),
                    _path_arg(arguments, "new_pack_dir"),
                )
                result = ToolResult(
                    "Pack diff: "
                    f"+{len(payload['added_urls'])} "
                    f"-{len(payload['removed_urls'])} "
                    f"~{len(payload['changed_urls'])}",
                    data=payload,
                )
            elif name == "pack_citations":
                payload = await asyncio.to_thread(
                    build_citation_map,
                    _path_arg(arguments, "pack_dir"),
                    required_domains=_string_list_arg(arguments, "required_domains"),
                )
                result = ToolResult(
                    f"Citation map: {payload['source_count']} sources",
                    data=payload,
                )
            elif name == "pack_entities":
                payload = await asyncio.to_thread(
                    extract_pack_entities,
                    _path_arg(arguments, "pack_dir"),
                    required_domains=_string_list_arg(arguments, "required_domains"),
                    limit=_coerce_int(arguments.get("limit"), name="limit", default=100),
                )
                result = ToolResult(
                    f"Extracted entities: {payload['entity_count']}",
                    data=payload,
                )
            elif name == "pack_search":
                payload = await asyncio.to_thread(
                    search_pack,
                    _path_arg(arguments, "pack_dir"),
                    _require_str(arguments, "query"),
                    required_domains=_string_list_arg(arguments, "required_domains"),
                    limit=_coerce_int(arguments.get("limit"), name="limit", default=10),
                )
                result = ToolResult(
                    f"Pack search: {payload['result_count']} results",
                    data=payload,
                )
            elif name == "pack_brief":
                objective = arguments.get("objective")
                if objective is not None and not isinstance(objective, str):
                    raise ValueError("'objective' must be a string")
                payload = await asyncio.to_thread(
                    build_research_brief,
                    _path_arg(arguments, "pack_dir"),
                    objective=objective,
                    required_domains=_string_list_arg(arguments, "required_domains"),
                    max_excerpts=_coerce_int(arguments.get("max_excerpts"), name="max_excerpts", default=8),
                    entity_limit=_coerce_int(arguments.get("entity_limit"), name="entity_limit", default=20),
                )
                result = ToolResult(
                    "Research brief: "
                    f"{len(payload['key_excerpts'])} excerpts from "
                    f"{payload['summary']['source_count']} sources",
                    data=payload,
                )
            elif name == "pack_prepare":
                objective = arguments.get("objective")
                if objective is not None and not isinstance(objective, str):
                    raise ValueError("'objective' must be a string")
                raw_search_queries = arguments.get("search_queries")
                if raw_search_queries is None:
                    search_queries = None
                else:
                    if not isinstance(raw_search_queries, list) or not all(
                        isinstance(item, str) and item for item in raw_search_queries
                    ):
                        raise ValueError("'search_queries' must be a list of non-empty strings")
                    search_queries = raw_search_queries
                payload = await asyncio.to_thread(
                    prepare_pack,
                    _path_arg(arguments, "pack_dir"),
                    objective=objective,
                    search_queries=search_queries,
                    default_search=bool(arguments.get("default_search", True)),
                    required_domains=_string_list_arg(arguments, "required_domains"),
                    max_excerpts=_coerce_int(arguments.get("max_excerpts"), name="max_excerpts", default=8),
                    entity_limit=_coerce_int(arguments.get("entity_limit"), name="entity_limit", default=20),
                    search_limit=_coerce_int(arguments.get("search_limit"), name="search_limit", default=10),
                    markdown=bool(arguments.get("markdown", True)),
                )
                result = ToolResult(
                    f"Prepared pack: {payload['summary']['artifact_count']} artifacts",
                    data=payload,
                )
            elif name == "add_source":
                add_name = _require_str(arguments, "name")
                add_url = _require_str(arguments, "url")
                description = arguments.get("description")
                if description is not None and not isinstance(description, str):
                    raise ValueError("'description' must be a string")
                category = arguments.get("category")
                if category is not None and not isinstance(category, str):
                    raise ValueError("'category' must be a string")
                max_pages = arguments.get("max_pages")
                result = add_source(
                    add_name,
                    add_url,
                    description=description,
                    category=category,
                    max_pages=_coerce_int(max_pages, name="max_pages", default=0) or None,
                    force=bool(arguments.get("force", False)),
                )
            elif name == "remove_source":
                rm_name = _require_str(arguments, "name")
                result = remove_source(
                    rm_name,
                    delete_cache=bool(arguments.get("delete_cache", False)),
                )
            else:
                result = ToolResult(f"Unknown tool: {name}", is_error=True)
        except ValueError as err:
            result = ToolResult(str(err), is_error=True)
        except Exception as err:  # noqa: BLE001
            logger.exception("Tool %s raised", name)
            result = ToolResult(f"Tool error: {err}", is_error=True)

        # Return CallToolResult directly so:
        # (a) ``is_error`` propagates (the SDK's tuple/list paths hardcode
        #     isError=False), and
        # (b) errors on tools with an outputSchema don't fail the validator
        #     for "missing structured content."
        # `content` is typed `list[TextContent | ImageContent | ...]` on the SDK
        # side; list invariance means we have to widen the local annotation
        # explicitly even though TextContent is one of the valid variants.
        content: list[Any] = [TextContent(type="text", text=result.text)]
        return CallToolResult(
            content=content,
            structuredContent=result.data if not result.is_error else None,
            isError=result.is_error,
        )

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
    return 0


def run_mcp_server(argv: list[str]) -> int:
    """Entry point for ``docpull mcp``."""
    parser = argparse.ArgumentParser(prog="docpull mcp", description="Run the docpull MCP server over stdio.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    try:
        return asyncio.run(_run_stdio())
    except KeyboardInterrupt:
        return 0


__all__ = ["run_mcp_server"]
