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
    "refresh_pack",
    "audit_pack",
    "answer_pack",
    "build_brand_pack",
    "build_styleguide_pack",
    "build_product_pack",
    "extract_schema",
    "build_image_pack",
    "capture_screenshot_pack",
    "build_search_pack",
    "export_pack",
    "ExportResult",
    "score_pack",
    "score_pack_sources",
    "diff_packs",
    "build_citation_map",
    "extract_pack_entities",
    "search_pack",
    "build_research_brief",
    "prepare_pack",
    "GraphError",
    "build_graph",
    "load_graph",
    "graph_status",
    "query_graph",
    "graph_neighbors",
    "refresh_graph",
    "load_pack",
    "LocalPack",
    "PackReadError",
    "PackSource",
    "create_pack_app",
    "PackASGIApp",
    "PackServerError",
    "create_report_server",
    "render_report_document",
    "ReportHTTPServer",
    "ShareError",
    "extract_pack",
    "map_sources",
    "crawl_pack",
    "research_pack",
    "entities_pack",
    "validate_structured_output",
    "ParityWorkflowError",
    "ScrapeResult",
    "ScrapeRunResult",
    "Scraper",
    "scrape_one",
    "scrape_one_blocking",
    "scrape_site",
    "PageContext",
    "PolicyConfig",
    "DocpullConfig",
    "ProfileName",
    "CrawlConfig",
    "ContentFilterConfig",
    "OutputConfig",
    "NetworkConfig",
    "PerformanceConfig",
    "CacheConfig",
    "RenderActionPolicy",
    "RenderConfig",
    "RenderViewport",
    "Renderer",
    "RenderedPage",
    "AgentBrowserRenderer",
    "VercelSandboxRenderer",
    "E2BSandboxRenderer",
    "RenderError",
    "RendererUnavailableError",
    "agent_browser_binary",
    "check_agent_browser_availability",
    "check_vercel_sandbox_availability",
    "check_e2b_sandbox_availability",
    "check_render_backend_availability",
    "estimate_cloud_render_cost_usd",
    "render_url",
    "render_url_to_directory",
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
    "brand_pack",
    "styleguide_pack",
    "product_pack",
    "extract_schema",
    "image_pack",
    "screenshot_pack",
    "search_pack",
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
}

EXPECTED_CLI_WORKFLOWS = {
    "render",
    "discover",
    "policy",
    "auth",
    "refresh",
    "answer-pack",
    "export",
    "serve",
    "share",
    "monitor",
    "mcp",
    "parallel",
    "pack",
    "graph",
    "evidence-pack",
    "benchmark",
    "provider",
    "providers",
    "brand-pack",
    "styleguide-pack",
    "product-pack",
    "extract-schema",
    "image-pack",
    "screenshot-pack",
    "search-pack",
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
    assert "API** means the Python SDK / library API unless explicitly" in contract
    assert "hosted HTTP API. The hosted HTTP" in contract
    assert "Hosted HTTP API" in contract
    assert "Core-aligned" in contract
    assert "Adapted" in contract
    assert "Surface-specific" in contract
    assert "not 1:1 flag parity" in contract
