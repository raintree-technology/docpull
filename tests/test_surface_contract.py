"""Regression tests for the documented CLI / SDK / MCP surface contract."""

from __future__ import annotations

from pathlib import Path

import docpull
from docpull.cli import create_parser

ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SDK_EXPORTS = {
    "Fetcher",
    "fetch_blocking",
    "fetch_one",
    "ScrapeResult",
    "ScrapeRunResult",
    "Scraper",
    "scrape_one",
    "scrape_one_blocking",
    "scrape_site",
    "PageContext",
    "DocpullConfig",
    "ProfileName",
    "CrawlConfig",
    "ContentFilterConfig",
    "OutputConfig",
    "NetworkConfig",
    "PerformanceConfig",
    "CacheConfig",
    "EventType",
    "FetchEvent",
    "FetchStats",
    "SqliteSearchResult",
    "search_sqlite_documents",
    "CacheManager",
    "StreamingDeduplicator",
    "Chunk",
    "TokenCounter",
    "chunk_markdown",
}

EXPECTED_MCP_TOOLS = {
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
    "add_source",
    "remove_source",
}

EXPECTED_CLI_WORKFLOWS = {
    "mcp",
    "parallel",
    "pack",
    "evidence-pack",
    "benchmark",
    "provider",
    "providers",
}


def test_documented_sdk_exports_remain_public() -> None:
    assert set(docpull.__all__) >= EXPECTED_SDK_EXPORTS


def test_documented_mcp_tools_remain_registered() -> None:
    server_source = (ROOT / "src/docpull/mcp/server.py").read_text(encoding="utf-8")

    for tool_name in EXPECTED_MCP_TOOLS:
        assert f'name="{tool_name}"' in server_source


def test_documented_cli_workflows_remain_dispatched() -> None:
    cli_source = (ROOT / "src/docpull/cli.py").read_text(encoding="utf-8")

    for workflow in EXPECTED_CLI_WORKFLOWS:
        assert f'"{workflow}"' in cli_source

    args = create_parser().parse_args(["https://example.com"])
    assert args.url == "https://example.com"


def test_docs_link_to_surface_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    plugin_readme = (ROOT / "plugin/README.md").read_text(encoding="utf-8")

    assert "docs/surface-contract.md" in readme
    assert "docs/surface-contract.md" in plugin_readme


def test_surface_contract_states_non_1_to_1_policy() -> None:
    contract = (ROOT / "docs/surface-contract.md").read_text(encoding="utf-8")

    assert "DocPull exposes the same core workflows through CLI, Python SDK, and MCP" in contract
    assert "API** means the Python SDK / library API" in contract
    assert "DocPull does not currently ship a hosted HTTP API" in contract
    assert "Core-aligned" in contract
    assert "Adapted" in contract
    assert "Surface-specific" in contract
    assert "not 1:1 flag parity" in contract
