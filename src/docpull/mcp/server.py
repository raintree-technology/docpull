"""stdio MCP server exposing docpull tools to AI agents.

Requires the optional ``mcp`` Python package (install with
``pip install docpull[mcp]``). The server registers six tools:

- ``fetch_url(url)`` — one-shot fetch, no discovery. The agent-oriented tool.
- ``ensure_docs(source, force?)`` — fetch a named library.
- ``list_sources(category?)`` — show available aliases.
- ``list_indexed()`` — show what has been fetched.
- ``grep_docs(pattern, library?, limit?)`` — regex search through cached docs.
- ``read_doc(library, path, line_start?, line_end?)`` — read a fetched file.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any

from .tools import (
    ToolResult,
    ensure_docs,
    fetch_url,
    grep_docs,
    list_indexed,
    list_sources,
    read_doc,
)

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Call list_sources to discover aliases before ensure_docs. "
    "Use ensure_docs for a whole library (cached 7 days), fetch_url for one "
    "ad-hoc HTTPS page. After ensure_docs, use grep_docs to find passages "
    "and read_doc to pull the surrounding lines."
)


def _format_result(result: ToolResult) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": result.text}],
        "isError": result.is_error,
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
        from mcp.types import TextContent, Tool, ToolAnnotations
    except ImportError:
        print(
            "docpull mcp requires the 'mcp' package. Install with: "
            "pip install docpull[mcp]",
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
            ),
            Tool(
                name="grep_docs",
                description=(
                    "Regex search through fetched Markdown. Results are ranked by "
                    "match density (most matches per file first) and rendered with "
                    "lines of surrounding context. Use ensure_docs first; then "
                    "read_doc to pull more context around a hit."
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
            ),
            Tool(
                name="read_doc",
                description=(
                    "Read a Markdown file from a fetched library, optionally sliced "
                    "by line range. The natural follow-up to grep_docs: pass the "
                    "library + path it returned to pull more surrounding context."
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
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
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
            else:
                result = ToolResult(f"Unknown tool: {name}", is_error=True)
        except ValueError as err:
            result = ToolResult(str(err), is_error=True)
        except Exception as err:  # noqa: BLE001
            logger.exception("Tool %s raised", name)
            result = ToolResult(f"Tool error: {err}", is_error=True)
        return [TextContent(type="text", text=result.text)]

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
