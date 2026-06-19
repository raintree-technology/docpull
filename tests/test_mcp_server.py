"""End-to-end tests for the stdio MCP server."""

from __future__ import annotations

import json
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
    pack_dir = tmp_path / "pack"
    sources_dir = pack_dir / "sources"
    sources_dir.mkdir(parents=True)
    record = {
        "document_id": "doc_1",
        "url": "https://docs.parallel.ai/api-reference/search/search",
        "title": "Parallel Search API",
        "content": "Parallel Search API returns cited JSON results for live agent search.",
        "content_hash": "hash_1",
        "source_type": "parallel_extract",
    }
    (pack_dir / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
    (pack_dir / "corpus.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document_count": 1,
                "record_count": 1,
                "records": [
                    {
                        "document_id": record["document_id"],
                        "url": record["url"],
                        "content_hash": record["content_hash"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (sources_dir / "01.md").write_text(str(record["content"]), encoding="utf-8")
    (pack_dir / "sources.md").write_text("# Sources\n", encoding="utf-8")
    (pack_dir / "parallel.pack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "parallel",
                "workflow": "context-pack",
                "objective": "Review Parallel Search API",
                "request_options": {"source_policy": {"include_domains": ["docs.parallel.ai"]}},
                "extract_error_count": 0,
                "record_count": 1,
                "sources": [
                    {
                        "index": 1,
                        "url": record["url"],
                        "title": record["title"],
                        "path": "sources/01.md",
                    }
                ],
                "artifacts": {
                    "documents_ndjson": "documents.ndjson",
                    "corpus_manifest": "corpus.manifest.json",
                    "sources": "sources.md",
                },
            }
        ),
        encoding="utf-8",
    )

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
