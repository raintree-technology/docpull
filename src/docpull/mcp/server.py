"""stdio MCP server exposing docpull tools to AI agents.

Requires the optional ``mcp`` Python package (install with
``pip install docpull[mcp]``). The server registers eight tools:

Read-only:
- ``fetch_url(url)`` — one-shot fetch, no discovery. Agent-oriented fast path.
- ``list_sources(category?)`` — show available aliases.
- ``list_indexed()`` — show what has been fetched.
- ``grep_docs(pattern, library?, limit?)`` — regex search through cached docs.
- ``read_doc(library, path, line_start?, line_end?)`` — read a fetched file.

Write:
- ``ensure_docs(source, force?)`` — fetch (or refresh) a named library.
- ``add_source(name, url, ...)`` — add or update a user source alias.
- ``remove_source(name, delete_cache?)`` — remove a user source alias.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

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
    "Use ensure_docs for a whole library (cached 7 days), fetch_url for one "
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
                    "Fetch documentation for a named source alias (e.g. 'react', "
                    "'nextjs'). Uses a 7-day cache; pass force=true to refresh. "
                    "Optional profile selects fetch behavior: rag (default, "
                    "balanced for retrieval), mirror (full archive), quick "
                    "(fast/shallow), llm (NDJSON chunks). Use list_sources to "
                    "discover aliases first."
                ),
                annotations=ToolAnnotations(
                    title="Fetch a documentation library",
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
                    "List configured documentation source aliases, optionally "
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
                    "List sources that have been fetched to the local docs "
                    "directory, with last-fetched age. Sorted alphabetically."
                ),
                annotations=ToolAnnotations(
                    title="List locally cached libraries",
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
                    title="Regex-search cached docs",
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
                    title="Read a cached doc file",
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
                    "docs (delete_cache=true). Cannot remove a builtin source — "
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
                            "description": "Also delete the cached docs directory",
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
