"""
docpull - Context dependencies for AI agents.

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

__version__ = "6.2.0"

from .cache import CacheManager, StreamingDeduplicator
from .context_ci import CIThresholds, ContextCIError, run_context_ci
from .context_packs import (
    async_build_dataset_pack,
    async_build_package_pack,
    async_build_paper_pack,
    async_build_repo_pack,
    async_build_standards_pack,
    async_build_transcript_pack,
    async_build_wiki_pack,
    build_brand_pack,
    build_dataset_pack,
    build_feed_pack,
    build_image_pack,
    build_openapi_pack,
    build_package_pack,
    build_paper_pack,
    build_policy_pack,
    build_product_pack,
    build_repo_pack,
    build_standards_pack,
    build_styleguide_pack,
    build_transcript_pack,
    build_wiki_pack,
    capture_screenshot_pack,
)
from .contracts import (
    ArtifactManifest,
    ChangeEvent,
    EvidenceSpan,
    IntelligenceBundle,
    SourceAuthority,
    WorkflowRequest,
    WorkflowResult,
    bundled_schema_path,
    write_contract_schemas,
)
from .conversion.chunking import Chunk, TokenCounter, chunk_markdown
from .core.fetcher import Fetcher, fetch_blocking, fetch_one
from .document_parse import DocumentParseError, ParsedDocument, parse_documents, parse_one_document
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
from .local_workflows import audit_pack, refresh_pack
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
from .output_contract import validate_pack_contract
from .pack_reader import LocalPack, PackReadError, PackSource, load_pack
from .pack_tools import (
    build_citation_map,
    build_company_brain_bundle,
    build_intelligence_bundle,
    build_research_brief,
    diff_packs,
    extract_pack_entities,
    prepare_pack,
    score_pack,
    score_pack_sources,
    search_pack,
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
from .server import PackASGIApp, PackServerError, create_pack_app
from .share import ReportHTTPServer, ShareError, create_report_server, render_report_document
from .surface import PUBLIC_SDK_EXPORTS
from .workflows import async_run_workflow, create_workflow_request, run_workflow

__all__ = [
    "__version__",
    "async_build_dataset_pack",
    "async_build_package_pack",
    "async_build_paper_pack",
    "async_build_repo_pack",
    "async_build_standards_pack",
    "async_build_transcript_pack",
    "async_build_wiki_pack",
    "async_run_workflow",
    "ArtifactManifest",
    "ChangeEvent",
    "EvidenceSpan",
    "IntelligenceBundle",
    "SourceAuthority",
    "WorkflowRequest",
    "WorkflowResult",
    "bundled_schema_path",
    "write_contract_schemas",
    "create_workflow_request",
    "run_workflow",
    "Fetcher",
    "fetch_blocking",
    "fetch_one",
    "refresh_pack",
    "audit_pack",
    "build_dataset_pack",
    "build_brand_pack",
    "build_feed_pack",
    "build_openapi_pack",
    "build_package_pack",
    "build_paper_pack",
    "build_policy_pack",
    "build_product_pack",
    "build_repo_pack",
    "build_standards_pack",
    "build_styleguide_pack",
    "build_transcript_pack",
    "build_wiki_pack",
    "build_image_pack",
    "capture_screenshot_pack",
    "parse_documents",
    "parse_one_document",
    "DocumentParseError",
    "ParsedDocument",
    "score_pack",
    "score_pack_sources",
    "diff_packs",
    "build_citation_map",
    "build_intelligence_bundle",
    "build_company_brain_bundle",
    "extract_pack_entities",
    "search_pack",
    "build_research_brief",
    "prepare_pack",
    "validate_pack_contract",
    "run_context_ci",
    "ContextCIError",
    "CIThresholds",
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
    "create_report_server",
    "render_report_document",
    "ReportHTTPServer",
    "ShareError",
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

assert tuple(__all__) == PUBLIC_SDK_EXPORTS
