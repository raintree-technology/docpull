"""End-to-end tests for the stdio MCP server."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import pytest

pytest.importorskip("mcp")
pytest.importorskip("mcp.client.stdio")

from mcp.client.stdio import stdio_client

from docpull.mcp import server as mcp_server
from mcp import ClientSession, StdioServerParameters
from tests.pack_fixtures import write_context_pack


def _docpull_mcp_pids() -> set[int]:
    """Return live test-server PIDs for explicit stdio cleanup."""
    if os.name == "nt":
        return set()
    try:
        proc = subprocess.run(
            ["ps", "-axo", "pid=,command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return set()

    needle = f"{sys.executable} -m docpull mcp"
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if needle not in stripped:
            continue
        pid_text = stripped.split(maxsplit=1)[0]
        if pid_text.isdigit():
            pids.add(int(pid_text))
    return pids


def _terminate_new_docpull_mcp_processes(before: set[int]) -> None:
    """Clean up stdio child processes if the upstream client leaves any alive."""
    if os.name == "nt":
        return
    deadline = time.monotonic() + 2.0
    leaked: set[int] = set()
    while time.monotonic() < deadline:
        leaked = _docpull_mcp_pids() - before
        if leaked:
            break
        time.sleep(0.05)
    if not leaked:
        return

    for pid in leaked:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    time.sleep(0.2)
    for pid in _docpull_mcp_pids() - before:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


@asynccontextmanager
async def _stdio_client_with_cleanup(server: StdioServerParameters) -> AsyncIterator[tuple[object, object]]:
    before = _docpull_mcp_pids()
    try:
        async with stdio_client(server) as streams:
            yield streams
    finally:
        _terminate_new_docpull_mcp_processes(before)


def test_mcp_server_argument_helpers() -> None:
    assert mcp_server._coerce_int(None, name="limit", default=20) == 20
    assert mcp_server._coerce_int("5", name="limit", default=20) == 5
    assert mcp_server._coerce_int(7, name="limit", default=20) == 7
    with pytest.raises(ValueError, match="got bool"):
        mcp_server._coerce_int(True, name="limit", default=20)
    with pytest.raises(ValueError, match="invalid literal"):
        mcp_server._coerce_int("nope", name="limit", default=20)
    with pytest.raises(ValueError, match="got float"):
        mcp_server._coerce_int(1.5, name="limit", default=20)

    assert mcp_server._require_str({"name": "react"}, "name") == "react"
    with pytest.raises(ValueError, match="Missing required argument"):
        mcp_server._require_str({}, "name")
    with pytest.raises(ValueError, match="non-empty string"):
        mcp_server._require_str({"name": ""}, "name")

    assert mcp_server._string_list_arg({}, "queries") == []
    assert mcp_server._string_list_arg({"queries": ["a", "b"]}, "queries") == ["a", "b"]
    with pytest.raises(ValueError, match="list of non-empty strings"):
        mcp_server._string_list_arg({"queries": ["a", ""]}, "queries")

    assert mcp_server._path_arg({}, "output_dir", "packs/default").as_posix() == "packs/default"
    with pytest.raises(ValueError, match="non-empty path string"):
        mcp_server._path_arg({"output_dir": ""}, "output_dir")


@pytest.mark.asyncio
async def test_mcp_dispatch_tool_handles_success_and_validation_errors(tmp_path):
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir)

    score = await mcp_server._dispatch_tool("pack_score", {"pack_dir": str(pack_dir)})

    assert score.is_error is False
    assert score.data is not None
    assert score.data["score"] == 100

    graph = await mcp_server._dispatch_tool("graph_build", {"pack_dir": str(pack_dir)})
    assert graph.is_error is False
    assert graph.data is not None
    assert graph.data["status"] == "current"

    graph_query = await mcp_server._dispatch_tool(
        "graph_query",
        {"pack_dir": str(pack_dir), "query": "Parallel Search"},
    )
    assert graph_query.is_error is False
    assert graph_query.data is not None
    assert graph_query.data["result_count"] >= 1

    graph_refresh = await mcp_server._dispatch_tool("graph_refresh", {"pack_dir": str(pack_dir)})
    assert graph_refresh.is_error is False
    assert graph_refresh.data is not None
    assert graph_refresh.data["new_status"] == "current"

    missing_query = await mcp_server._dispatch_tool("pack_search", {"pack_dir": str(pack_dir)})
    assert missing_query.is_error is True
    assert "Missing required argument: 'query'" in missing_query.text

    bad_limit = await mcp_server._dispatch_tool(
        "parallel_context_pack",
        {"objective": "Parallel docs", "extract_limit": True, "dry_run": True},
    )
    assert bad_limit.is_error is True
    assert "'extract_limit' must be an integer, got bool" in bad_limit.text

    blocked_parallel = await mcp_server._dispatch_tool(
        "parallel_context_pack",
        {"objective": "Parallel docs", "dry_run": True, "budget": 0},
    )
    assert blocked_parallel.is_error is False
    assert blocked_parallel.data is not None
    assert blocked_parallel.data["blocked_by_budget"] is True

    unknown = await mcp_server._dispatch_tool("not_a_tool", {})
    assert unknown.is_error is True
    assert unknown.text == "Unknown tool: not_a_tool"

    research = await mcp_server._dispatch_tool(
        "research_pack",
        {
            "pack_dir": str(pack_dir),
            "objective": "What does Parallel Search return?",
            "output_dir": str(tmp_path / "research"),
        },
    )
    assert research.is_error is False
    assert research.data is not None
    assert research.data["workflow"] == "research-pack"
    assert (tmp_path / "research" / "research.result.json").exists()


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
    async with _stdio_client_with_cleanup(server) as (read, write), ClientSession(read, write) as session:
        await session.initialize()

        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        render_tool = next(tool for tool in tools.tools if tool.name == "render_url")
        assert render_tool.inputSchema["properties"]["runtime"]["enum"] == [
            "local",
            "vercel",
            "e2b",
        ]
        assert render_tool.inputSchema["properties"]["runtime"]["default"] == "local"
        assert render_tool.inputSchema["properties"]["cloud_agent_browser_install"]["enum"] == [
            "auto",
            "skip",
        ]
        assert (
            render_tool.inputSchema["properties"]["cloud_agent_browser_binary"]["default"] == "agent-browser"
        )
        assert render_tool.inputSchema["properties"]["template"]["type"] == "string"
        assert "e2b_template" not in render_tool.inputSchema["properties"]
        assert render_tool.inputSchema["properties"]["cloud_result_transport"]["enum"] == [
            "auto",
            "stdout",
            "file",
        ]
        assert render_tool.inputSchema["properties"]["budget"]["minimum"] == 0
        parallel_tool = next(tool for tool in tools.tools if tool.name == "parallel_context_pack")
        assert parallel_tool.inputSchema["properties"]["budget"]["minimum"] == 0
        assert {
            "fetch_url",
            "render_url",
            "ensure_docs",
            "list_sources",
            "list_indexed",
            "grep_docs",
            "read_doc",
            "parallel_context_pack",
            "parallel_api_pack",
            "discover_sources",
            "fetch_discovered_sources",
            "extract_pack",
            "map_sources",
            "crawl_pack",
            "research_pack",
            "entities_pack",
            "pack_score",
            "pack_diff",
            "refresh_pack",
            "audit_pack",
            "pack_citations",
            "pack_entities",
            "pack_search",
            "answer_pack",
            "pack_brief",
            "pack_prepare",
            "graph_build",
            "graph_status",
            "graph_query",
            "graph_neighbors",
            "graph_refresh",
            "validate_policy",
            "export_pack",
            "serve_pack_status",
            "add_source",
            "remove_source",
        }.issubset(names)

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
        assert prepared.structuredContent["summary"]["graph_node_count"] >= 1
        assert prepared.structuredContent["artifacts"]["graph_graph"] == "graph.json"
        assert prepared.structuredContent["artifacts"]["prepare"] == "pack.prepare.json"
        assert (pack_dir / "pack.prepare.json").exists()

        graph = await session.call_tool("graph_build", {"pack_dir": str(pack_dir)})
        assert graph.isError is False
        assert graph.structuredContent is not None
        assert graph.structuredContent["status"] == "current"
        assert (pack_dir / "graph.json").exists()

        graph_status = await session.call_tool("graph_status", {"pack_dir": str(pack_dir)})
        assert graph_status.isError is False
        assert graph_status.structuredContent is not None
        assert graph_status.structuredContent["status"] == "current"

        graph_query = await session.call_tool(
            "graph_query",
            {"pack_dir": str(pack_dir), "query": "Parallel Search"},
        )
        assert graph_query.isError is False
        assert graph_query.structuredContent is not None
        assert graph_query.structuredContent["result_count"] >= 1

        graph_neighbors = await session.call_tool(
            "graph_neighbors",
            {"pack_dir": str(pack_dir), "entity": "Parallel Search API"},
        )
        assert graph_neighbors.isError is False
        assert graph_neighbors.structuredContent is not None
        assert graph_neighbors.structuredContent["matched_entity_count"] >= 1

        graph_refresh = await session.call_tool("graph_refresh", {"pack_dir": str(pack_dir)})
        assert graph_refresh.isError is False
        assert graph_refresh.structuredContent is not None
        assert graph_refresh.structuredContent["new_status"] == "current"
        assert (pack_dir / "graph.diff.json").exists()

        score = await session.call_tool("pack_score", {"pack_dir": str(pack_dir)})
        assert score.isError is False
        assert score.structuredContent is not None
        assert score.structuredContent["score"] == 100

        audit = await session.call_tool("audit_pack", {"pack_dir": str(pack_dir)})
        assert audit.isError is False
        assert audit.structuredContent is not None
        assert audit.structuredContent["score"] >= 50

        refresh = await session.call_tool("refresh_pack", {"pack_dir": str(pack_dir), "dry_run": True})
        assert refresh.isError is False
        assert refresh.structuredContent is not None
        assert refresh.structuredContent["dry_run"] is True

        citations = await session.call_tool("pack_citations", {"pack_dir": str(pack_dir)})
        assert citations.isError is False
        assert citations.structuredContent is not None
        assert citations.structuredContent["source_count"] == 1

        entities = await session.call_tool("pack_entities", {"pack_dir": str(pack_dir), "limit": 5})
        assert entities.isError is False
        assert entities.structuredContent is not None
        assert entities.structuredContent["entity_count"] >= 1

        search = await session.call_tool("pack_search", {"pack_dir": str(pack_dir), "query": "cited JSON"})
        assert search.isError is False
        assert search.structuredContent is not None
        assert search.structuredContent["result_count"] == 1

        answer = await session.call_tool(
            "answer_pack",
            {"pack_dir": str(pack_dir), "question": "What does Parallel Search return?"},
        )
        assert answer.isError is False
        assert answer.structuredContent is not None
        assert answer.structuredContent["answer"]["status"] == "answered_from_local_pack"

        policy_path = tmp_path / "policy.yml"
        policy_path.write_text("schema_version: 1\nallowed_domains:\n  - docs.parallel.ai\n")
        policy = await session.call_tool("validate_policy", {"policy_path": str(policy_path)})
        assert policy.isError is False
        assert policy.structuredContent is not None
        assert policy.structuredContent["valid"] is True

        discovery_dir = tmp_path / "discovery"
        discovery = await session.call_tool(
            "discover_sources",
            {
                "urls": ["https://docs.parallel.ai/api-reference/search/search"],
                "include_domains": ["docs.parallel.ai"],
                "output_dir": str(discovery_dir),
            },
        )
        assert discovery.isError is False
        assert discovery.structuredContent is not None
        assert discovery.structuredContent["candidate_count"] == 1

        selected = await session.call_tool(
            "fetch_discovered_sources",
            {"discovery_pack_dir": str(discovery_dir), "output_dir": str(tmp_path / "selected")},
        )
        assert selected.isError is False
        assert selected.structuredContent is not None
        assert selected.structuredContent["selected_count"] == 1

        exported = await session.call_tool(
            "export_pack",
            {
                "pack_dir": str(pack_dir),
                "format": "dspy-jsonl",
                "output": str(tmp_path / "mcp-export.jsonl"),
            },
        )
        assert exported.isError is False
        assert exported.structuredContent is not None
        assert exported.structuredContent["record_count"] == 1

        serve_status = await session.call_tool("serve_pack_status", {"pack_dir": str(pack_dir)})
        assert serve_status.isError is False
        assert serve_status.structuredContent is not None
        assert serve_status.structuredContent["document_count"] == 1

        brief = await session.call_tool(
            "pack_brief",
            {"pack_dir": str(pack_dir), "objective": "Review Parallel Search API", "entity_limit": 5},
        )
        assert brief.isError is False
        assert brief.structuredContent is not None
        assert brief.structuredContent["summary"]["source_count"] == 1

        added = await session.call_tool(
            "add_source",
            {
                "name": "teamdocs",
                "url": "https://example.com/docs",
                "description": "Team docs",
                "category": "user",
                "max_pages": 5,
            },
        )
        assert added.isError is False
        assert added.structuredContent is not None
        assert added.structuredContent["name"] == "teamdocs"

        removed = await session.call_tool("remove_source", {"name": "teamdocs", "delete_cache": True})
        assert removed.isError is False
        assert removed.structuredContent is not None
        assert removed.structuredContent["removed"] is True

        rejected = await session.call_tool("fetch_url", {"url": "https://localhost/admin"})
        assert rejected.isError is True

        bad_category = await session.call_tool("list_sources", {"category": 123})
        assert bad_category.isError is True
        assert "validation error" in bad_category.content[0].text.lower()

        missing_pattern = await session.call_tool("grep_docs", {})
        assert missing_pattern.isError is True
        assert "validation error" in missing_pattern.content[0].text.lower()

        bad_line = await session.call_tool(
            "read_doc",
            {"library": "react", "path": "index.md", "line_start": True},
        )
        assert bad_line.isError is True
        assert "validation error" in bad_line.content[0].text.lower()

        bad_extract_limit = await session.call_tool(
            "parallel_context_pack",
            {"objective": "Parallel docs", "extract_limit": 21, "dry_run": True},
        )
        assert bad_extract_limit.isError is True
        assert "validation error" in bad_extract_limit.content[0].text.lower()

        bad_kind = await session.call_tool(
            "parallel_api_pack",
            {"source": str(pack_dir / "documents.ndjson"), "kind": "graphql"},
        )
        assert bad_kind.isError is True
        assert "validation error" in bad_kind.content[0].text.lower()

        bad_prepare = await session.call_tool(
            "pack_prepare",
            {"pack_dir": str(pack_dir), "search_queries": ["ok", ""]},
        )
        assert bad_prepare.isError is True
        assert "'search_queries' must be a list of non-empty strings" in bad_prepare.content[0].text
