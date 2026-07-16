"""stdio MCP server exposing docpull tools to AI agents.

Requires the optional ``mcp`` Python package (install with
``pip install docpull[mcp]``). The server registers local DocPull tools:

Read-only:
- ``fetch_url(url)`` — one-shot fetch, no discovery. Agent-oriented fast path.
- ``render_url(url, ...)`` — explicit local browser render to disk.
- ``list_sources(category?)`` — show available aliases.
- ``list_indexed()`` — show what has been fetched.
- ``grep_docs(pattern, library?, limit?)`` — regex search through cached web-source Markdown.
- ``read_doc(library, path, line_start?, line_end?)`` — read a fetched Markdown file.
- ``pack_score(pack_dir, required_domains?)`` — score a context pack.
- ``pack_diff(old_pack_dir, new_pack_dir)`` — compare context packs.
- ``refresh_pack(pack_dir, ...)`` — refresh an existing local pack.
- ``audit_pack(pack_dir, ...)`` — run pack quality checks.
- ``pack_citations(pack_dir, required_domains?)`` — build a stable source map.
- ``pack_entities(pack_dir, limit?, required_domains?)`` — extract cited entities.
- ``pack_search(pack_dir, query, limit?, required_domains?)`` — search pack records.
- ``pack_brief(pack_dir, objective?, ...)`` — generate a cited local research brief.
- ``graph_status(pack_dir)`` — report whether local graph artifacts are current.
- ``graph_query(pack_dir, query, limit?)`` — search graph nodes and cited edge evidence.
- ``graph_neighbors(pack_dir, entity, limit?)`` — list cited graph neighbors for an entity.
- ``validate_policy(policy_path)`` — validate a source policy file.
- ``serve_pack_status(pack_dir)`` — inspect local pack-server health.

Write:
- ``ensure_docs(source, force?)`` — fetch (or refresh) a named source alias.
- ``pack_prepare(pack_dir, objective?, ...)`` — write standard pack intelligence artifacts.
- ``graph_build(pack_dir, entity_limit?)`` — write local source graph sidecars.
- ``graph_refresh(pack_dir, entity_limit?)`` — rebuild graph sidecars and write graph.diff.json.
- ``export_pack(pack_dir, format, output)`` — export a pack to agent/RAG formats.
- ``add_source(name, url, ...)`` — add or update a user source alias.
- ``remove_source(name, delete_cache?)`` — remove a user source alias.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal, cast

from ..accounting import (
    RunAccounting,
    blocked_action,
    budget_block_payload,
    default_route_steps,
    effective_budget_limit,
    maybe_write_run_accounting,
    paid_action_blocked,
)
from ..export_formats import EXPORT_FORMATS
from ..surface import PRUNED_MCP_TOOLS
from .tools import (
    ToolResult,
    add_source,
    ensure_docs,
    fetch_url,
    grep_docs,
    list_indexed,
    list_sources,
    read_doc,
    remove_source,
)

logger = logging.getLogger(__name__)

SERVER_INSTRUCTIONS = (
    "Call list_sources to discover aliases before ensure_docs. "
    "Use ensure_docs for a whole named web source (cached 7 days), fetch_url for one "
    "ad-hoc HTTPS page. After ensure_docs, use grep_docs to find passages "
    "and read_doc to pull the surrounding lines. grep_docs/read_doc are historical "
    "tool names that work on cached Markdown from any fetched source. Use add_source / "
    "remove_source to manage the user-defined registry."
)


# Output schemas — keep these next to the tool list so they stay in sync.
# Tools that return free-form Markdown (fetch_url) intentionally omit a
# schema; the rest expose structured payloads alongside the rendered text.

_ACCOUNTING_OUTPUT_SCHEMA = {
    "type": "object",
    "description": (
        "Non-secret run accounting receipt. Mirrors run.accounting.json when an artifact was written."
    ),
    "properties": {
        "schema_version": {"type": "integer"},
        "generated_at": {"type": "string"},
        "budget_limit_usd": {"type": ["number", "null"]},
        "estimated_paid_cost_usd": {"type": "number"},
        "actual_paid_cost_usd": {"type": ["number", "null"]},
        "paid_request_count": {"type": "integer"},
        "local_browser_seconds": {"type": "number"},
        "http_request_count": {"type": "integer"},
        "cache_hit_count": {"type": "integer"},
        "blocked_actions": {"type": "array", "items": {"type": "object"}},
        "route_steps": {"type": "array", "items": {"type": "object"}},
        "command": {"type": "string"},
        "metadata": {"type": "object"},
        "artifact_path": {"type": "string"},
    },
    "required": [
        "schema_version",
        "generated_at",
        "budget_limit_usd",
        "estimated_paid_cost_usd",
        "actual_paid_cost_usd",
        "paid_request_count",
        "local_browser_seconds",
        "http_request_count",
        "cache_hit_count",
        "blocked_actions",
        "route_steps",
    ],
}

_LIST_SOURCES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "url": {"type": "string"},
                    "description": {"type": "string"},
                    "category": {"type": "string"},
                    "max_pages": {"type": "integer"},
                },
                "required": ["name", "url", "description", "category"],
            },
        },
    },
    "required": ["sources"],
}

_LIST_INDEXED_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "libraries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "file_count": {"type": "integer"},
                    "fresh": {"type": "boolean"},
                    "fetched_at": {"type": "string"},
                    "age_seconds": {"type": "integer"},
                },
                "required": ["name", "file_count", "fresh"],
            },
        },
    },
    "required": ["libraries"],
}

_GREP_DOCS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string"},
        "total_matches": {"type": "integer"},
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "library": {"type": "string"},
                    "path": {
                        "type": "string",
                        "description": "Relative to the library root; pass directly to read_doc",
                    },
                    "match_count": {"type": "integer"},
                    "matches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "lineno": {"type": "integer"},
                                "before": {"type": "array", "items": {"type": "string"}},
                                "line": {"type": "string"},
                                "after": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["lineno", "before", "line", "after"],
                        },
                    },
                },
                "required": ["library", "path", "match_count", "matches"],
            },
        },
        "truncated": {"type": "boolean"},
        "timed_out": {"type": "boolean"},
    },
    "required": ["pattern", "total_matches", "files", "truncated", "timed_out"],
}

_READ_DOC_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "library": {"type": "string"},
        "path": {"type": "string"},
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "total_lines": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["library", "path", "line_start", "line_end", "total_lines", "text"],
}

_ENSURE_DOCS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "cached": {"type": "boolean"},
        "file_count": {"type": "integer"},
        "pages_fetched": {"type": "integer"},
        "pages_skipped": {"type": "integer"},
        "pages_failed": {"type": "integer"},
        "target_dir": {"type": "string"},
    },
    "required": ["source", "cached", "target_dir"],
}

_ADD_SOURCE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "url": {"type": "string"},
        "replaced": {"type": "boolean"},
        "shadowed_builtin": {"type": "boolean"},
        "config_path": {"type": "string"},
    },
    "required": ["name", "url", "replaced", "shadowed_builtin", "config_path"],
}

_REMOVE_SOURCE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "removed": {"type": "boolean"},
        "cache_deleted": {"type": "boolean"},
        "config_path": {"type": "string"},
    },
    "required": ["name", "removed", "cache_deleted"],
}

_PACK_SCORE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "grade": {"type": "string"},
        "summary": {"type": "object"},
        "issues": {"type": "array"},
        "warnings": {"type": "array"},
    },
    "required": ["score", "grade", "summary", "issues", "warnings"],
}

_PACK_DIFF_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "added_urls": {"type": "array", "items": {"type": "string"}},
        "removed_urls": {"type": "array", "items": {"type": "string"}},
        "changed_urls": {"type": "array", "items": {"type": "string"}},
        "unchanged_urls": {"type": "array", "items": {"type": "string"}},
        "old_record_count": {"type": "integer"},
        "new_record_count": {"type": "integer"},
    },
    "required": ["added_urls", "removed_urls", "changed_urls", "unchanged_urls"],
}

_PACK_CITATIONS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "source_count": {"type": "integer"},
        "record_count": {"type": "integer"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "sources": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["source_count", "record_count", "sources"],
}

_PACK_ENTITIES_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entity_count": {"type": "integer"},
        "source_count": {"type": "integer"},
        "record_count": {"type": "integer"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "entities": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["entity_count", "source_count", "record_count", "entities"],
}

_PACK_BRIEF_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "object"},
        "load_plan": {"type": "array", "items": {"type": "object"}},
        "key_excerpts": {"type": "array", "items": {"type": "object"}},
        "entities": {"type": "array", "items": {"type": "object"}},
        "artifacts": {"type": "object"},
    },
    "required": ["objective", "summary", "load_plan", "key_excerpts", "entities"],
}

_PACK_SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "source_count": {"type": "integer"},
        "record_count": {"type": "integer"},
        "result_count": {"type": "integer"},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "results": {"type": "array", "items": {"type": "object"}},
        "citations": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["query", "source_count", "record_count", "result_count", "results", "citations"],
}

_PACK_PREPARE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "objective": {"type": "string"},
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "expected_domains": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "object"},
        "artifacts": {"type": "object"},
    },
    "required": ["objective", "search_queries", "summary", "artifacts"],
}

_GRAPH_BUILD_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "pack_dir": {"type": "string"},
        "pack_fingerprint": {"type": "object"},
        "summary": {"type": "object"},
        "top_entities": {"type": "array", "items": {"type": "object"}},
        "artifacts": {"type": "object"},
    },
    "required": ["status", "pack_dir", "pack_fingerprint", "summary", "artifacts"],
}

_GRAPH_STATUS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "pack_dir": {"type": "string"},
        "current_fingerprint": {"type": "object"},
        "graph_fingerprint": {"type": ["object", "null"]},
        "diff": {"type": "object"},
        "reason": {"type": "string"},
    },
    "required": ["status", "pack_dir", "current_fingerprint"],
}

_GRAPH_QUERY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "status": {"type": "string"},
        "result_count": {"type": "integer"},
        "results": {"type": "array", "items": {"type": "object"}},
        "citations": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["query", "status", "result_count", "results", "citations"],
}

_GRAPH_NEIGHBORS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entity": {"type": "string"},
        "status": {"type": "string"},
        "matched_entity_count": {"type": "integer"},
        "neighbor_count": {"type": "integer"},
        "matched_entities": {"type": "array", "items": {"type": "object"}},
        "neighbors": {"type": "array", "items": {"type": "object"}},
        "citations": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["entity", "status", "matched_entity_count", "neighbor_count", "neighbors"],
}

_GRAPH_REFRESH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "old_status": {"type": "string"},
        "new_status": {"type": "string"},
        "pack_dir": {"type": "string"},
        "summary": {"type": "object"},
        "added_nodes": {"type": "array", "items": {"type": "string"}},
        "removed_nodes": {"type": "array", "items": {"type": "string"}},
        "added_edges": {"type": "array", "items": {"type": "string"}},
        "removed_edges": {"type": "array", "items": {"type": "string"}},
        "artifacts": {"type": "object"},
    },
    "required": ["old_status", "new_status", "pack_dir", "summary", "artifacts"],
}

_REFRESH_PACK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "dry_run": {"type": "boolean"},
        "pack_dir": {"type": "string"},
        "output_dir": {"type": "string"},
        "summary": {"type": "object"},
        "diff": {"type": "object"},
        "artifacts": {"type": "object"},
    },
    "required": ["dry_run", "pack_dir", "output_dir", "summary", "diff", "artifacts"],
}

_AUDIT_PACK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer"},
        "grade": {"type": "string"},
        "passed": {"type": "boolean"},
        "summary": {"type": "object"},
        "dimensions": {"type": "object"},
        "issues": {"type": "array"},
        "warnings": {"type": "array"},
        "artifacts": {"type": "object"},
    },
    "required": ["score", "grade", "passed", "summary", "dimensions"],
}

_VALIDATE_POLICY_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "policy_path": {"type": "string"},
        "source_policy": {"type": "object"},
        "explain": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["valid", "policy_path", "source_policy", "explain"],
}

_RENDER_URL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "backend": {"type": "string"},
        "html_path": {"type": "string"},
        "sidecar_path": {"type": "string"},
        "html_bytes": {"type": "integer"},
        "html_sha256": {"type": "string"},
        "output_dir": {"type": "string"},
        "dry_run": {"type": "boolean"},
        "blocked_by_budget": {"type": "boolean"},
        "budget_limit_usd": {"type": ["number", "null"]},
        "blocked_action": {"type": "object"},
        "accounting": _ACCOUNTING_OUTPUT_SCHEMA,
    },
    "required": ["url", "backend"],
}

_EXPORT_PACK_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "format": {"type": "string"},
        "output_path": {"type": "string"},
        "record_count": {"type": "integer"},
        "artifacts": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["format", "output_path", "record_count", "artifacts"],
}

_SERVE_PACK_STATUS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "pack_dir": {"type": "string"},
        "document_count": {"type": "integer"},
        "source_count": {"type": "integer"},
        "document_source": {"type": "string"},
        "sqlite_fts_available": {"type": "boolean"},
    },
    "required": ["status", "pack_dir", "document_count", "source_count"],
}

_WORKFLOW_RESULT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_version": {"const": "workflow.result.v1"},
        "request_id": {"type": "string"},
        "workflow": {"type": "string"},
        "status": {"type": "string"},
        "pack_identity": {"type": "object"},
        "run_identity": {"type": "object"},
        "progress_events": {"type": "array", "items": {"type": "object"}},
        "warnings": {"type": "array", "items": {"type": "object"}},
        "failures": {"type": "array", "items": {"type": "object"}},
        "budget_usage": {"type": "object"},
        "hashes": {"type": "object"},
        "replay_configuration": {"type": "object"},
    },
    "required": [
        "contract_version",
        "request_id",
        "workflow",
        "status",
        "pack_identity",
        "run_identity",
        "progress_events",
        "warnings",
        "failures",
        "budget_usage",
        "hashes",
        "replay_configuration",
    ],
}

_INTELLIGENCE_BUNDLE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "contract_version": {"const": "intelligence.bundle.v1"},
        "bundle_id": {"type": "string"},
        "bundle_hash": {"type": "string"},
        "pack_identity": {"type": "object"},
        "run_identity": {"type": "object"},
        "source_snapshots": {"type": "array", "items": {"type": "object"}},
        "document_versions": {"type": "array", "items": {"type": "object"}},
        "observations": {"type": "array", "items": {"type": "object"}},
        "relationship_candidates": {"type": "array", "items": {"type": "object"}},
        "change_candidates": {"type": "array", "items": {"type": "object"}},
    },
    "required": [
        "contract_version",
        "bundle_id",
        "bundle_hash",
        "pack_identity",
        "run_identity",
        "source_snapshots",
        "document_versions",
        "observations",
        "change_candidates",
    ],
}


def _coerce_int(value: Any, *, name: str, default: int) -> int:
    """Accept int or numeric string; reject anything else with a clear error."""
    if value is None:
        return default
    if isinstance(value, bool):  # bool is a subclass of int — exclude
        raise ValueError(f"'{name}' must be an integer, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as err:
            raise ValueError(f"'{name}' must be an integer: {err}") from None
    raise ValueError(f"'{name}' must be an integer, got {type(value).__name__}")


def _require_str(arguments: dict[str, Any], key: str) -> str:
    if key not in arguments:
        raise ValueError(f"Missing required argument: '{key}'")
    value = arguments[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty string")
    return value


def _string_list_arg(arguments: dict[str, Any], key: str) -> list[str]:
    value = arguments.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"'{key}' must be a list of non-empty strings")
    return value


def _path_arg(arguments: dict[str, Any], key: str, default: str | None = None) -> Path:
    value = arguments.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"'{key}' must be a non-empty path string")
    return Path(value)


def _read_accounting_payload(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["artifact_path"] = str(path)
    return payload


def _mcp_accounting_payload(
    accounting: RunAccounting,
    *,
    output_dir: Path | None = None,
    budget_limit_usd: float | None,
    paid_capable: bool,
) -> dict[str, Any]:
    path = None
    if output_dir is not None:
        path = maybe_write_run_accounting(
            output_dir,
            budget_limit_usd=budget_limit_usd,
            paid_capable=paid_capable,
            accounting=accounting,
        )
    if path is not None:
        artifact_payload = _read_accounting_payload(path)
        if artifact_payload is not None:
            return artifact_payload
    return accounting.to_dict()


async def _dispatch_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    progress_callback_factory: Callable[[], Awaitable[Any]] | None = None,
) -> ToolResult:
    """Run one MCP tool and return the transport-neutral tool result."""
    try:
        if name in PRUNED_MCP_TOOLS:
            raise ValueError(f"Unknown tool: {name}")

        if name == "fetch_url":
            url = _require_str(arguments, "url")
            max_tokens = _coerce_int(arguments.get("max_tokens"), name="max_tokens", default=0)
            remote_documents = arguments.get("remote_documents", "off")
            if remote_documents not in {"off", "pdf"}:
                raise ValueError("'remote_documents' must be off or pdf")
            remote_document_backend = arguments.get("remote_document_backend", "auto")
            if remote_document_backend not in {"auto", "pypdf", "markitdown", "unstructured"}:
                raise ValueError("'remote_document_backend' must be auto, pypdf, markitdown, or unstructured")
            remote_document_timeout_seconds = _coerce_int(
                arguments.get("remote_document_timeout_seconds"),
                name="remote_document_timeout_seconds",
                default=60,
            )
            remote_document_memory_mib = _coerce_int(
                arguments.get("remote_document_memory_mib"),
                name="remote_document_memory_mib",
                default=1024,
            )
            result = await fetch_url(
                url,
                max_tokens=max_tokens or None,
                remote_documents=cast(Literal["off", "pdf"], remote_documents),
                remote_document_backend=cast(
                    Literal["auto", "pypdf", "markitdown", "unstructured"],
                    remote_document_backend,
                ),
                remote_document_timeout_seconds=remote_document_timeout_seconds,
                remote_document_memory_mib=remote_document_memory_mib,
            )

        elif name == "render_url":
            url = _require_str(arguments, "url")
            timeout_arg = arguments.get("timeout_seconds", 30)
            if not isinstance(timeout_arg, int | float):
                raise ValueError("'timeout_seconds' must be a number")
            wait_for = arguments.get("wait_for", "load")
            if wait_for not in {"load", "domcontentloaded", "networkidle"}:
                raise ValueError("'wait_for' must be load, domcontentloaded, or networkidle")
            runtime_to_backend = {
                "local": "agent-browser",
                "vercel": "vercel-sandbox",
                "e2b": "e2b-sandbox",
            }
            runtime = arguments.get("runtime", "local")
            if not isinstance(runtime, str) or runtime not in runtime_to_backend:
                raise ValueError("'runtime' must be local, vercel, or e2b")
            backend = runtime_to_backend[runtime]
            cloud_agent_browser_install = arguments.get("cloud_agent_browser_install", "skip")
            if cloud_agent_browser_install not in {"auto", "skip"}:
                raise ValueError("'cloud_agent_browser_install' must be auto or skip")
            cloud_result_transport = arguments.get("cloud_result_transport", "auto")
            if cloud_result_transport not in {"auto", "stdout", "file"}:
                raise ValueError("'cloud_result_transport' must be auto, stdout, or file")
            cloud_max_estimated_cost = arguments.get("cloud_max_estimated_cost_usd")
            if cloud_max_estimated_cost is not None and not isinstance(cloud_max_estimated_cost, int | float):
                raise ValueError("'cloud_max_estimated_cost_usd' must be a number")
            budget_arg = arguments.get("budget")
            if budget_arg is not None and not isinstance(budget_arg, int | float):
                raise ValueError("'budget' must be a number")
            budget_limit = effective_budget_limit(
                float(budget_arg) if budget_arg is not None else None,
                float(cloud_max_estimated_cost)
                if cloud_max_estimated_cost is not None and backend in {"vercel-sandbox", "e2b-sandbox"}
                else None,
            )
            template = arguments.get("template")
            if template is not None and not isinstance(template, str):
                raise ValueError("'template' must be a string")
            cloud_agent_browser_binary = arguments.get("cloud_agent_browser_binary", "agent-browser")
            if not isinstance(cloud_agent_browser_binary, str):
                raise ValueError("'cloud_agent_browser_binary' must be a string")
            output_dir = _path_arg(arguments, "output_dir", "rendered")
            estimated = 0.0
            paid_capable_render = backend in {"vercel-sandbox", "e2b-sandbox"}
            accounting = RunAccounting(
                budget_limit_usd=budget_limit,
                estimated_paid_cost_usd=estimated,
                route_steps=default_route_steps(
                    include_local_render=True,
                    include_cloud=paid_capable_render,
                    budget_limit_usd=budget_limit,
                ),
                command="mcp render_url",
                metadata={
                    "mcp_tool": "render_url",
                    "url": url,
                    "runtime": runtime,
                    "backend": backend,
                },
            )
            render_blocked = False
            if backend in {"vercel-sandbox", "e2b-sandbox"}:
                from ..models.config import RenderConfig
                from ..rendering import estimate_cloud_render_cost_usd

                cloud_backend = cast(Literal["vercel-sandbox", "e2b-sandbox"], backend)
                estimated = estimate_cloud_render_cost_usd(
                    cloud_backend,
                    RenderConfig(
                        mode="agent-browser",
                        backend=cloud_backend,
                        timeout_seconds=float(timeout_arg),
                    ),
                )
                accounting.estimated_paid_cost_usd = estimated
                if paid_action_blocked(budget_limit, estimated_cost_usd=estimated):
                    render_blocked = True
                    accounting.blocked_actions.append(
                        blocked_action(
                            f"render:{backend}",
                            budget_limit_usd=budget_limit,
                            estimated_cost_usd=estimated,
                            provider=backend,
                        )
                    )
                    accounting_payload = _mcp_accounting_payload(
                        accounting,
                        output_dir=output_dir,
                        budget_limit_usd=budget_limit,
                        paid_capable=True,
                    )
                    result = ToolResult(
                        f"Render blocked by budget before {backend}.",
                        data={
                            "url": url,
                            "backend": backend,
                            "output_dir": str(output_dir),
                            "dry_run": False,
                            **budget_block_payload(
                                f"render:{backend}",
                                budget_limit_usd=budget_limit,
                                estimated_cost_usd=estimated,
                                provider=backend,
                            ),
                            "accounting": accounting_payload,
                        },
                    )
                else:
                    accounting.paid_request_count = 1
            if not render_blocked:
                from ..rendering import render_url_to_directory

                artifact = await render_url_to_directory(
                    url,
                    output_dir,
                    config={
                        "mode": "agent-browser",
                        "backend": backend,
                        "allowed_domains": _string_list_arg(arguments, "allowed_domains"),
                        "timeout_seconds": float(timeout_arg),
                        "wait_for": wait_for,
                        "cloud_agent_browser_install": cloud_agent_browser_install,
                        "cloud_result_transport": cloud_result_transport,
                        "cloud_max_estimated_cost_usd": cloud_max_estimated_cost,
                        "cloud_agent_browser_binary": cloud_agent_browser_binary,
                        "e2b_template": template,
                    },
                )
                accounting_payload = _mcp_accounting_payload(
                    accounting,
                    output_dir=output_dir,
                    budget_limit_usd=budget_limit,
                    paid_capable=paid_capable_render,
                )
                payload = {
                    "url": artifact.page.url,
                    "backend": artifact.page.backend,
                    "html_path": str(artifact.html_path),
                    "sidecar_path": str(artifact.sidecar_path),
                    "html_bytes": artifact.page.html_bytes,
                    "html_sha256": artifact.page.html_sha256,
                    "accounting": accounting_payload,
                }
                result = ToolResult(
                    f"Rendered {artifact.page.html_bytes} bytes: {artifact.html_path}",
                    data=payload,
                )

        elif name == "ensure_docs":
            source = _require_str(arguments, "source")
            on_progress = await progress_callback_factory() if progress_callback_factory is not None else None
            result = await ensure_docs(
                source,
                force=bool(arguments.get("force", False)),
                profile=arguments.get("profile"),
                on_progress=on_progress,
            )

        elif name == "list_sources":
            category = arguments.get("category")
            if category is not None and not isinstance(category, str):
                raise ValueError("'category' must be a string")
            result = list_sources(category)

        elif name == "list_indexed":
            result = list_indexed()

        elif name == "grep_docs":
            pattern = _require_str(arguments, "pattern")
            library = arguments.get("library")
            if library is not None and not isinstance(library, str):
                raise ValueError("'library' must be a string")
            result = grep_docs(
                pattern,
                library=library,
                limit=_coerce_int(arguments.get("limit"), name="limit", default=20),
                case_sensitive=bool(arguments.get("case_sensitive", False)),
                context=_coerce_int(arguments.get("context"), name="context", default=1),
            )

        elif name == "read_doc":
            library = _require_str(arguments, "library")
            path = _require_str(arguments, "path")
            line_start = arguments.get("line_start")
            line_end = arguments.get("line_end")
            result = read_doc(
                library,
                path,
                line_start=_coerce_int(line_start, name="line_start", default=0) or None,
                line_end=_coerce_int(line_end, name="line_end", default=0) or None,
            )

        elif name in {
            "workflow_run",
            "brand_pack",
            "product_pack",
            "styleguide_pack",
            "image_pack",
            "screenshot_pack",
            "policy_pack",
            "relationship_pack",
        }:
            from ..policy import PolicyConfig
            from ..workflows import create_workflow_request, run_workflow

            workflow_aliases = {
                "brand_pack": ("brand-pack", "domain_or_url", "packs/brand"),
                "product_pack": ("product-pack", "url_or_domain", "packs/products"),
                "styleguide_pack": ("styleguide-pack", "domain_or_url", "packs/styleguide"),
                "image_pack": ("image-pack", "url_or_pack", "packs/images"),
                "screenshot_pack": ("screenshot-pack", "url", "packs/screenshot"),
                "policy_pack": ("policy-pack", "domain_or_url", "packs/policies"),
                "relationship_pack": ("relationship-pack", "source", "packs/relationships"),
            }
            if name == "workflow_run":
                workflow_name = _require_str(arguments, "workflow")
                raw_input = arguments.get("input")
                if raw_input is not None:
                    if not isinstance(raw_input, dict) or not raw_input:
                        raise ValueError("'input' must be a non-empty object")
                    input_payload: dict[str, Any] | None = raw_input
                    value: str | None = None
                else:
                    value = _require_str(arguments, "value")
                    input_payload = None
                default_output = f"packs/{workflow_name.replace('-pack', '').replace('_', '-')}"
            else:
                workflow_name, input_key, default_output = workflow_aliases[name]
                raw_sources = arguments.get("sources") if name == "relationship_pack" else None
                if raw_sources is not None:
                    if (
                        not isinstance(raw_sources, list)
                        or not raw_sources
                        or not all(isinstance(item, (str, dict)) for item in raw_sources)
                    ):
                        raise ValueError("'sources' must be a non-empty array of strings or objects")
                    input_payload = {"sources": raw_sources}
                    value = None
                else:
                    value = _require_str(arguments, input_key)
                    input_payload = None
            output_dir = _path_arg(arguments, "output_dir", default_output)
            policy_path_raw = arguments.get("policy")
            if policy_path_raw is not None and not isinstance(policy_path_raw, str):
                raise ValueError("'policy' must be a path string")
            workflow_policy = PolicyConfig.from_file(Path(policy_path_raw)) if policy_path_raw else None
            reserved = {
                "workflow",
                "value",
                "input",
                "sources",
                "source",
                "domain_or_url",
                "url_or_domain",
                "url_or_pack",
                "url",
                "output_dir",
                "policy",
                "options",
            }
            options = {key: value for key, value in arguments.items() if key not in reserved}
            nested_options = arguments.get("options")
            if nested_options is not None:
                if not isinstance(nested_options, dict):
                    raise ValueError("'options' must be an object")
                options.update(nested_options)
            workflow_request = create_workflow_request(
                workflow_name,
                value,
                output_dir=output_dir,
                input_payload=input_payload,
                options=options,
                policy=workflow_policy,
            )
            workflow_payload = await asyncio.to_thread(run_workflow, workflow_request)
            result = ToolResult(
                f"Workflow {workflow_payload['workflow']}: {workflow_payload['status']}",
                data=workflow_payload,
            )

        elif name == "intelligence_bundle":
            from ..pack_tools import build_intelligence_bundle

            objective = arguments.get("objective")
            market = arguments.get("market")
            if objective is not None and not isinstance(objective, str):
                raise ValueError("'objective' must be a string")
            if market is not None and not isinstance(market, str):
                raise ValueError("'market' must be a string")
            output_raw = arguments.get("output")
            if output_raw is not None and not isinstance(output_raw, str):
                raise ValueError("'output' must be a path string")
            bundle_payload = await asyncio.to_thread(
                build_intelligence_bundle,
                _path_arg(arguments, "pack_dir"),
                objective=objective,
                market=market,
                search_queries=_string_list_arg(arguments, "search_queries") or None,
                default_search=bool(arguments.get("default_search", True)),
                required_domains=_string_list_arg(arguments, "required_domains"),
                max_excerpts=_coerce_int(arguments.get("max_excerpts"), name="max_excerpts", default=8),
                entity_limit=_coerce_int(arguments.get("entity_limit"), name="entity_limit", default=20),
                search_limit=_coerce_int(arguments.get("search_limit"), name="search_limit", default=10),
                output=Path(output_raw) if output_raw else None,
            )
            result = ToolResult(
                f"Intelligence bundle: {bundle_payload['bundle_id']}",
                data=bundle_payload,
            )

        elif name == "pack_score":
            from ..pack_tools import score_pack

            payload = await asyncio.to_thread(
                score_pack,
                _path_arg(arguments, "pack_dir"),
                required_domains=_string_list_arg(arguments, "required_domains"),
            )
            result = ToolResult(
                f"Pack score: {payload['score']}/100 ({payload['grade']})",
                data=payload,
            )

        elif name == "pack_diff":
            from ..pack_tools import diff_packs

            diff_payload: dict[str, Any] = await asyncio.to_thread(
                diff_packs,
                _path_arg(arguments, "old_pack_dir"),
                _path_arg(arguments, "new_pack_dir"),
            )
            result = ToolResult(
                "Pack diff: "
                f"+{len(diff_payload['added_urls'])} "
                f"-{len(diff_payload['removed_urls'])} "
                f"~{len(diff_payload['changed_urls'])}",
                data=diff_payload,
            )

        elif name == "refresh_pack":
            from ..local_workflows import refresh_pack

            output_dir_arg = arguments.get("output_dir")
            if output_dir_arg is not None and not isinstance(output_dir_arg, str):
                raise ValueError("'output_dir' must be a path string")
            refresh_payload: dict[str, Any] = await asyncio.to_thread(
                refresh_pack,
                _path_arg(arguments, "pack_dir"),
                output_dir=Path(output_dir_arg) if output_dir_arg else None,
                changed_only=bool(arguments.get("changed_only", False)),
                dry_run=bool(arguments.get("dry_run", False)),
            )
            refresh_summary_raw = refresh_payload.get("summary")
            refresh_summary: dict[str, Any] = (
                refresh_summary_raw if isinstance(refresh_summary_raw, dict) else {}
            )
            result = ToolResult(
                "Refresh pack: "
                f"{refresh_summary.get('changed_count', 0)} changed, "
                f"{refresh_summary.get('failed_count', 0)} failed",
                data=refresh_payload,
            )

        elif name == "audit_pack":
            from ..local_workflows import audit_pack

            fail_under = arguments.get("fail_under")
            if fail_under is not None and not isinstance(fail_under, int | float):
                raise ValueError("'fail_under' must be a number")
            payload = await asyncio.to_thread(
                audit_pack,
                _path_arg(arguments, "pack_dir"),
                required_domains=_string_list_arg(arguments, "required_domains"),
                fail_under=float(fail_under) if fail_under is not None else None,
            )
            result = ToolResult(
                f"Pack audit: {payload['score']}/100 ({payload['grade']})",
                data=payload,
            )

        elif name == "pack_citations":
            from ..pack_tools import build_citation_map

            payload = await asyncio.to_thread(
                build_citation_map,
                _path_arg(arguments, "pack_dir"),
                required_domains=_string_list_arg(arguments, "required_domains"),
            )
            result = ToolResult(
                f"Citation map: {payload['source_count']} sources",
                data=payload,
            )

        elif name == "pack_entities":
            from ..pack_tools import extract_pack_entities

            payload = await asyncio.to_thread(
                extract_pack_entities,
                _path_arg(arguments, "pack_dir"),
                required_domains=_string_list_arg(arguments, "required_domains"),
                limit=_coerce_int(arguments.get("limit"), name="limit", default=100),
            )
            result = ToolResult(
                f"Extracted entities: {payload['entity_count']}",
                data=payload,
            )

        elif name == "pack_search":
            from ..pack_tools import search_pack

            payload = await asyncio.to_thread(
                search_pack,
                _path_arg(arguments, "pack_dir"),
                _require_str(arguments, "query"),
                required_domains=_string_list_arg(arguments, "required_domains"),
                limit=_coerce_int(arguments.get("limit"), name="limit", default=10),
            )
            result = ToolResult(
                f"Pack search: {payload['result_count']} results",
                data=payload,
            )

        elif name == "pack_brief":
            from ..pack_tools import build_research_brief

            brief_objective = arguments.get("objective")
            if brief_objective is not None and not isinstance(brief_objective, str):
                raise ValueError("'objective' must be a string")
            brief_payload: dict[str, Any] = await asyncio.to_thread(
                build_research_brief,
                _path_arg(arguments, "pack_dir"),
                objective=brief_objective,
                required_domains=_string_list_arg(arguments, "required_domains"),
                max_excerpts=_coerce_int(arguments.get("max_excerpts"), name="max_excerpts", default=8),
                entity_limit=_coerce_int(arguments.get("entity_limit"), name="entity_limit", default=20),
            )
            brief_summary_raw = brief_payload.get("summary")
            brief_summary: dict[str, Any] = brief_summary_raw if isinstance(brief_summary_raw, dict) else {}
            key_excerpts = brief_payload.get("key_excerpts")
            result = ToolResult(
                "Research brief: "
                f"{len(key_excerpts) if isinstance(key_excerpts, list) else 0} excerpts from "
                f"{brief_summary.get('source_count', 0)} sources",
                data=brief_payload,
            )

        elif name == "pack_prepare":
            from ..pack_tools import prepare_pack

            prepare_objective = arguments.get("objective")
            if prepare_objective is not None and not isinstance(prepare_objective, str):
                raise ValueError("'objective' must be a string")
            raw_search_queries = arguments.get("search_queries")
            if raw_search_queries is None:
                search_queries = None
            else:
                if not isinstance(raw_search_queries, list) or not all(
                    isinstance(item, str) and item for item in raw_search_queries
                ):
                    raise ValueError("'search_queries' must be a list of non-empty strings")
                search_queries = raw_search_queries
            prepare_payload: dict[str, Any] = await asyncio.to_thread(
                prepare_pack,
                _path_arg(arguments, "pack_dir"),
                objective=prepare_objective,
                search_queries=search_queries,
                default_search=bool(arguments.get("default_search", True)),
                required_domains=_string_list_arg(arguments, "required_domains"),
                max_excerpts=_coerce_int(arguments.get("max_excerpts"), name="max_excerpts", default=8),
                entity_limit=_coerce_int(arguments.get("entity_limit"), name="entity_limit", default=20),
                search_limit=_coerce_int(arguments.get("search_limit"), name="search_limit", default=10),
                graph=bool(arguments.get("graph", True)),
                graph_entity_limit=_coerce_int(
                    arguments.get("graph_entity_limit"),
                    name="graph_entity_limit",
                    default=500,
                ),
                markdown=bool(arguments.get("markdown", True)),
            )
            prepare_summary_raw = prepare_payload.get("summary")
            prepare_summary: dict[str, Any] = (
                prepare_summary_raw if isinstance(prepare_summary_raw, dict) else {}
            )
            result = ToolResult(
                f"Prepared pack: {prepare_summary.get('artifact_count', 0)} artifacts",
                data=prepare_payload,
            )

        elif name == "graph_build":
            from ..graph import build_graph

            graph_payload: dict[str, Any] = await asyncio.to_thread(
                build_graph,
                _path_arg(arguments, "pack_dir"),
                entity_limit=_coerce_int(
                    arguments.get("entity_limit"),
                    name="entity_limit",
                    default=500,
                ),
            )
            graph_summary_raw = graph_payload.get("summary")
            graph_summary: dict[str, Any] = graph_summary_raw if isinstance(graph_summary_raw, dict) else {}
            result = ToolResult(
                "Graph built: "
                f"{graph_summary.get('node_count', 0)} nodes, "
                f"{graph_summary.get('edge_count', 0)} edges",
                data=graph_payload,
            )

        elif name == "graph_status":
            from ..graph import graph_status

            status_payload: dict[str, Any] = await asyncio.to_thread(
                graph_status,
                _path_arg(arguments, "pack_dir"),
            )
            result = ToolResult(
                f"Graph status: {status_payload.get('status')}",
                data=status_payload,
            )

        elif name == "graph_query":
            from ..graph import query_graph

            query_payload: dict[str, Any] = await asyncio.to_thread(
                query_graph,
                _path_arg(arguments, "pack_dir"),
                _require_str(arguments, "query"),
                limit=_coerce_int(arguments.get("limit"), name="limit", default=10),
            )
            result = ToolResult(
                f"Graph query: {query_payload.get('result_count', 0)} results",
                data=query_payload,
            )

        elif name == "graph_neighbors":
            from ..graph import graph_neighbors

            neighbors_payload: dict[str, Any] = await asyncio.to_thread(
                graph_neighbors,
                _path_arg(arguments, "pack_dir"),
                _require_str(arguments, "entity"),
                limit=_coerce_int(arguments.get("limit"), name="limit", default=20),
            )
            result = ToolResult(
                f"Graph neighbors: {neighbors_payload.get('neighbor_count', 0)} results",
                data=neighbors_payload,
            )

        elif name == "graph_refresh":
            from ..graph import refresh_graph

            graph_refresh_payload: dict[str, Any] = await asyncio.to_thread(
                refresh_graph,
                _path_arg(arguments, "pack_dir"),
                entity_limit=_coerce_int(
                    arguments.get("entity_limit"),
                    name="entity_limit",
                    default=500,
                ),
            )
            graph_refresh_summary_raw = graph_refresh_payload.get("summary")
            graph_refresh_summary: dict[str, Any] = (
                graph_refresh_summary_raw if isinstance(graph_refresh_summary_raw, dict) else {}
            )
            result = ToolResult(
                "Graph refreshed: "
                f"+{graph_refresh_summary.get('added_node_count', 0)} nodes, "
                f"-{graph_refresh_summary.get('removed_node_count', 0)} nodes",
                data=graph_refresh_payload,
            )

        elif name == "validate_policy":
            from ..policy import PolicyConfig

            policy_path = _path_arg(arguments, "policy_path")
            policy = PolicyConfig.from_file(policy_path)
            validation_source_policy: dict[str, Any] = policy.to_source_policy_payload(
                source="mcp-validate-policy"
            )
            result = ToolResult(
                f"Policy valid: {policy_path}",
                data={
                    "valid": True,
                    "policy_path": str(policy_path),
                    "source_policy": validation_source_policy,
                    "explain": validation_source_policy["explain"],
                },
            )

        elif name == "export_pack":
            from ..exports import export_pack as export_local_pack

            format_arg = _require_str(arguments, "format")
            skill_name = arguments.get("skill_name")
            if skill_name is not None and not isinstance(skill_name, str):
                raise ValueError("'skill_name' must be a string")
            skill_description = arguments.get("skill_description")
            if skill_description is not None and not isinstance(skill_description, str):
                raise ValueError("'skill_description' must be a string")
            export_result = await asyncio.to_thread(
                export_local_pack,
                _path_arg(arguments, "pack_dir"),
                format=format_arg,
                output=_path_arg(arguments, "output"),
                skill_name=skill_name,
                skill_description=skill_description,
            )
            result = ToolResult(
                f"Exported {export_result.record_count} records as {export_result.format}",
                data={
                    "format": export_result.format,
                    "output_path": str(export_result.output_path),
                    "record_count": export_result.record_count,
                    "artifacts": [str(path) for path in export_result.artifacts],
                },
            )

        elif name == "serve_pack_status":
            from ..pack_reader import load_pack

            served_pack = await asyncio.to_thread(load_pack, _path_arg(arguments, "pack_dir"))
            result = ToolResult(
                f"Pack server status: {len(served_pack.documents)} documents",
                data=served_pack.health_payload(),
            )

        elif name == "add_source":
            add_name = _require_str(arguments, "name")
            add_url = _require_str(arguments, "url")
            description = arguments.get("description")
            if description is not None and not isinstance(description, str):
                raise ValueError("'description' must be a string")
            category = arguments.get("category")
            if category is not None and not isinstance(category, str):
                raise ValueError("'category' must be a string")
            max_pages = arguments.get("max_pages")
            result = add_source(
                add_name,
                add_url,
                description=description,
                category=category,
                max_pages=_coerce_int(max_pages, name="max_pages", default=0) or None,
                force=bool(arguments.get("force", False)),
            )

        elif name == "remove_source":
            rm_name = _require_str(arguments, "name")
            result = remove_source(
                rm_name,
                delete_cache=bool(arguments.get("delete_cache", False)),
            )

        else:
            result = ToolResult(f"Unknown tool: {name}", is_error=True)

    except ValueError as err:
        result = ToolResult(str(err), is_error=True)
    except Exception as err:  # noqa: BLE001
        logger.exception("Tool %s raised", name)
        result = ToolResult(f"Tool error: {err}", is_error=True)
    return result


async def _run_stdio() -> int:
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
    except ImportError:
        print(
            "docpull mcp requires the 'mcp' package. Install with: pip install docpull[mcp]",
            file=sys.stderr,
        )
        return 1

    server: Server = Server(  # type: ignore[no-any-unimported]
        "docpull", instructions=SERVER_INSTRUCTIONS
    )

    @server.list_tools()  # type: ignore[misc,no-untyped-call]
    async def _list_tools() -> list[Tool]:  # type: ignore[no-any-unimported]
        tools = [
            Tool(
                name="fetch_url",
                description=(
                    "Fetch a single HTTPS URL and return clean Markdown. No discovery "
                    "or crawl — the agent-friendly fast path. Returns the page's "
                    "Markdown with source and detected framework in the header. "
                    "Optionally chunk the output with max_tokens. Rejects non-HTTPS "
                    "URLs, localhost, and private IPs. For whole source aliases use "
                    "ensure_docs instead."
                ),
                annotations=ToolAnnotations(
                    title="Fetch one HTTPS page",
                    readOnlyHint=True,
                    openWorldHint=True,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "HTTPS URL to fetch",
                            "pattern": "^https://",
                        },
                        "max_tokens": {
                            "type": "integer",
                            "minimum": 100,
                            "maximum": 200000,
                            "description": "If set, split into chunks of this many tokens",
                        },
                        "remote_documents": {
                            "type": "string",
                            "enum": ["off", "pdf"],
                            "default": "off",
                            "description": "Explicitly allow local parsing of a remote PDF",
                        },
                        "remote_document_backend": {
                            "type": "string",
                            "enum": ["auto", "pypdf", "markitdown", "unstructured"],
                            "default": "auto",
                            "description": "Local parser backend for explicitly enabled remote PDFs",
                        },
                        "remote_document_timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 3600,
                            "default": 60,
                            "description": "Wall-time limit for the isolated PDF parser",
                        },
                        "remote_document_memory_mib": {
                            "type": "integer",
                            "minimum": 64,
                            "default": 1024,
                            "description": "Address-space limit for the isolated PDF parser",
                        },
                    },
                    "required": ["url"],
                },
            ),
            Tool(
                name="render_url",
                description=(
                    "Render one public URL through an explicit optional render runtime "
                    "and write rendered HTML plus rendered_pages.ndjson. Runtimes may require "
                    "agent-browser, Vercel Sandbox auth, or E2B_API_KEY; no captcha solving "
                    "or stealth behavior."
                ),
                annotations=ToolAnnotations(
                    title="Render one URL",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "pattern": "^https://"},
                        "runtime": {
                            "type": "string",
                            "enum": ["local", "vercel", "e2b"],
                            "default": "local",
                            "description": "Render runtime: local agent-browser, Vercel Sandbox, or E2B.",
                        },
                        "output_dir": {"type": "string", "default": "rendered"},
                        "allowed_domains": {"type": "array", "items": {"type": "string"}},
                        "timeout_seconds": {"type": "number", "default": 30},
                        "wait_for": {
                            "type": "string",
                            "enum": ["load", "domcontentloaded", "networkidle"],
                            "default": "load",
                        },
                        "cloud_agent_browser_install": {
                            "type": "string",
                            "enum": ["auto", "skip"],
                            "default": "skip",
                        },
                        "cloud_result_transport": {
                            "type": "string",
                            "enum": ["auto", "stdout", "file"],
                            "default": "auto",
                        },
                        "cloud_max_estimated_cost_usd": {"type": "number"},
                        "budget": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Maximum paid-capable cloud spend. Use 0 to block cloud runtimes.",
                        },
                        "cloud_agent_browser_binary": {
                            "type": "string",
                            "default": "agent-browser",
                        },
                        "template": {
                            "type": "string",
                            "description": "Cloud runtime template name; currently used by runtime=e2b.",
                        },
                    },
                    "required": ["url"],
                },
                outputSchema=_RENDER_URL_OUTPUT_SCHEMA,
            ),
            Tool(
                name="ensure_docs",
                description=(
                    "Fetch Markdown for a named source alias (e.g. 'react', "
                    "'nextjs', or a user-added website). Uses a 7-day cache; "
                    "pass force=true to refresh. "
                    "Optional profile selects fetch behavior: rag (default, "
                    "balanced for retrieval), mirror (full archive), quick "
                    "(fast/shallow), llm (NDJSON chunks). Use list_sources to "
                    "discover aliases first."
                ),
                annotations=ToolAnnotations(
                    title="Fetch a source alias",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=True,
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
                outputSchema=_ENSURE_DOCS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="list_sources",
                description=(
                    "List configured source aliases, optionally "
                    "filtered by category. Use this to discover what ensure_docs "
                    "can fetch."
                ),
                annotations=ToolAnnotations(
                    title="List configured source aliases",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["frontend", "backend", "ai", "database", "user"],
                            "description": "Filter by category",
                        }
                    },
                },
                outputSchema=_LIST_SOURCES_OUTPUT_SCHEMA,
            ),
            Tool(
                name="list_indexed",
                description=(
                    "List web sources that have been fetched to the local Markdown "
                    "directory, with last-fetched age. Sorted alphabetically."
                ),
                annotations=ToolAnnotations(
                    title="List locally cached sources",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={"type": "object", "properties": {}},
                outputSchema=_LIST_INDEXED_OUTPUT_SCHEMA,
            ),
            Tool(
                name="grep_docs",
                description=(
                    "Regex search through fetched web-source Markdown. Results are ranked by "
                    "match density (most matches per file first) and rendered with "
                    "lines of surrounding context. Each result returns the source alias "
                    "as the historical library field plus a path relative to that source, "
                    "so you can feed both "
                    "fields straight into read_doc. Use ensure_docs first."
                ),
                annotations=ToolAnnotations(
                    title="Regex-search cached Markdown",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string", "maxLength": 1000},
                        "library": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                            "description": "Restrict to one source alias (name from list_indexed)",
                        },
                        "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 200},
                        "case_sensitive": {"type": "boolean", "default": False},
                        "context": {
                            "type": "integer",
                            "default": 1,
                            "minimum": 0,
                            "maximum": 3,
                            "description": "Lines of context per match (0 = none)",
                        },
                    },
                    "required": ["pattern"],
                },
                outputSchema=_GREP_DOCS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="read_doc",
                description=(
                    "Read a Markdown file from a fetched source alias, optionally sliced "
                    "by line range. The natural follow-up to grep_docs: pass each "
                    "result's library/source alias and path (path is already relative "
                    "to the source root) to pull more surrounding context."
                ),
                annotations=ToolAnnotations(
                    title="Read a cached Markdown file",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "library": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                        },
                        "path": {
                            "type": "string",
                            "description": "Relative path under the source alias",
                        },
                        "line_start": {"type": "integer", "minimum": 1},
                        "line_end": {"type": "integer", "minimum": 1},
                    },
                    "required": ["library", "path"],
                },
                outputSchema=_READ_DOC_OUTPUT_SCHEMA,
            ),
            Tool(
                name="workflow_run",
                description=(
                    "Run a registered evidence-pack workflow through workflow.request.v1 and return "
                    "workflow.result.v1. Supported workflows are brand, product, styleguide, visual/image, "
                    "screenshot, policy, relationship, dataset, fetch, and crawl. Browser use remains "
                    "explicitly gated."
                ),
                annotations=ToolAnnotations(
                    title="Run an evidence-pack workflow",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "workflow": {
                            "type": "string",
                            "enum": [
                                "brand-pack",
                                "product-pack",
                                "styleguide-pack",
                                "visual-pack",
                                "image-pack",
                                "screenshot-pack",
                                "policy-pack",
                                "relationship-pack",
                                "dataset-pack",
                                "fetch",
                                "crawl",
                            ],
                        },
                        "value": {"type": "string"},
                        "input": {
                            "type": "object",
                            "minProperties": 1,
                            "description": (
                                "Full WorkflowRequest input payload. Use sources for multi-source "
                                "dataset and relationship workflows."
                            ),
                        },
                        "output_dir": {"type": "string"},
                        "policy": {"type": "string"},
                        "options": {"type": "object"},
                    },
                    "required": ["workflow", "output_dir"],
                    "oneOf": [{"required": ["value"]}, {"required": ["input"]}],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="brand_pack",
                description="Build an evidence-backed brand pack through the common workflow protocol.",
                annotations=ToolAnnotations(
                    title="Build a brand pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain_or_url": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "email": {"type": "string"},
                        "name": {"type": "string"},
                        "ticker": {"type": "string"},
                        "download_assets": {"type": "boolean", "default": True},
                        "max_pages": {"type": "integer", "minimum": 1, "default": 6},
                        "policy": {"type": "string"},
                    },
                    "required": ["domain_or_url", "output_dir"],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="product_pack",
                description="Build product and pricing evidence through the common workflow protocol.",
                annotations=ToolAnnotations(
                    title="Build a product pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url_or_domain": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "mode": {"type": "string", "enum": ["page", "site"], "default": "page"},
                        "max_pages": {"type": "integer", "minimum": 1, "default": 8},
                        "policy": {"type": "string"},
                    },
                    "required": ["url_or_domain", "output_dir"],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="styleguide_pack",
                description=(
                    "Build styleguide and design-token evidence through the common workflow protocol."
                ),
                annotations=ToolAnnotations(
                    title="Build a styleguide pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain_or_url": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "render": {"type": "boolean", "default": False},
                        "max_stylesheets": {"type": "integer", "minimum": 1, "default": 12},
                        "policy": {"type": "string"},
                    },
                    "required": ["domain_or_url", "output_dir"],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="image_pack",
                description="Build a bounded visual-asset manifest through the common workflow protocol.",
                annotations=ToolAnnotations(
                    title="Build an image pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url_or_pack": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "download_assets": {"type": "boolean", "default": True},
                        "max_assets": {"type": "integer", "minimum": 1, "default": 40},
                        "policy": {"type": "string"},
                    },
                    "required": ["url_or_pack", "output_dir"],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="screenshot_pack",
                description=(
                    "Capture a screenshot pack through the explicitly trusted local browser gate and common "
                    "workflow protocol."
                ),
                annotations=ToolAnnotations(
                    title="Capture a screenshot pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "pattern": "^https://"},
                        "output_dir": {"type": "string"},
                        "viewport": {"type": "string", "default": "1280x720"},
                        "full_page": {"type": "boolean", "default": False},
                        "wait_for": {
                            "type": "string",
                            "enum": ["load", "domcontentloaded", "networkidle"],
                            "default": "load",
                        },
                        "agent_browser_binary": {"type": "string"},
                        "policy": {"type": "string"},
                    },
                    "required": ["url", "output_dir"],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="policy_pack",
                description=(
                    "Discover policy documents and emit stable clause evidence and textual change "
                    "candidates; "
                    "does not provide legal conclusions."
                ),
                annotations=ToolAnnotations(
                    title="Build a policy evidence pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "domain_or_url": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "max_pages": {"type": "integer", "minimum": 1, "default": 16},
                        "baseline_pack": {"type": "string"},
                        "policy": {"type": "string"},
                    },
                    "required": ["domain_or_url", "output_dir"],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="relationship_pack",
                description=(
                    "Extract cited owned-by, operated-by, acquired-by, franchised-by, and invested-in "
                    "observations. Outputs review candidates and never infers independence from missing "
                    "evidence."
                ),
                annotations=ToolAnnotations(
                    title="Build a relationship evidence pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "sources": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "oneOf": [{"type": "string"}, {"type": "object"}],
                            },
                        },
                        "output_dir": {"type": "string"},
                        "max_pages_per_source": {"type": "integer", "minimum": 1, "default": 4},
                        "policy": {"type": "string"},
                    },
                    "required": ["output_dir"],
                    "oneOf": [{"required": ["source"]}, {"required": ["sources"]}],
                },
                outputSchema=_WORKFLOW_RESULT_OUTPUT_SCHEMA,
            ),
            Tool(
                name="intelligence_bundle",
                description=(
                    "Generate a deterministic intelligence.bundle.v1 tracker import with source snapshots, "
                    "document versions, precise observations, and change candidates."
                ),
                annotations=ToolAnnotations(
                    title="Generate an intelligence bundle",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "objective": {"type": "string"},
                        "market": {"type": "string"},
                        "search_queries": {"type": "array", "items": {"type": "string"}},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                        "max_excerpts": {"type": "integer", "minimum": 1, "default": 8},
                        "entity_limit": {"type": "integer", "minimum": 0, "default": 20},
                        "search_limit": {"type": "integer", "minimum": 1, "default": 10},
                        "output": {"type": "string"},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_INTELLIGENCE_BUNDLE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_score",
                description="Score a docpull context pack for agent-readiness without shelling out.",
                annotations=ToolAnnotations(
                    title="Score a context pack",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_SCORE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_diff",
                description="Diff two docpull context packs by URL and content hashes without shelling out.",
                annotations=ToolAnnotations(
                    title="Diff context packs",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "old_pack_dir": {"type": "string"},
                        "new_pack_dir": {"type": "string"},
                    },
                    "required": ["old_pack_dir", "new_pack_dir"],
                },
                outputSchema=_PACK_DIFF_OUTPUT_SCHEMA,
            ),
            Tool(
                name="refresh_pack",
                description=(
                    "Refresh the URLs from an existing local pack, write a refreshed "
                    "snapshot plus refresh.report.json/md, and return a structured diff. "
                    "Use dry_run=true to plan without network calls."
                ),
                annotations=ToolAnnotations(
                    title="Refresh a local pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=False,
                    openWorldHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "output_dir": {"type": "string"},
                        "changed_only": {"type": "boolean", "default": False},
                        "dry_run": {"type": "boolean", "default": False},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_REFRESH_PACK_OUTPUT_SCHEMA,
            ),
            Tool(
                name="audit_pack",
                description="Write pack.audit.json and PACK_AUDIT.md with deterministic quality dimensions.",
                annotations=ToolAnnotations(
                    title="Audit a local pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                        "fail_under": {"type": "number"},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_AUDIT_PACK_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_citations",
                description="Build a stable citation/source map for a docpull context pack.",
                annotations=ToolAnnotations(
                    title="Build pack citations",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_CITATIONS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_entities",
                description=(
                    "Extract cited entities and structured signals from a docpull context pack locally."
                ),
                annotations=ToolAnnotations(
                    title="Extract pack entities",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 100},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_ENTITIES_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_search",
                description="Search a docpull context pack locally and return cited excerpts.",
                annotations=ToolAnnotations(
                    title="Search a context pack",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 10},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir", "query"],
                },
                outputSchema=_PACK_SEARCH_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_brief",
                description=(
                    "Generate a cited local research brief from a docpull context pack, "
                    "including source load order, key excerpts, and structured signals."
                ),
                annotations=ToolAnnotations(
                    title="Build pack research brief",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "objective": {"type": "string"},
                        "max_excerpts": {"type": "integer", "minimum": 1, "default": 8},
                        "entity_limit": {"type": "integer", "minimum": 0, "default": 20},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_BRIEF_OUTPUT_SCHEMA,
            ),
            Tool(
                name="pack_prepare",
                description=(
                    "Write the standard local pack intelligence bundle: pack score, "
                    "source scores, citations, entities, local search results, and a "
                    "cited research brief."
                ),
                annotations=ToolAnnotations(
                    title="Prepare pack artifacts",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "objective": {"type": "string"},
                        "search_queries": {"type": "array", "items": {"type": "string"}},
                        "default_search": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Use the objective as a local search query when search_queries is omitted"
                            ),
                        },
                        "max_excerpts": {"type": "integer", "minimum": 1, "default": 8},
                        "entity_limit": {"type": "integer", "minimum": 0, "default": 20},
                        "search_limit": {"type": "integer", "minimum": 1, "default": 10},
                        "graph": {
                            "type": "boolean",
                            "default": True,
                            "description": "Also build local graph sidecars as part of prepare",
                        },
                        "graph_entity_limit": {"type": "integer", "minimum": 1, "default": 500},
                        "required_domains": {"type": "array", "items": {"type": "string"}},
                        "markdown": {
                            "type": "boolean",
                            "default": True,
                            "description": (
                                "Also write Markdown sidecars such as SEARCH.md and RESEARCH_BRIEF.md"
                            ),
                        },
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_PACK_PREPARE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="graph_build",
                description=(
                    "Build local cited source graph sidecars for a DocPull pack: "
                    "graph.json, graph.nodes.ndjson, graph.edges.ndjson, and GRAPH.md."
                ),
                annotations=ToolAnnotations(
                    title="Build pack source graph",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "entity_limit": {"type": "integer", "minimum": 1, "default": 500},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_GRAPH_BUILD_OUTPUT_SCHEMA,
            ),
            Tool(
                name="graph_status",
                description="Report whether local graph artifacts are missing, current, or stale.",
                annotations=ToolAnnotations(
                    title="Check graph freshness",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_GRAPH_STATUS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="graph_query",
                description="Search graph nodes and cited graph edge evidence without generating an answer.",
                annotations=ToolAnnotations(
                    title="Query source graph",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 10},
                    },
                    "required": ["pack_dir", "query"],
                },
                outputSchema=_GRAPH_QUERY_OUTPUT_SCHEMA,
            ),
            Tool(
                name="graph_neighbors",
                description="List cited neighboring nodes for matching graph entity nodes.",
                annotations=ToolAnnotations(
                    title="Find graph neighbors",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "entity": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "default": 20},
                    },
                    "required": ["pack_dir", "entity"],
                },
                outputSchema=_GRAPH_NEIGHBORS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="graph_refresh",
                description=(
                    "Rebuild local source graph sidecars from the current pack and write graph.diff.json."
                ),
                annotations=ToolAnnotations(
                    title="Refresh source graph",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "entity_limit": {"type": "integer", "minimum": 1, "default": 500},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_GRAPH_REFRESH_OUTPUT_SCHEMA,
            ),
            Tool(
                name="validate_policy",
                description=(
                    "Validate a local DocPull policy file and return its non-secret source_policy payload."
                ),
                annotations=ToolAnnotations(
                    title="Validate policy",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "policy_path": {"type": "string"},
                    },
                    "required": ["policy_path"],
                },
                outputSchema=_VALIDATE_POLICY_OUTPUT_SCHEMA,
            ),
            Tool(
                name="export_pack",
                description="Export a local pack to agent-safe JSONL or skill/rule formats.",
                annotations=ToolAnnotations(
                    title="Export pack",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                        "format": {
                            "type": "string",
                            "enum": list(EXPORT_FORMATS),
                        },
                        "output": {"type": "string"},
                        "skill_name": {"type": "string"},
                        "skill_description": {"type": "string"},
                    },
                    "required": ["pack_dir", "format", "output"],
                },
                outputSchema=_EXPORT_PACK_OUTPUT_SCHEMA,
            ),
            Tool(
                name="serve_pack_status",
                description=(
                    "Return the same health payload the local pack server exposes at /health, "
                    "without starting a listener."
                ),
                annotations=ToolAnnotations(
                    title="Pack server status",
                    readOnlyHint=True,
                    openWorldHint=False,
                    idempotentHint=True,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "pack_dir": {"type": "string"},
                    },
                    "required": ["pack_dir"],
                },
                outputSchema=_SERVE_PACK_STATUS_OUTPUT_SCHEMA,
            ),
            Tool(
                name="add_source",
                description=(
                    "Add or update a user source alias in the writable "
                    "sources.yaml. Refuses to shadow a builtin alias unless "
                    "force=true. URL is HTTPS-only and validated against the "
                    "same SSRF rules as fetch_url. Use list_sources to confirm "
                    "the change."
                ),
                annotations=ToolAnnotations(
                    title="Add or update a user source",
                    readOnlyHint=False,
                    destructiveHint=False,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                            "description": "Alias name (alnum + _ . -)",
                        },
                        "url": {
                            "type": "string",
                            "pattern": "^https://",
                            "description": "HTTPS URL to crawl",
                        },
                        "description": {"type": "string", "maxLength": 500},
                        "category": {
                            "type": "string",
                            "enum": ["frontend", "backend", "ai", "database", "user"],
                        },
                        "max_pages": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 100000,
                        },
                        "force": {
                            "type": "boolean",
                            "default": False,
                            "description": "Override a builtin alias of the same name",
                        },
                    },
                    "required": ["name", "url"],
                },
                outputSchema=_ADD_SOURCE_OUTPUT_SCHEMA,
            ),
            Tool(
                name="remove_source",
                description=(
                    "Remove a user source alias. Optionally delete its cached "
                    "Markdown cache (delete_cache=true). Cannot remove a builtin source — "
                    "to stop using one, just don't call ensure_docs on it."
                ),
                annotations=ToolAnnotations(
                    title="Remove a user source",
                    readOnlyHint=False,
                    destructiveHint=True,
                    idempotentHint=True,
                    openWorldHint=False,
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "pattern": "^[a-zA-Z0-9_.-]+$",
                            "maxLength": 128,
                        },
                        "delete_cache": {
                            "type": "boolean",
                            "default": False,
                            "description": "Also delete the cached Markdown directory",
                        },
                    },
                    "required": ["name"],
                },
                outputSchema=_REMOVE_SOURCE_OUTPUT_SCHEMA,
            ),
        ]
        return [tool for tool in tools if tool.name not in PRUNED_MCP_TOOLS]

    async def _make_progress_callback() -> Any:
        """Return ``(pages_done, total_or_none) -> awaitable`` bound to the
        current request's progressToken, or ``None`` if the client did not
        request progress."""
        ctx = server.request_context
        if ctx.meta is None or ctx.meta.progressToken is None:
            return None
        token = ctx.meta.progressToken
        session = ctx.session

        async def _cb(done: int, total: int | None) -> None:
            try:
                await session.send_progress_notification(
                    progress_token=token,
                    progress=float(done),
                    total=float(total) if total is not None else None,
                )
            except Exception:  # noqa: BLE001
                logger.debug("progress notification failed", exc_info=True)

        return _cb

    @server.call_tool()  # type: ignore[misc,no-untyped-call]
    async def _call_tool(  # type: ignore[no-any-unimported]
        name: str, arguments: dict[str, Any]
    ) -> CallToolResult:
        result = await _dispatch_tool(
            name,
            arguments,
            progress_callback_factory=_make_progress_callback,
        )

        # Return CallToolResult directly so:
        # (a) ``is_error`` propagates (the SDK's tuple/list paths hardcode
        #     isError=False), and
        # (b) errors on tools with an outputSchema don't fail the validator
        #     for "missing structured content."
        # `content` is typed `list[TextContent | ImageContent | ...]` on the SDK
        # side; list invariance means we have to widen the local annotation
        # explicitly even though TextContent is one of the valid variants.
        content: list[Any] = [TextContent(type="text", text=result.text)]
        return CallToolResult(
            content=content,
            structuredContent=result.data if not result.is_error else None,
            isError=result.is_error,
        )

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
