"""End-to-end tests for the stdio MCP server."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("mcp")
pytest.importorskip("mcp.client.stdio")

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters
from tests.pack_fixtures import write_context_pack


@pytest.mark.asyncio
async def test_stdio_server_lists_and_calls_tools(tmp_path):
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "docpull", "mcp"],
        env=env,
    )
    async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        assert names == {
            "fetch_url",
            "ensure_docs",
            "list_sources",
            "list_indexed",
            "grep_docs",
            "read_doc",
            "parallel_context_pack",
            "parallel_api_pack",
            "pack_score",
            "pack_diff",
            "pack_citations",
            "pack_entities",
            "pack_search",
            "pack_brief",
            "pack_prepare",
            "add_source",
            "remove_source",
        }

        result = await session.call_tool("list_sources", {})
        assert result.isError is False
        assert result.structuredContent is not None
        assert any(source["name"] == "react" for source in result.structuredContent["sources"])
        assert any(source["name"] == "parallel" for source in result.structuredContent["sources"])

        dry_run = await session.call_tool(
            "parallel_context_pack",
            {"objective": "Parallel docs", "queries": ["Parallel API"], "dry_run": True},
        )
        assert dry_run.isError is False
        assert dry_run.structuredContent is not None
        assert dry_run.structuredContent["dry_run"] is True

        prepared = await session.call_tool(
            "pack_prepare",
            {
                "pack_dir": str(pack_dir),
                "objective": "Review Parallel Search API",
                "search_queries": ["cited JSON"],
            },
        )
        assert prepared.isError is False
        assert prepared.structuredContent is not None
        assert prepared.structuredContent["summary"]["score"] == 100
        assert prepared.structuredContent["artifacts"]["prepare"] == "pack.prepare.json"
        assert (pack_dir / "pack.prepare.json").exists()

        rejected = await session.call_tool("fetch_url", {"url": "https://localhost/admin"})
        assert rejected.isError is True
