"""DocPull — reproducible context dependencies for AI agents.

The public SDK is loaded lazily. Importing :mod:`docpull` is common to every
CLI command and Python submodule import, so the package root must not eagerly
load optional workflows or rendering backends. Attribute access and
``from docpull import ...`` retain the public contract while loading only the
module that owns the requested symbol.
"""
# ruff: noqa: F401 - TYPE_CHECKING imports document the lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .surface import PUBLIC_SDK_EXPORTS

__version__ = "6.4.0"

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    **{
        name: (".context_packs", name)
        for name in (
            "async_build_dataset_pack",
            "async_build_package_pack",
            "async_build_paper_pack",
            "async_build_repo_pack",
            "async_build_standards_pack",
            "async_build_transcript_pack",
            "async_build_wiki_pack",
            "build_brand_pack",
            "build_dataset_pack",
            "build_feed_pack",
            "build_image_pack",
            "build_openapi_pack",
            "build_package_pack",
            "build_paper_pack",
            "build_policy_pack",
            "build_relationship_pack",
            "build_product_pack",
            "build_repo_pack",
            "build_standards_pack",
            "build_styleguide_pack",
            "build_website_pack",
            "build_transcript_pack",
            "build_wiki_pack",
            "capture_screenshot_pack",
            "validate_website_snapshot_pack",
        )
    },
    **{
        name: (".contracts", name)
        for name in (
            "ArtifactManifest",
            "ChangeEvent",
            "EvidenceSpan",
            "IntelligenceBundle",
            "CoverageResult",
            "RelationshipCandidate",
            "RelationshipPack",
            "SourceAuthority",
            "WorkflowRequest",
            "WorkflowResult",
            "WebsiteSnapshot",
            "WebsiteSnapshotDocument",
            "bundled_schema_path",
            "write_contract_schemas",
        )
    },
    **{
        name: (".workflows", name)
        for name in ("async_run_workflow", "create_workflow_request", "run_workflow")
    },
    **{name: (".core.fetcher", name) for name in ("Fetcher", "fetch_blocking", "fetch_one")},
    **{name: (".conversion.chunking", name) for name in ("Chunk", "TokenCounter", "chunk_markdown")},
    "PageContext": (".pipeline.base", "PageContext"),
    **{name: (".pipeline.steps", name) for name in ("SqliteSearchResult", "search_sqlite_documents")},
    **{
        name: (".models.config", name)
        for name in (
            "BudgetConfig",
            "CacheConfig",
            "ContentFilterConfig",
            "CrawlConfig",
            "DocpullConfig",
            "NetworkConfig",
            "OutputConfig",
            "PerformanceConfig",
            "ProfileName",
            "RenderActionPolicy",
            "RenderConfig",
            "RenderViewport",
        )
    },
    **{name: (".models.events", name) for name in ("EventType", "FetchEvent", "FetchStats")},
    **{name: (".cache", name) for name in ("CacheManager", "StreamingDeduplicator")},
    "PolicyConfig": (".policy", "PolicyConfig"),
    **{name: (".local_workflows", name) for name in ("audit_pack", "refresh_pack")},
    **{
        name: (".document_parse", name)
        for name in ("DocumentParseError", "ParsedDocument", "parse_documents", "parse_one_document")
    },
    **{
        name: (".pack_tools", name)
        for name in (
            "build_citation_map",
            "build_company_brain_bundle",
            "build_intelligence_bundle",
            "build_research_brief",
            "diff_packs",
            "extract_pack_entities",
            "prepare_pack",
            "score_pack",
            "score_pack_sources",
            "search_pack",
        )
    },
    "validate_pack_contract": (".output_contract", "validate_pack_contract"),
    **{name: (".context_ci", name) for name in ("CIThresholds", "ContextCIError", "run_context_ci")},
    **{name: (".exports", name) for name in ("ExportResult", "export_pack")},
    **{name: (".pack_reader", name) for name in ("LocalPack", "PackReadError", "PackSource", "load_pack")},
    **{
        name: (".graph", name)
        for name in (
            "GraphError",
            "build_graph",
            "graph_neighbors",
            "graph_status",
            "load_graph",
            "query_graph",
            "refresh_graph",
        )
    },
    **{name: (".server", name) for name in ("PackASGIApp", "PackServerError", "create_pack_app")},
    **{
        name: (".share", name)
        for name in ("ReportHTTPServer", "ShareError", "create_report_server", "render_report_document")
    },
    **{
        name: (".rendering", name)
        for name in (
            "AgentBrowserRenderer",
            "E2BSandboxRenderer",
            "RenderedPage",
            "Renderer",
            "RenderError",
            "RendererUnavailableError",
            "VercelSandboxRenderer",
            "agent_browser_binary",
            "check_agent_browser_availability",
            "check_e2b_sandbox_availability",
            "check_render_backend_availability",
            "check_vercel_sandbox_availability",
            "estimate_cloud_render_cost_usd",
            "render_url",
            "render_url_to_directory",
        )
    },
}

__all__ = list(PUBLIC_SDK_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load one public SDK symbol on first access and cache it locally."""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Include lazy public attributes in interactive discovery."""
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
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
        build_relationship_pack,
        build_repo_pack,
        build_standards_pack,
        build_styleguide_pack,
        build_transcript_pack,
        build_website_pack,
        build_wiki_pack,
        capture_screenshot_pack,
        validate_website_snapshot_pack,
    )
    from .contracts import (
        ArtifactManifest,
        ChangeEvent,
        CoverageResult,
        EvidenceSpan,
        IntelligenceBundle,
        RelationshipCandidate,
        RelationshipPack,
        SourceAuthority,
        WebsiteSnapshot,
        WebsiteSnapshotDocument,
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
    from .workflows import async_run_workflow, create_workflow_request, run_workflow


assert tuple(__all__) == PUBLIC_SDK_EXPORTS
assert set(_LAZY_EXPORTS) == set(__all__) - {"__version__"}
