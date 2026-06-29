"""Unified local-first search-pack workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from ..accounting import (
    RunAccounting,
    blocked_action,
    budget_block_payload,
    default_route_steps,
    effective_budget_limit,
    paid_action_blocked,
    write_run_accounting,
)
from ..pack_tools import search_pack as search_local_pack
from ..policy import PolicyConfig
from ..time_utils import utc_now_iso
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    ContextPackError,
    append_ndjson,
    artifact_ref,
    public_url,
    quote_markdown,
    write_json,
)

SEARCH_WORKFLOW = "search-pack"
DEFAULT_SEARCH_OUTPUT_DIR = Path("packs/search")
SearchProvider = Literal["local", "parallel", "tavily", "exa", "context"]


def build_search_pack(
    query: str,
    *,
    provider: SearchProvider = "local",
    pack_dir: Path | None = None,
    output_dir: Path = DEFAULT_SEARCH_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    max_results: int = 10,
    scrape: bool = False,
    dry_run: bool = False,
    budget: float | None = None,
    max_estimated_cost: float | None = None,
) -> dict[str, Any]:
    """Build a search-pack from local evidence or an explicit paid provider."""
    if not query.strip():
        raise ContextPackError("search-pack query must be non-empty.")
    if max_results < 1:
        raise ContextPackError("max_results must be at least 1.")
    output_dir = output_dir.resolve()
    policy = policy or PolicyConfig()
    if provider == "local":
        if pack_dir is None:
            raise ContextPackError("local search-pack requires pack_dir.")
        return _build_local_search_pack(
            query,
            pack_dir=pack_dir,
            output_dir=output_dir,
            policy=policy,
            required_domains=include_domains or [],
            max_results=max_results,
            scrape=scrape,
        )
    return _build_provider_search_pack(
        query,
        provider=provider,
        output_dir=output_dir,
        policy=policy,
        include_domains=include_domains or [],
        exclude_domains=exclude_domains or [],
        max_results=max_results,
        scrape=scrape,
        dry_run=dry_run,
        budget=budget,
        max_estimated_cost=max_estimated_cost,
    )


def _build_local_search_pack(
    query: str,
    *,
    pack_dir: Path,
    output_dir: Path,
    policy: PolicyConfig,
    required_domains: list[str],
    max_results: int,
    scrape: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    local = search_local_pack(pack_dir, query, required_domains=required_domains, limit=max_results)
    results = _normalize_local_results(local)
    replay_config = {
        "query": query,
        "provider": "local",
        "pack_dir": str(pack_dir),
        "include_domains": required_domains,
        "max_results": max_results,
        "scrape": scrape,
    }
    result_path = output_dir / "search.result.json"
    results_path = output_dir / "search.results.ndjson"
    markdown_path = output_dir / "SEARCH.md"
    policy_path = output_dir / "source_policy.json"
    pack_path = output_dir / "search.pack.json"
    append_ndjson(results_path, results)
    source_policy = policy.to_source_policy_payload(
        source=SEARCH_WORKFLOW,
        metadata={
            "provider": "local",
            "pack_dir": str(pack_dir),
            "query": query,
            "scrape": scrape,
        },
    )
    write_json(policy_path, source_policy)
    payload = {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": SEARCH_WORKFLOW,
        "provider": "local",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": query,
        "input": {"pack_dir": str(pack_dir), "scrape": scrape},
        "replay_config": replay_config,
        "summary": {
            "result_count": len(results),
            "max_results": max_results,
            "scrape_enabled": scrape,
        },
        "results": results,
        "request_options": {
            "include_domains": required_domains,
            "max_results": max_results,
            "scrape": scrape,
        },
        "artifacts": {
            "result": artifact_ref(output_dir, result_path),
            "results_ndjson": artifact_ref(output_dir, results_path),
            "markdown": artifact_ref(output_dir, markdown_path),
            "source_policy": artifact_ref(output_dir, policy_path),
            "pack_metadata": artifact_ref(output_dir, pack_path),
            "accounting": "run.accounting.json",
        },
    }
    write_json(result_path, payload)
    markdown_path.write_text(_search_markdown(payload), encoding="utf-8")
    payload_artifacts = payload.get("artifacts")
    artifacts = payload_artifacts if isinstance(payload_artifacts, dict) else {}
    write_json(
        pack_path,
        {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "provider": "local",
            "workflow": SEARCH_WORKFLOW,
            "status": "completed",
            "query": query,
            "summary": payload["summary"],
            "request_options": payload["request_options"],
            "replay_config": replay_config,
            "artifacts": {**artifacts, "pack_metadata": artifact_ref(output_dir, pack_path)},
        },
    )
    write_run_accounting(
        output_dir,
        RunAccounting(
            budget_limit_usd=policy.budget.maximum_paid_cost_usd,
            estimated_paid_cost_usd=0.0,
            http_request_count=0,
            cache_hit_count=0,
            route_steps=default_route_steps(),
            command=SEARCH_WORKFLOW,
            metadata={"query": query, "provider": "local"},
        ),
    )
    return payload


def _build_provider_search_pack(
    query: str,
    *,
    provider: SearchProvider,
    output_dir: Path,
    policy: PolicyConfig,
    include_domains: list[str],
    exclude_domains: list[str],
    max_results: int,
    scrape: bool,
    dry_run: bool,
    budget: float | None,
    max_estimated_cost: float | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    estimated = _estimate_provider_search_cost(provider, max_results=max_results, scrape=scrape)
    budget_limit = effective_budget_limit(
        budget,
        max_estimated_cost,
        policy.budget.maximum_paid_cost_usd,
        policy.providers.max_estimated_cost_usd,
    )
    blocked = paid_action_blocked(budget_limit, estimated_cost_usd=estimated)
    accounting = RunAccounting(
        budget_limit_usd=budget_limit,
        estimated_paid_cost_usd=estimated,
        paid_request_count=0 if dry_run or blocked else 1,
        route_steps=default_route_steps(include_provider=True, budget_limit_usd=budget_limit),
        command=SEARCH_WORKFLOW,
        metadata={"query": query, "provider": provider, "max_results": max_results, "scrape": scrape},
    )
    if blocked:
        accounting.blocked_actions.append(
            blocked_action(
                f"{provider}:search-pack",
                budget_limit_usd=budget_limit,
                estimated_cost_usd=estimated,
                provider=provider,
            )
        )
    request_options = {
        "include_domains": include_domains,
        "exclude_domains": exclude_domains,
        "max_results": max_results,
        "scrape": scrape,
    }
    replay_config = {
        "query": query,
        "provider": provider,
        "include_domains": include_domains,
        "exclude_domains": exclude_domains,
        "max_results": max_results,
        "scrape": scrape,
        "dry_run": dry_run,
        "budget": budget,
        "max_estimated_cost": max_estimated_cost,
    }
    if dry_run or blocked:
        result_path = output_dir / "search.result.json"
        results_path = output_dir / "search.results.ndjson"
        markdown_path = output_dir / "SEARCH.md"
        policy_path = output_dir / "source_policy.json"
        pack_path = output_dir / "search.pack.json"
        append_ndjson(results_path, [])
        source_policy = policy.to_source_policy_payload(
            source=SEARCH_WORKFLOW,
            metadata={
                "provider": provider,
                "query": query,
                "include_domains": include_domains,
                "exclude_domains": exclude_domains,
                "scrape": scrape,
            },
        )
        write_json(policy_path, source_policy)
        payload = {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "workflow": SEARCH_WORKFLOW,
            "provider": provider,
            "status": "dry_run" if dry_run and not blocked else "blocked_by_budget",
            "output_dir": str(output_dir),
            "query": query,
            "replay_config": replay_config,
            "summary": {
                "result_count": 0,
                "estimated_cost_usd": estimated,
                "budget_limit_usd": budget_limit,
            },
            "results": [],
            "request_options": request_options,
            "artifacts": {
                "result": artifact_ref(output_dir, result_path),
                "results_ndjson": artifact_ref(output_dir, results_path),
                "markdown": artifact_ref(output_dir, markdown_path),
                "source_policy": artifact_ref(output_dir, policy_path),
                "pack_metadata": artifact_ref(output_dir, pack_path),
                "accounting": "run.accounting.json",
            },
        }
        if blocked:
            payload.update(
                budget_block_payload(
                    f"{provider}:search-pack",
                    budget_limit_usd=budget_limit,
                    estimated_cost_usd=estimated,
                    provider=provider,
                )
            )
        write_json(result_path, payload)
        markdown_path.write_text(_search_markdown(payload), encoding="utf-8")
        write_json(
            pack_path,
            {
                "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
                "generated_at": utc_now_iso(),
                "provider": provider,
                "workflow": SEARCH_WORKFLOW,
                "status": payload["status"],
                "query": query,
                "summary": payload["summary"],
                "request_options": request_options,
                "replay_config": replay_config,
                "artifacts": payload["artifacts"],
            },
        )
        write_run_accounting(output_dir, accounting)
        return payload
    if provider != "parallel":
        write_run_accounting(output_dir, accounting)
        raise ContextPackError(
            f"Provider '{provider}' is not wired for live unified search-pack yet. "
            "Use --dry-run, budget 0, or existing provider context-pack commands."
        )
    write_run_accounting(output_dir, accounting)
    from ..parallel_workflows import DEFAULT_MODE, run_search_pack

    source_policy = {
        "include_domains": include_domains,
        "exclude_domains": exclude_domains,
    }
    pack_path = run_search_pack(
        objective=query,
        queries=[query],
        mode=DEFAULT_MODE,
        output_dir=output_dir,
        source_policy=source_policy,
        fetch_policy=None,
        max_search_results=max_results,
        max_search_chars_total=None,
        excerpt_chars_per_result=None,
        location=None,
        client_model=None,
        estimated_cost_usd=estimated,
    )
    write_run_accounting(output_dir, accounting)
    payload = {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": SEARCH_WORKFLOW,
        "provider": "parallel",
        "status": "completed",
        "output_dir": str(output_dir),
        "query": query,
        "replay_config": replay_config,
        "summary": {
            "result_count": _parallel_result_count(pack_path),
            "estimated_cost_usd": estimated,
            "budget_limit_usd": budget_limit,
        },
        "request_options": request_options,
        "artifacts": {
            "provider_pack": artifact_ref(output_dir, pack_path / "search.pack.json")
            if pack_path.is_dir()
            else str(pack_path),
            "accounting": "run.accounting.json",
        },
    }
    write_json(output_dir / "search.result.json", payload)
    return payload


def _normalize_local_results(local: dict[str, Any]) -> list[dict[str, Any]]:
    raw_results_value = local.get("results")
    raw_results: list[Any] = raw_results_value if isinstance(raw_results_value, list) else []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(raw_results, start=1):
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
                "rank": index,
                "url": public_url(str(item.get("url") or "")),
                "title": item.get("title"),
                "snippet": item.get("snippet") or item.get("excerpt") or item.get("content"),
                "score": item.get("score"),
                "citation_id": item.get("citation_id"),
                "source": "local_pack",
            }
        )
    return output


def _estimate_provider_search_cost(provider: str, *, max_results: int, scrape: bool) -> float:
    if provider == "parallel":
        from ..parallel_workflows import estimate_search_pack_cost

        return estimate_search_pack_cost(max_search_results=max_results)
    base = 0.005
    scrape_extra = 0.001 * max_results if scrape else 0.0
    return round(base + scrape_extra, 6)


def _parallel_result_count(pack_path: Path) -> int:
    metadata = pack_path / "search.pack.json"
    if not metadata.exists():
        return 0
    try:
        payload = json.loads(metadata.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    count = payload.get("item_count") or payload.get("record_count")
    return int(count) if isinstance(count, int) else 0


def _search_markdown(payload: dict[str, Any]) -> str:
    lines = [f"# Search: {payload.get('query')}", ""]
    raw_results = payload.get("results")
    results: list[Any] = raw_results if isinstance(raw_results, list) else []
    if not results:
        lines.append("No local results found.")
    for result in results:
        if isinstance(result, dict):
            lines.append(
                f"- {result.get('rank')}. [{quote_markdown(str(result.get('title') or result.get('url')))}]"
                f"({result.get('url')})"
            )
            if result.get("snippet"):
                lines.append(f"  {quote_markdown(str(result['snippet']))}")
    return "\n".join(lines).rstrip() + "\n"
