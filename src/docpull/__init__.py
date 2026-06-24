"""
docpull - Fetch and convert static/server-rendered web content to markdown.

Usage:
    from docpull import Fetcher, DocpullConfig, ProfileName

    config = DocpullConfig(
        url="https://docs.example.com",
        profile=ProfileName.RAG,
    )

    async with Fetcher(config) as fetcher:
        async for event in fetcher.run():
            print(event)
"""

__version__ = "5.0.1"

from .cache import CacheManager, StreamingDeduplicator
from .conversion.chunking import Chunk, TokenCounter, chunk_markdown
from .core.fetcher import Fetcher, fetch_blocking, fetch_one
from .exports import ExportResult, export_pack
from .graph import (
    GraphError,
    build_graph,
    graph_neighbors,
    graph_status,
    load_graph,
    query_graph,
    refresh_graph,
)
from .local_workflows import answer_pack, audit_pack, refresh_pack
from .models.config import (
    BudgetConfig,
    CacheConfig,
    ContentFilterConfig,
    CrawlConfig,
    DocpullConfig,
    NetworkConfig,
    OutputConfig,
    PerformanceConfig,
    ProfileName,
    RenderActionPolicy,
    RenderConfig,
    RenderViewport,
)
from .models.events import EventType, FetchEvent, FetchStats
from .pack_reader import LocalPack, PackReadError, PackSource, load_pack
from .pack_tools import (
    build_citation_map,
    build_research_brief,
    diff_packs,
    extract_pack_entities,
    prepare_pack,
    score_pack,
    score_pack_sources,
    search_pack,
)
from .parity import (
    ParityWorkflowError,
    crawl_pack,
    entities_pack,
    extract_pack,
    map_sources,
    research_pack,
    validate_structured_output,
)
from .pipeline.base import PageContext
from .pipeline.steps import SqliteSearchResult, search_sqlite_documents
from .policy import PolicyConfig
from .rendering import (
    AgentBrowserRenderer,
    E2BSandboxRenderer,
    RenderedPage,
    Renderer,
    RenderError,
    RendererUnavailableError,
    VercelSandboxRenderer,
    agent_browser_binary,
    check_agent_browser_availability,
    check_e2b_sandbox_availability,
    check_render_backend_availability,
    check_vercel_sandbox_availability,
    estimate_cloud_render_cost_usd,
    render_url,
    render_url_to_directory,
)
from .scraper import (
    Scraper,
    ScrapeResult,
    ScrapeRunResult,
    scrape_one,
    scrape_one_blocking,
    scrape_site,
)
from .server import PackASGIApp, PackServerError, create_pack_app

__all__ = [
    "__version__",
    "Fetcher",
    "fetch_blocking",
    "fetch_one",
    "refresh_pack",
    "audit_pack",
    "answer_pack",
    "extract_pack",
    "map_sources",
    "crawl_pack",
    "research_pack",
    "entities_pack",
    "validate_structured_output",
    "ParityWorkflowError",
    "score_pack",
    "score_pack_sources",
    "diff_packs",
    "build_citation_map",
    "extract_pack_entities",
    "search_pack",
    "build_research_brief",
    "prepare_pack",
    "export_pack",
    "ExportResult",
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
    "BudgetConfig",
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
]
