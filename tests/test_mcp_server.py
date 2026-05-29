"""End-to-end tests for the stdio MCP server."""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("mcp")
pytest.importorskip("mcp.client.stdio")

from mcp.client.stdio import stdio_client

from mcp import ClientSession, StdioServerParameters


@pytest.mark.asyncio
async def test_stdio_server_lists_and_calls_tools(tmp_path):
    env = os.environ.copy()
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")

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
            "add_source",
            "remove_source",
        }

        result = await session.call_tool("list_sources", {})
        assert result.isError is False
        assert result.structuredContent is not None
        assert any(source["name"] == "react" for source in result.structuredContent["sources"])

        rejected = await session.call_tool("fetch_url", {"url": "https://localhost/admin"})
        assert rejected.isError is True
