"""stdio MCP server exposing docpull tools to AI agents.

Requires the optional ``mcp`` Python package (install with
``pip install docpull[mcp]``). The server registers five tools:

- ``fetch_url(url)`` — one-shot fetch, no discovery. The agent-oriented tool.
- ``ensure_docs(source, force?)`` — fetch a named library.
- ``list_sources(category?)`` — show available aliases.
- ``list_indexed()`` — show what has been fetched.
- ``grep_docs(pattern, library?, limit?)`` — regex search through cached docs.
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
)

logger = logging.getLogger(__name__)


def _format_result(result: ToolResult) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": result.text}],
        "isError": result.is_error,
    }


async def _run_stdio() -> int:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import TextContent, Tool
    except ImportError:
        print(
            "docpull mcp requires the 'mcp' package. Install with: "
            "pip install docpull[mcp]",
            file=sys.stderr,
        )
        return 1

    server: Server = Server("docpull")

    @server.list_tools()  # type: ignore[misc,no-untyped-call]
    async def _list_tools() -> list[Tool]:
        return [
            Tool(
                name="fetch_url",
                description=(
                    "Fetch a single URL and return clean Markdown. No discovery or "
                    "crawl — the agent-friendly fast path. Returns the page's "
                    "Markdown with source and detected framework in the header. "
                    "Optionally chunk the output with max_tokens."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "HTTPS URL to fetch"},
                        "max_tokens": {
                            "type": "integer",
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
                    "Optional profile selects fetch behavior (rag is the default; "
                    "mirror keeps full archive, llm produces NDJSON chunks)."
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
                description="List configured documentation sources, optionally filtered by category.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "description": "Filter (frontend, backend, ai, database, user)",
                        }
                    },
                },
            ),
            Tool(
                name="list_indexed",
                description="List sources that have been fetched to the local docs directory.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="grep_docs",
                description=(
                    "Regex search through fetched Markdown. Results are ranked by "
                    "match density (most matches per file first) and rendered with "
                    "one line of surrounding context above and below each hit. "
                    "Use ensure_docs first."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "library": {"type": "string"},
                        "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                        "case_sensitive": {"type": "boolean", "default": False},
                        "context": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "maximum": 3,
                            "description": "Lines of surrounding context per match (0 = none)",
                        },
                    },
                    "required": ["pattern"],
                },
            ),
        ]

    @server.call_tool()  # type: ignore[misc,no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            if name == "fetch_url":
                result = await fetch_url(
                    arguments["url"],
                    max_tokens=arguments.get("max_tokens"),
                )
            elif name == "ensure_docs":
                result = await ensure_docs(
                    arguments["source"],
                    force=bool(arguments.get("force", False)),
                    profile=arguments.get("profile"),
                )
            elif name == "list_sources":
                result = list_sources(arguments.get("category"))
            elif name == "list_indexed":
                result = list_indexed()
            elif name == "grep_docs":
                result = grep_docs(
                    arguments["pattern"],
                    library=arguments.get("library"),
                    limit=int(arguments.get("limit", 20)),
                    case_sensitive=bool(arguments.get("case_sensitive", False)),
                    context=int(arguments.get("context", 1)),
                )
            else:
                result = ToolResult(f"Unknown tool: {name}", is_error=True)
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
