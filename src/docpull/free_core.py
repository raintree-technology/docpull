"""Free-core competitor parity command wrappers."""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse, urlunparse

from rich.console import Console
from rich.markup import escape

from .agent_publish import AgentPublishError, publish_agent_docs
from .basis import basis_record, build_pack_basis, write_basis
from .context_aliases import get_context_alias
from .context_packs.brand import build_brand_pack
from .context_packs.common import ContextPackError
from .context_packs.product import build_product_pack
from .context_packs.schema_extract import extract_schema
from .context_packs.search import build_search_pack
from .context_packs.styleguide import build_styleguide_pack
from .context_packs.visuals import build_image_pack, capture_screenshot_pack
from .core.fetcher import Fetcher
from .discovery.contracts import (
    CandidateSourceRecord,
    DiscoveryError,
    records_from_site_scan,
    select_candidate_records,
    write_discovery_pack,
)
from .discovery.filters import normalize_url
from .http.client import AsyncHttpClient
from .http.rate_limiter import PerHostRateLimiter
from .local_workflows import LocalWorkflowError, answer_pack, audit_pack
from .models.config import CrawlConfig, DocpullConfig, OutputConfig, ProfileName, RenderConfig
from .models.document import DocumentRecord
from .models.events import SkipReason
from .models.run import RunIdentity
from .monitor import MonitorError, init_monitor, run_monitor_once
from .pack_tools import (
    DEFAULT_BRIEF_ENTITY_LIMIT,
    PackToolError,
    _artifact_ref,
    build_citation_map,
    prepare_pack,
    score_pack,
)
from .parity import ParityWorkflowError, entities_pack, extract_pack, research_pack
from .policy import PolicyConfig, PolicyError
from .rendering import check_render_backend_availability
from .source_scoring import score_source
from .time_utils import utc_now_iso

FREE_CORE_SCHEMA_VERSION = 1
CRAWL_RENDER_UNAVAILABLE_MESSAGE = (
    "Static crawl can run, but JS-only coverage requires agent-browser. Run: docpull render --check"
)
BINARY_OR_DOWNLOAD_EXTENSIONS = {
    ".7z",
    ".avi",
    ".bin",
    ".bz2",
    ".csv",
    ".dmg",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".iso",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".tar",
    ".tgz",
    ".webm",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}
LOW_VALUE_PATH_SEGMENTS = {
    "account",
    "app",
    "dashboard",
    "login",
    "pricing",
    "sign-in",
    "sign-up",
    "signin",
    "signup",
}
GITHUB_CHROME_SEGMENTS = {
    "about",
    "customer-stories",
    "enterprise",
    "features",
    "help",
    "login",
    "marketplace",
    "notifications",
    "pricing",
    "search",
    "settings",
    "sponsors",
    "topics",
}
LOCALE_SEGMENTS = {
    "ar",
    "bg",
    "bn",
    "ca",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "fa",
    "fi",
    "fr",
    "he",
    "hi",
    "id",
    "it",
    "ja",
    "ko",
    "nl",
    "no",
    "pl",
    "pt",
    "pt-br",
    "ro",
    "ru",
    "sv",
    "th",
    "tr",
    "uk",
    "vi",
    "zh",
    "zh-cn",
    "zh-hans",
    "zh-hant",
    "zh-tw",
}


class FreeCoreError(RuntimeError):
    """User-facing free-core workflow error."""


def run_scrape_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull scrape", "Scrape one URL into a local context pack")
    parser.add_argument("url")
    args = parser.parse_args(argv)
    return _print_result(
        lambda: scrape_url(
            args.url,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Scrape",
    )


def run_batch_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull batch", "Scrape one or more known URLs into one local pack")
    parser.add_argument("urls", nargs="*", help="URLs to fetch")
    parser.add_argument("--input", type=Path, help="Newline, JSON, or NDJSON URL input file")
    args = parser.parse_args(argv)
    return _print_result(
        lambda: batch_scrape(
            args.urls,
            input_path=args.input,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Batch",
    )


def run_map_url_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull map", "Map a URL into local candidate-source artifacts")
    parser.add_argument("url")
    parser.add_argument("--max-per-source", type=int, default=50)
    args = parser.parse_args(argv)
    return _print_result(
        lambda: map_url(
            args.url,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            max_results=args.max_results,
            max_per_source=args.max_per_source,
        ),
        json_output=args.json_output,
        label="Map",
    )


def run_crawl_url_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull crawl", "Deep-crawl a URL into a local context pack")
    parser.add_argument("url")
    parser.add_argument("--select", action="append", dest="selectors", default=[])
    parser.add_argument("--profile", default="docs", choices=["docs", "rag", "mirror", "quick", "llm"])
    parser.add_argument("--max-pages", dest="max_results", type=int, help="Alias for --max-results")
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--mode", choices=["standard", "exhaustive-docs"], default="standard")
    parser.add_argument("--audit-gaps", action="store_true")
    parser.add_argument("--include-locales", action="store_true")
    args = parser.parse_args(argv)
    return _print_result(
        lambda: crawl_url(
            args.url,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            selectors=args.selectors or None,
            max_results=args.max_results or 100,
            max_depth=args.max_depth,
            mode=args.mode,
            render=args.render,
            audit_gaps=args.audit_gaps,
            include_locales=args.include_locales,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Crawl",
    )


def run_search_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull search", "Search a local pack, or explicitly use a configured provider")
    parser.add_argument("query")
    parser.add_argument("--pack-dir", type=Path)
    parser.add_argument("--provider", choices=["local", "tavily", "exa", "parallel"], default="local")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    if args.provider != "local":
        return _provider_search(args.provider, args)
    return _print_result(
        lambda: local_search(
            args.query,
            pack_dir=args.pack_dir,
            limit=args.limit,
            output_dir=args.output_dir,
        ),
        json_output=args.json_output,
        label="Search",
    )


def run_extract_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull extract", "Extract structured local evidence from a URL or pack")
    parser.add_argument("target")
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--preset", choices=["brand", "product", "styleguide"])
    args = parser.parse_args(argv)
    return _print_result(
        lambda: extract_target(
            args.target,
            schema_path=args.schema,
            preset=args.preset,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Extract",
    )


def run_research_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull research", "Research from a local pack or one URL with citations")
    parser.add_argument("target")
    parser.add_argument("--question", required=True)
    parser.add_argument("--schema", type=Path)
    args = parser.parse_args(argv)
    return _print_result(
        lambda: research_target(
            args.target,
            question=args.question,
            schema_path=args.schema,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Research",
    )


def run_answer_top_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull answer", "Answer from a local pack or URL with citations")
    parser.add_argument("target")
    parser.add_argument("--question", required=True)
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)
    return _print_result(
        lambda: answer_target(
            args.target,
            question=args.question,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            limit=args.limit,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Answer",
    )


def run_entities_top_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull entities", "Extract local cited entities from a pack or URL")
    parser.add_argument("target")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    return _print_result(
        lambda: entities_target(
            args.target,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            limit=args.limit,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Entities",
    )


def run_brief_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull brief", "Prepare a local research brief from a pack or URL")
    parser.add_argument("target")
    parser.add_argument("--objective")
    parser.add_argument("--search-query", action="append", dest="search_queries", default=[])
    parser.add_argument("--max-excerpts", type=int, default=8)
    parser.add_argument("--graph-entity-limit", type=int, default=DEFAULT_BRIEF_ENTITY_LIMIT)
    args = parser.parse_args(argv)
    return _print_result(
        lambda: brief_target(
            args.target,
            objective=args.objective,
            search_queries=args.search_queries or None,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            max_excerpts=args.max_excerpts,
            graph_entity_limit=args.graph_entity_limit,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Brief",
    )


def run_images_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull images", "Extract local image evidence from a URL or pack")
    parser.add_argument("target")
    parser.add_argument("--max-assets", type=int, default=40)
    parser.add_argument("--download-assets", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args(argv)
    return _print_result(
        lambda: image_target(
            args.target,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            download_assets=args.download_assets,
            max_assets=args.max_assets,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Images",
    )


def run_screenshot_cli(argv: list[str] | None = None) -> int:
    parser = _base_parser("docpull screenshot", "Capture an explicit local screenshot pack")
    parser.add_argument("url")
    parser.add_argument("--viewport", default="1280x720")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--wait-for", choices=["load", "domcontentloaded", "networkidle"], default="load")
    parser.add_argument("--agent-browser-binary")
    args = parser.parse_args(argv)
    return _print_result(
        lambda: screenshot_url(
            args.url,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            viewport=args.viewport,
            full_page=args.full_page,
            wait_for=args.wait_for,
            agent_browser_binary=args.agent_browser_binary,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        label="Screenshot",
    )


def run_monitor_target_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull monitor", description="Create/run a local monitor")
    parser.add_argument("target")
    parser.add_argument("--name")
    parser.add_argument("--state-dir", type=Path, default=Path(".docpull/monitors"))
    parser.add_argument("--output-dir", "-o", type=Path, default=Path("packs/monitor-source"))
    parser.add_argument("--run", action="store_true", help="Run one monitor cycle after creating it")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _print_result(
        lambda: monitor_target(
            args.target,
            name=args.name,
            state_dir=args.state_dir,
            output_dir=args.output_dir,
            run_once=args.run,
        ),
        json_output=args.json_output,
        label="Monitor",
    )


def scrape_url(
    url: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    dry_run: bool = False,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        url_file = Path(tmp) / "urls.txt"
        url_file.write_text(url + "\n", encoding="utf-8")
        payload = extract_pack(url_file, output_dir=output_dir, policy=policy, dry_run=dry_run)
    if not dry_run:
        _standardize_pack(output_dir, workflow="scrape", objective=f"Scrape {url}")
    return payload


def batch_scrape(
    urls: list[str],
    *,
    input_path: Path | None,
    output_dir: Path,
    policy: PolicyConfig,
    dry_run: bool = False,
) -> dict[str, Any]:
    resolved_urls = _batch_urls(urls, input_path=input_path)
    if not resolved_urls:
        raise FreeCoreError("batch requires at least one URL or --input file")
    with tempfile.TemporaryDirectory() as tmp:
        url_file = Path(tmp) / "urls.txt"
        url_file.write_text("".join(f"{url}\n" for url in resolved_urls), encoding="utf-8")
        payload = extract_pack(url_file, output_dir=output_dir, policy=policy, dry_run=dry_run)
    if not dry_run:
        _standardize_pack(output_dir, workflow="batch", objective=f"Batch scrape {len(resolved_urls)} URLs")
    return {**payload, "input_url_count": len(resolved_urls)}


def map_url(
    url: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    max_results: int | None,
    max_per_source: int = 50,
) -> dict[str, Any]:
    effective_policy = _policy_with_default_domain(policy, url)
    records = asyncio.run(_scan_site(url, policy=effective_policy, max_per_source=max_per_source))
    return _write_map_artifacts(
        url,
        output_dir=output_dir,
        policy=effective_policy,
        records=records,
        max_results=max_results,
    )


def _write_map_artifacts(
    url: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    records: list[CandidateSourceRecord],
    max_results: int | None,
) -> dict[str, Any]:
    allowed_records = _records_allowed_by_policy(records, policy)
    report = write_discovery_pack(
        output_dir,
        allowed_records,
        policy=policy,
        objective=f"Map {url}",
        query=url,
        source="free-core-map",
        max_results=None,
    )
    selected = select_candidate_records(allowed_records, [f"top:{max_results or 25}"])
    _write_selected(output_dir / "selected_sources.ndjson", selected)
    (output_dir / "selected_urls.txt").write_text(
        "".join(f"{record.url}\n" for record in selected),
        encoding="utf-8",
    )
    _write_json(output_dir / "sitegraph.json", _sitegraph(url, allowed_records))
    (output_dir / "MAP.md").write_text(_map_markdown(url, report), encoding="utf-8")
    return {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": "map",
        "url": url,
        "output_dir": str(output_dir.resolve()),
        "summary": {
            "candidate_count": report["candidate_count"],
            "selected_count": len(selected),
        },
        "artifacts": {
            **report["artifacts"],
            "selected_sources": "selected_sources.ndjson",
            "selected_urls": "selected_urls.txt",
            "sitegraph": "sitegraph.json",
            "markdown": "MAP.md",
        },
    }


def crawl_url(
    url: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    selectors: list[str] | None,
    max_results: int,
    max_depth: int = 3,
    mode: str = "standard",
    render: str = "off",
    audit_gaps: bool = False,
    include_locales: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    if mode not in {"standard", "exhaustive-docs"}:
        raise FreeCoreError("crawl mode must be 'standard' or 'exhaustive-docs'")
    if max_results < 1:
        raise FreeCoreError("crawl requires --max-pages/--max-results >= 1")
    if render not in {"off", "fallback", "agent-browser"}:
        raise FreeCoreError("crawl render mode must be off, fallback, or agent-browser")
    if render != "off":
        available, _message = check_render_backend_availability("agent-browser")
        if not available:
            raise FreeCoreError(CRAWL_RENDER_UNAVAILABLE_MESSAGE)

    effective_policy = _policy_with_default_domain(policy, url)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    map_dir = output_dir / "_map"
    acquired = asyncio.run(
        _acquire_crawl_candidates(
            url,
            policy=effective_policy,
            mode=mode,
            max_results=max_results,
            max_depth=max_depth,
            render=render,
        )
    )
    records = acquired["records"]
    map_payload = _write_map_artifacts(
        url,
        output_dir=map_dir,
        policy=effective_policy,
        records=records,
        max_results=max_results,
    )
    selection = _select_crawl_records(
        url,
        records,
        policy=effective_policy,
        selectors=selectors or [f"top:{max_results}"],
        max_results=max_results,
        include_locales=include_locales,
    )
    selected = selection["selected"]
    _write_selected(output_dir / "selected_sources.ndjson", selected)
    (output_dir / "selected_urls.txt").write_text(
        "".join(f"{record.url}\n" for record in selected),
        encoding="utf-8",
    )

    fetched = (
        {
            "record_count": 0,
            "records": [],
            "source_entries": [],
            "errors": [],
            "skips": [],
            "fetched_urls": [],
        }
        if dry_run
        else asyncio.run(
            _fetch_crawl_selection_to_pack(
                [record.url for record in selected],
                output_dir,
                policy=effective_policy,
                render=render,
            )
        )
    )
    if not dry_run:
        _standardize_pack(output_dir, workflow="crawl", objective=f"Crawl {url}")

    acquisition_payload = _build_acquisition_routes(
        acquired["routes"],
        selected=selected,
        fetched=fetched,
        url_routes=acquired["url_routes"],
    )
    coverage = _build_coverage_report(
        url,
        mode=mode,
        render=render,
        discovered_records=records,
        selected=selected,
        fetched=fetched,
        prefetch_skips=selection["skips"],
        acquisition=acquisition_payload,
        dry_run=dry_run,
    )
    _write_json(output_dir / "acquisition.routes.json", acquisition_payload)
    _write_json(output_dir / "coverage.report.json", coverage)
    (output_dir / "COVERAGE_REPORT.md").write_text(_coverage_markdown(coverage), encoding="utf-8")
    if not dry_run:
        _augment_quality_artifacts_for_coverage(output_dir, coverage, url=url)

    payload = _crawl_result_payload(
        url,
        output_dir=output_dir,
        mode=mode,
        render=render,
        dry_run=dry_run,
        map_payload=map_payload,
        selected=selected,
        fetched=fetched,
        coverage=coverage,
        acquisition=acquisition_payload,
    )
    gap_reasons = _audit_gap_reasons(coverage) if audit_gaps else []
    if gap_reasons:
        payload["status"] = "completed_with_gaps"
        payload["exit_code"] = 2
        payload["audit_gaps"] = gap_reasons
    _write_json(output_dir / "crawl.result.json", payload)
    (output_dir / "CRAWL_REPORT.md").write_text(_crawl_report_markdown(payload), encoding="utf-8")
    _write_json(output_dir / "crawl.plan.json", {"map": map_payload, "crawl": payload})
    return payload


async def _acquire_crawl_candidates(
    url: str,
    *,
    policy: PolicyConfig,
    mode: str,
    max_results: int,
    max_depth: int,
    render: str,
) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    records_by_url: dict[str, CandidateSourceRecord] = {}
    url_routes: dict[str, list[str]] = {}

    def add_route(
        route: str,
        records: list[CandidateSourceRecord],
        *,
        status: str = "completed",
        error: str | None = None,
        fallback_reason: str | None = None,
    ) -> None:
        routes.append(
            {
                "schema_version": FREE_CORE_SCHEMA_VERSION,
                "route": route,
                "status": status,
                "discovered_count": len(records),
                "selected_count": 0,
                "fetched_count": 0,
                "extracted_count": 0,
                "skip_counts": {},
                "failure_count": 0,
                "fallback_reason": fallback_reason,
                "error": error,
                "sample_urls": [record.url for record in records[:10]],
            }
        )
        for record in records:
            key = normalize_url(record.url)
            records_by_url.setdefault(key, record)
            route_names = url_routes.setdefault(key, [])
            if route not in route_names:
                route_names.append(route)

    async def run_route(route: str, action: Any, *, fallback_reason: str | None = None) -> int:
        try:
            records = await action()
        except Exception as err:  # noqa: BLE001
            add_route(route, [], status="failed", error=str(err), fallback_reason=fallback_reason)
            return 0
        add_route(route, records, fallback_reason=fallback_reason)
        return len(records)

    if mode == "standard":
        core_count = await run_route(
            "core_crawl",
            lambda: _discover_core_records(
                url,
                policy=policy,
                max_results=max_results,
                max_depth=max_depth,
                render=render,
            ),
        )
        if core_count <= 1:
            await run_route(
                "single_scrape",
                lambda: _single_scrape_records(url, policy=policy),
                fallback_reason="core_crawl_shallow",
            )
            await run_route(
                "docs_nav",
                lambda: _scan_site_source_records(
                    url,
                    policy=policy,
                    source="links",
                    max_results=max(max_results, 50),
                ),
                fallback_reason="core_crawl_shallow",
            )
    else:
        for route, source in (
            ("llms_txt", "llms"),
            ("sitemaps", "sitemaps"),
            ("docs_nav", "links"),
            ("openapi_refs", "openapi"),
            ("github_docs_tree", "github"),
        ):
            await run_route(
                route,
                lambda source=source: _scan_site_source_records(
                    url,
                    policy=policy,
                    source=source,
                    max_results=max(max_results, 50),
                ),
                fallback_reason="exhaustive-docs",
            )
        await run_route(
            "core_crawl",
            lambda: _discover_core_records(
                url,
                policy=policy,
                max_results=max(max_results * 3, max_results),
                max_depth=max_depth,
                render=render,
            ),
            fallback_reason="exhaustive-docs",
        )
        await run_route(
            "markdown_alternates",
            lambda: _markdown_alternate_records(
                list(records_by_url.values()),
                policy=policy,
                max_results=max(max_results, 50),
            ),
            fallback_reason="exhaustive-docs",
        )
        await run_route(
            "single_scrape",
            lambda: _single_scrape_records(url, policy=policy),
            fallback_reason="exhaustive-docs",
        )

    return {
        "records": list(records_by_url.values()),
        "routes": routes,
        "url_routes": url_routes,
    }


async def _discover_core_records(
    url: str,
    *,
    policy: PolicyConfig,
    max_results: int,
    max_depth: int,
    render: str,
) -> list[CandidateSourceRecord]:
    config = _crawl_fetcher_config(
        url,
        output_dir=Path(tempfile.gettempdir()) / "docpull-crawl-discovery",
        max_pages=max_results,
        max_depth=max_depth,
        render=render,
    )
    async with Fetcher(config) as fetcher:
        urls = await fetcher.discover()
    return _candidate_records_from_urls(
        urls,
        route="core-crawl",
        expected_domains=policy.allowed_domains,
    )


async def _scan_site_source_records(
    url: str,
    *,
    policy: PolicyConfig,
    source: str,
    max_results: int,
) -> list[CandidateSourceRecord]:
    async with AsyncHttpClient(
        rate_limiter=PerHostRateLimiter(default_delay=0.2, default_concurrent=2),
        max_retries=1,
        log_retry_warnings=False,
    ) as client:
        return await records_from_site_scan(
            url,
            client=client,
            sources=[source],
            expected_domains=policy.allowed_domains,
            max_results_per_source=max_results,
        )


async def _single_scrape_records(url: str, *, policy: PolicyConfig) -> list[CandidateSourceRecord]:
    return _candidate_records_from_urls(
        [url],
        route="single-scrape",
        expected_domains=policy.allowed_domains,
    )


async def _markdown_alternate_records(
    records: list[CandidateSourceRecord],
    *,
    policy: PolicyConfig,
    max_results: int,
) -> list[CandidateSourceRecord]:
    alternates = _markdown_alternate_urls([record.url for record in records])
    return _candidate_records_from_urls(
        alternates[:max_results],
        route="markdown-alternate",
        expected_domains=policy.allowed_domains,
    )


def _candidate_records_from_urls(
    urls: list[str],
    *,
    route: str,
    expected_domains: list[str],
) -> list[CandidateSourceRecord]:
    records: list[CandidateSourceRecord] = []
    seen: set[str] = set()
    for index, url in enumerate(urls, start=1):
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        score = score_source(url=url, expected_domains=expected_domains)
        records.append(
            CandidateSourceRecord(
                url=url,
                source=route,
                provider="local",
                score=float(score["score"]),
                rank=index,
                metadata={
                    "local_score": score["score"],
                    "score_grade": score["grade"],
                    "score_reasons": score["reasons"],
                },
            )
        )
    return records


def _markdown_alternate_urls(urls: list[str]) -> list[str]:
    alternates: list[str] = []
    seen: set[str] = set()
    for url in urls:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        path = parsed.path or "/"
        suffix = Path(path).suffix.lower()
        candidates: list[str] = []
        if suffix in {".md", ".markdown", ".txt"} or suffix in BINARY_OR_DOWNLOAD_EXTENSIONS:
            continue
        if suffix in {".html", ".htm"}:
            candidates.append(path[: -len(suffix)] + ".md")
        elif path.endswith("/"):
            candidates.append(path.rstrip("/") + ".md")
            candidates.append(path.rstrip("/") + "/index.md")
        elif "." not in Path(path).name:
            candidates.append(path + ".md")
            candidates.append(path.rstrip("/") + "/index.md")
        for candidate_path in candidates:
            alt = urlunparse((parsed.scheme, parsed.netloc, candidate_path, "", "", ""))
            key = normalize_url(alt)
            if key in seen:
                continue
            seen.add(key)
            alternates.append(alt)
    return alternates


def _select_crawl_records(
    start_url: str,
    records: list[CandidateSourceRecord],
    *,
    policy: PolicyConfig,
    selectors: list[str],
    max_results: int,
    include_locales: bool,
) -> dict[str, Any]:
    clean: list[CandidateSourceRecord] = []
    skips: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    locale_canonicals: set[str] = set()
    ordered = sorted(
        records,
        key=lambda record: (
            _has_locale_segment(record.url),
            -(record.score if record.score is not None else 0),
            record.url,
        ),
    )
    for record in ordered:
        key = normalize_url(record.url)
        if key in seen_urls:
            skips.append({"url": record.url, "reason": "duplicate"})
            continue
        seen_urls.add(key)
        allowed, reason = policy.allows_url(record.url)
        if not allowed:
            skips.append({"url": record.url, "reason": reason or "policy_denied"})
            continue
        hygiene_reason = _hygiene_skip_reason(
            start_url,
            record.url,
            include_locales=include_locales,
            locale_canonicals=locale_canonicals,
        )
        if hygiene_reason:
            skips.append({"url": record.url, "reason": hygiene_reason})
            continue
        clean.append(record)
    selected = select_candidate_records(clean, selectors)[:max_results]
    return {"selected": selected, "skips": skips, "candidate_count": len(clean)}


def _hygiene_skip_reason(
    start_url: str,
    url: str,
    *,
    include_locales: bool,
    locale_canonicals: set[str],
) -> str | None:
    parsed = urlparse(url)
    start = urlparse(start_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "invalid_url"
    normalized_url = normalize_url(url)
    normalized_start = normalize_url(start_url)
    path = parsed.path or "/"
    path_segments = [segment.lower() for segment in path.split("/") if segment]
    suffix = Path(path).suffix.lower()
    if suffix in BINARY_OR_DOWNLOAD_EXTENSIONS or any(
        segment in {"download", "downloads"} for segment in path_segments
    ):
        return "binary_or_download"
    if normalized_url != normalized_start and any(
        segment in LOW_VALUE_PATH_SEGMENTS for segment in path_segments
    ):
        return "low_value_path"
    if parsed.netloc.lower() == "github.com":
        github_reason = _github_hygiene_reason(start, parsed, normalized_url == normalized_start)
        if github_reason:
            return github_reason
    canonical = _locale_canonical_key(url)
    if include_locales:
        return None
    if canonical in locale_canonicals and _has_locale_segment(url):
        return "localized_duplicate"
    locale_canonicals.add(canonical)
    return None


def _github_hygiene_reason(start: Any, parsed: Any, is_start_url: bool) -> str | None:
    if is_start_url:
        return None
    path_segments = [segment.lower() for segment in (parsed.path or "/").split("/") if segment]
    if path_segments and path_segments[0] in GITHUB_CHROME_SEGMENTS:
        return "github_chrome"
    start_segments = [segment.lower() for segment in (start.path or "/").split("/") if segment]
    if len(start_segments) >= 2 and len(path_segments) >= 2 and path_segments[:2] != start_segments[:2]:
        return "github_repo_drift"
    return None


def _has_locale_segment(url: str) -> bool:
    return any(segment.lower() in LOCALE_SEGMENTS for segment in urlparse(url).path.split("/") if segment)


def _locale_canonical_key(url: str) -> str:
    parsed = urlparse(url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    canonical_segments = [segment for segment in segments if segment.lower() not in LOCALE_SEGMENTS]
    canonical_path = "/" + "/".join(canonical_segments)
    if parsed.path.endswith("/") and not canonical_path.endswith("/"):
        canonical_path += "/"
    return normalize_url(
        urlunparse((parsed.scheme, parsed.netloc, canonical_path or "/", "", parsed.query, ""))
    )


async def _fetch_crawl_selection_to_pack(
    urls: list[str],
    output_dir: Path,
    *,
    policy: PolicyConfig,
    render: str,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[DocumentRecord] = []
    source_entries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    fetched_urls: list[str] = []
    if not urls:
        _write_crawl_local_pack(output_dir, records, source_entries, policy=policy)
        return {
            "record_count": 0,
            "records": records,
            "source_entries": source_entries,
            "errors": errors,
            "skips": skips,
            "fetched_urls": fetched_urls,
        }

    run_identity = RunIdentity.from_config(DocpullConfig(url=urls[0], profile=ProfileName.CUSTOM))
    config = _crawl_fetcher_config(
        urls[0],
        output_dir=output_dir,
        max_pages=len(urls),
        max_depth=1,
        render=render,
    )
    async with Fetcher(config) as fetcher:
        for url in urls:
            allowed, reason = policy.allows_url(url)
            if not allowed:
                skips.append({"url": url, "reason": reason or "policy_denied"})
                continue
            fetched_urls.append(url)
            ctx = await fetcher.fetch_one(url, save=False)
            if ctx.error:
                errors.append({"url": url, "error": ctx.error})
                continue
            if ctx.should_skip:
                skips.append(
                    {
                        "url": url,
                        "reason": _skip_reason_value(ctx.skip_code or ctx.skip_reason),
                        "message": ctx.skip_reason,
                        "status_code": ctx.status_code,
                        "content_type": ctx.content_type,
                    }
                )
                continue
            content = str(ctx.markdown or "")
            if not content.strip():
                skips.append({"url": url, "reason": "empty_content", "status_code": ctx.status_code})
                continue
            record = DocumentRecord.from_page(
                url=url,
                title=ctx.title,
                content=content,
                metadata=ctx.metadata,
                extraction=ctx.extraction_info,
                source_type=ctx.source_type or "crawl",
                run_identity=run_identity,
            )
            records.append(record)
            source_path = output_dir / "sources" / f"{len(records):03d}.md"
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(content, encoding="utf-8")
            source_entries.append(
                {
                    "index": len(source_entries) + 1,
                    "url": url,
                    "title": ctx.title or url,
                    "path": _artifact_ref(output_dir, source_path),
                }
            )

    _write_crawl_local_pack(output_dir, records, source_entries, policy=policy)
    return {
        "record_count": len(records),
        "records": records,
        "source_entries": source_entries,
        "errors": errors,
        "skips": skips,
        "fetched_urls": fetched_urls,
    }


def _crawl_fetcher_config(
    url: str,
    *,
    output_dir: Path,
    max_pages: int,
    max_depth: int,
    render: str,
) -> DocpullConfig:
    render_mode = cast(Literal["off", "agent-browser", "fallback"], render)
    return DocpullConfig(
        url=url,
        profile=ProfileName.CUSTOM,
        crawl=CrawlConfig(
            max_pages=max_pages,
            max_depth=max(max_depth, 1),
            max_concurrent=6,
            per_host_concurrent=2,
            rate_limit=0.2,
            streaming_discovery=False,
        ),
        output=OutputConfig(
            directory=output_dir,
            format="ndjson",
            ndjson_filename="documents.ndjson",
            naming_strategy="hierarchical",
        ),
        render=RenderConfig(mode=render_mode),
    )


def _write_crawl_local_pack(
    output_dir: Path,
    records: list[DocumentRecord],
    sources: list[dict[str, Any]],
    *,
    policy: PolicyConfig,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "documents.ndjson").write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )
    _write_json(
        output_dir / "corpus.manifest.json",
        {
            "schema_version": FREE_CORE_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "output_format": "ndjson",
            "document_count": len({record.document_id for record in records}),
            "record_count": len(records),
            "records": [
                {
                    "document_id": record.document_id,
                    "url": record.url,
                    "title": record.title,
                    "content_hash": record.content_hash,
                    "source_type": record.source_type,
                    "output_path": sources[index]["path"] if index < len(sources) else None,
                }
                for index, record in enumerate(records)
            ],
        },
    )
    source_policy = policy.to_source_policy_payload(
        source="crawl",
        url=records[0].url if records else None,
        metadata={"workflow": "crawl", "record_count": len(records)},
    )
    _write_json(output_dir / "source_policy.json", source_policy)
    _write_json(
        output_dir / "local.pack.json",
        {
            "schema_version": FREE_CORE_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "provider": "local",
            "workflow": "crawl",
            "request_options": {"source_policy": source_policy},
            "record_count": len(records),
            "sources": sources,
            "artifacts": {
                "documents_ndjson": "documents.ndjson",
                "corpus_manifest": "corpus.manifest.json",
                "sources": "sources.md",
                "source_policy": "source_policy.json",
                "coverage_report": "coverage.report.json",
                "acquisition_routes": "acquisition.routes.json",
            },
        },
    )
    (output_dir / "sources.md").write_text(_sources_markdown(sources), encoding="utf-8")


def _sources_markdown(sources: list[dict[str, Any]]) -> str:
    lines = ["# Sources", ""]
    if not sources:
        lines.append("_No sources were extracted._")
    for source in sources:
        title = source.get("title") or source.get("url")
        lines.append(f"- {source.get('index')}. [{title}]({source.get('url')})")
    return "\n".join(lines).rstrip() + "\n"


def _build_acquisition_routes(
    routes: list[dict[str, Any]],
    *,
    selected: list[CandidateSourceRecord],
    fetched: dict[str, Any],
    url_routes: dict[str, list[str]],
) -> dict[str, Any]:
    route_payloads = [{**route, "skip_counts": dict(route.get("skip_counts") or {})} for route in routes]
    by_name = {str(route["route"]): route for route in route_payloads}

    def route_names_for_url(url: str) -> list[str]:
        return url_routes.get(normalize_url(url)) or []

    for record in selected:
        for route_name in route_names_for_url(record.url):
            if route_name in by_name:
                by_name[route_name]["selected_count"] += 1
    for url in fetched.get("fetched_urls", []):
        for route_name in route_names_for_url(str(url)):
            if route_name in by_name:
                by_name[route_name]["fetched_count"] += 1
    for record in fetched.get("records", []):
        for route_name in route_names_for_url(str(record.url)):
            if route_name in by_name:
                by_name[route_name]["extracted_count"] += 1
    for skip in fetched.get("skips", []):
        reason = str(skip.get("reason") or "skipped")
        for route_name in route_names_for_url(str(skip.get("url") or "")):
            if route_name in by_name:
                counts = by_name[route_name].setdefault("skip_counts", {})
                counts[reason] = int(counts.get(reason, 0)) + 1
    for error in fetched.get("errors", []):
        for route_name in route_names_for_url(str(error.get("url") or "")):
            if route_name in by_name:
                by_name[route_name]["failure_count"] += 1

    return {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "routes": route_payloads,
    }


def _build_coverage_report(
    url: str,
    *,
    mode: str,
    render: str,
    discovered_records: list[CandidateSourceRecord],
    selected: list[CandidateSourceRecord],
    fetched: dict[str, Any],
    prefetch_skips: list[dict[str, Any]],
    acquisition: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    fetched_skips = [dict(item) for item in fetched.get("skips", [])]
    errors = [dict(item) for item in fetched.get("errors", [])]
    all_skips = [*prefetch_skips, *fetched_skips]
    skip_counts = Counter(str(item.get("reason") or "skipped") for item in all_skips)
    js_only = sum(
        1
        for item in all_skips
        if str(item.get("reason") or "") == SkipReason.JS_ONLY_SPA.value
        or "js-only" in str(item.get("message") or item.get("reason") or "").lower()
    )
    robots = sum(
        1 for item in all_skips if str(item.get("reason") or "") == SkipReason.ROBOTS_DISALLOWED.value
    )
    auth_blocked = sum(1 for item in fetched_skips if item.get("status_code") in {401, 403})
    cloudflare = sum(
        1
        for item in fetched_skips
        if "cloudflare" in str(item.get("reason") or "").lower()
        or "cloudflare" in str(item.get("message") or "").lower()
        or "cloudflare" in str(item.get("error") or "").lower()
    )
    binary_pdf = sum(
        1
        for item in all_skips
        if str(item.get("reason") or "") in {"binary_or_download", SkipReason.INVALID_CONTENT_TYPE.value}
        or Path(urlparse(str(item.get("url") or "")).path).suffix.lower() == ".pdf"
    )
    summary: dict[str, Any] = {
        "discovered_url_count": len({normalize_url(record.url) for record in discovered_records}),
        "selected_url_count": len(selected),
        "fetched_url_count": len(fetched.get("fetched_urls", [])),
        "extracted_doc_count": int(fetched.get("record_count", 0)),
        "skipped_js_only": js_only,
        "blocked_by_robots": robots,
        "blocked_by_auth": auth_blocked,
        "blocked_by_cloudflare": cloudflare,
        "blocked_by_cloudflare_or_auth": max(auth_blocked, cloudflare),
        "binary_pdf_skipped": binary_pdf,
        "nonzero_failures": len(errors),
        "prefetch_skipped_count": len(prefetch_skips),
        "fetch_skipped_count": len(fetched_skips),
        "dry_run": dry_run,
    }
    summary["coverage_confidence"] = _coverage_confidence(summary, render=render)
    return {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "url": url,
        "mode": mode,
        "render": render,
        "summary": summary,
        "skip_counts": dict(skip_counts),
        "skips": all_skips[:500],
        "errors": errors[:500],
        "routes": acquisition.get("routes", []),
        "recommendations": _coverage_recommendations(summary, render=render),
    }


def _coverage_confidence(summary: dict[str, Any], *, render: str) -> str:
    discovered = int(summary.get("discovered_url_count") or 0)
    selected = int(summary.get("selected_url_count") or 0)
    extracted = int(summary.get("extracted_doc_count") or 0)
    failures = int(summary.get("nonzero_failures") or 0)
    fetch_skips = int(summary.get("fetch_skipped_count") or 0)
    js_only = int(summary.get("skipped_js_only") or 0)
    if selected == 0 or extracted == 0:
        return "low"
    if extracted <= 1 and discovered > 3:
        return "low"
    if selected >= 5 and extracted / selected < 0.4:
        return "low"
    if failures >= max(2, selected // 2):
        return "low"
    if selected >= 5 and (fetch_skips + failures) / selected >= 0.35:
        return "medium"
    if js_only and render == "off":
        return "medium"
    if discovered > selected * 3 and selected < 10:
        return "medium"
    return "high"


def _coverage_recommendations(summary: dict[str, Any], *, render: str) -> list[str]:
    recommendations: list[str] = []
    confidence = str(summary.get("coverage_confidence") or "")
    if confidence == "low" or int(summary.get("extracted_doc_count") or 0) <= 1:
        recommendations.append("Rerun with `docpull crawl URL --mode exhaustive-docs --audit-gaps`.")
    if int(summary.get("skipped_js_only") or 0) and render == "off":
        recommendations.append(
            "JS-only pages were skipped; rerun with `--render fallback` after `docpull render --check`."
        )
    if int(summary.get("binary_pdf_skipped") or 0):
        recommendations.append("Binary/PDF URLs were skipped by docs-mode hygiene.")
    return recommendations


def _audit_gap_reasons(coverage: dict[str, Any]) -> list[str]:
    summary = coverage.get("summary") or {}
    reasons: list[str] = []
    if summary.get("coverage_confidence") == "low":
        reasons.append("coverage confidence is low")
    if int(summary.get("extracted_doc_count") or 0) <= 1:
        reasons.append("one-document or empty pack")
    selected = int(summary.get("selected_url_count") or 0)
    extracted = int(summary.get("extracted_doc_count") or 0)
    skipped_or_failed = int(summary.get("fetch_skipped_count") or 0) + int(
        summary.get("nonzero_failures") or 0
    )
    if selected >= 5 and extracted / selected < 0.5:
        reasons.append("less than half of selected URLs produced documents")
    if selected >= 5 and skipped_or_failed / selected >= 0.35:
        reasons.append("high skip/failure rate")
    return reasons


def _coverage_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Coverage Report",
        "",
        f"- URL: {payload.get('url')}",
        f"- Mode: `{payload.get('mode')}`",
        f"- Render: `{payload.get('render')}`",
        f"- Confidence: **{summary.get('coverage_confidence')}**",
        "",
        "## Counts",
        "",
    ]
    for key in (
        "discovered_url_count",
        "selected_url_count",
        "fetched_url_count",
        "extracted_doc_count",
        "skipped_js_only",
        "blocked_by_robots",
        "blocked_by_cloudflare_or_auth",
        "binary_pdf_skipped",
        "nonzero_failures",
    ):
        lines.append(f"- {key}: {summary.get(key, 0)}")
    recommendations = payload.get("recommendations") or []
    if recommendations:
        lines.extend(["", "## Recommendations", ""])
        lines.extend(f"- {item}" for item in recommendations)
    return "\n".join(lines).rstrip() + "\n"


def _crawl_result_payload(
    url: str,
    *,
    output_dir: Path,
    mode: str,
    render: str,
    dry_run: bool,
    map_payload: dict[str, Any],
    selected: list[CandidateSourceRecord],
    fetched: dict[str, Any],
    coverage: dict[str, Any],
    acquisition: dict[str, Any],
) -> dict[str, Any]:
    status = "dry_run" if dry_run else ("completed" if not fetched.get("errors") else "completed_with_errors")
    return {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": "crawl",
        "status": status,
        "url": url,
        "mode": mode,
        "render": render,
        "output_dir": str(output_dir),
        "summary": {
            **coverage["summary"],
            "selected_count": len(selected),
            "record_count": int(fetched.get("record_count", 0)),
        },
        "artifacts": {
            "documents_ndjson": "documents.ndjson",
            "corpus_manifest": "corpus.manifest.json",
            "sources": "sources.md",
            "pack": "local.pack.json",
            "selected_sources": "selected_sources.ndjson",
            "selected_urls": "selected_urls.txt",
            "coverage_report": "coverage.report.json",
            "coverage_markdown": "COVERAGE_REPORT.md",
            "acquisition_routes": "acquisition.routes.json",
            "crawl_result": "crawl.result.json",
            "crawl_report": "CRAWL_REPORT.md",
        },
        "map": map_payload,
        "coverage": coverage,
        "acquisition": acquisition,
        "errors": fetched.get("errors", []),
        "skips": fetched.get("skips", []),
    }


def _crawl_report_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Crawl Report",
        "",
        f"- Status: `{payload.get('status')}`",
        f"- URL: {payload.get('url')}",
        f"- Mode: `{payload.get('mode')}`",
        f"- Coverage confidence: **{summary.get('coverage_confidence')}**",
        f"- Documents: {summary.get('extracted_doc_count', 0)}",
        f"- Selected URLs: {summary.get('selected_url_count', 0)}",
        f"- Failures: {summary.get('nonzero_failures', 0)}",
    ]
    gap_reasons = payload.get("audit_gaps") or []
    if gap_reasons:
        lines.extend(["", "## Audit Gaps", ""])
        lines.extend(f"- {reason}" for reason in gap_reasons)
    return "\n".join(lines).rstrip() + "\n"


def _augment_quality_artifacts_for_coverage(pack_dir: Path, coverage: dict[str, Any], *, url: str) -> None:
    summary = coverage.get("summary") or {}
    if (
        summary.get("coverage_confidence") not in {"low", "medium"}
        and int(summary.get("extracted_doc_count") or 0) > 1
    ):
        return
    message = (
        f"This crawl appears shallow; rerun with `docpull crawl {url} --mode exhaustive-docs --audit-gaps`"
    )
    if int(summary.get("skipped_js_only") or 0):
        message += " and add `--render fallback` if JS-only pages are expected."
    else:
        message += "."
    for filename in ("pack.score.json", "pack.audit.json"):
        path = pack_dir / filename
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        warnings = payload.setdefault("warnings", [])
        if isinstance(warnings, list) and message not in warnings:
            warnings.append(message)
        payload.setdefault("summary", {})["coverage_confidence"] = summary.get("coverage_confidence")
        payload["coverage_report"] = "coverage.report.json"
        _write_json(path, payload)


def _skip_reason_value(value: Any) -> str:
    if isinstance(value, SkipReason):
        return value.value
    return str(value or "skipped")


def local_search(query: str, *, pack_dir: Path | None, limit: int, output_dir: Path) -> dict[str, Any]:
    if pack_dir is None:
        raise FreeCoreError("local search requires --pack-dir; use --provider tavily/exa/parallel explicitly")
    return build_search_pack(
        query,
        provider="local",
        pack_dir=pack_dir,
        output_dir=output_dir,
        max_results=limit,
    )


def extract_target(
    target: str,
    *,
    schema_path: Path | None,
    preset: str | None,
    output_dir: Path,
    policy: PolicyConfig,
    dry_run: bool = False,
) -> dict[str, Any]:
    if bool(schema_path) == bool(preset):
        raise FreeCoreError("Use exactly one of --schema or --preset")
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {"schema_version": FREE_CORE_SCHEMA_VERSION, "workflow": "extract", "status": "dry_run"}
    if preset == "brand":
        payload = build_brand_pack(target, output_dir=output_dir, policy=policy)
    elif preset == "product":
        payload = build_product_pack(target, output_dir=output_dir, policy=policy)
    elif preset == "styleguide":
        payload = build_styleguide_pack(target, output_dir=output_dir, policy=policy)
    else:
        assert schema_path is not None
        payload = extract_schema(target, schema_path=schema_path, output_dir=output_dir, policy=policy)
    _write_extraction_basis(output_dir, target)
    _write_validation_report(output_dir, payload)
    _publish_if_pack_readable(output_dir)
    return payload


def research_target(
    target: str,
    *,
    question: str,
    schema_path: Path | None,
    output_dir: Path,
    policy: PolicyConfig,
    dry_run: bool = False,
) -> dict[str, Any]:
    source = _resolve_target_url_or_pack(target)
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {"schema_version": FREE_CORE_SCHEMA_VERSION, "workflow": "research", "status": "dry_run"}
    if source.exists():
        payload = research_pack(source, objective=question, output_dir=output_dir, schema_path=schema_path)
    else:
        scrape_dir = output_dir / "_source-pack"
        scrape_url(target, output_dir=scrape_dir, policy=policy)
        payload = research_pack(
            scrape_dir,
            objective=question,
            output_dir=output_dir,
            schema_path=schema_path,
        )
    basis_source = source if source.exists() else scrape_dir
    write_basis(
        output_dir / "basis.ndjson",
        build_pack_basis(basis_source, claim_path="research.question", claim=question),
    )
    _publish_if_pack_readable(output_dir)
    return payload


def answer_target(
    target: str,
    *,
    question: str,
    output_dir: Path,
    policy: PolicyConfig,
    limit: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {"schema_version": FREE_CORE_SCHEMA_VERSION, "workflow": "answer", "status": "dry_run"}
    pack_dir = _pack_from_target(target, output_dir=output_dir / "_source-pack", policy=policy)
    payload = answer_pack(
        pack_dir,
        question,
        limit=limit,
        markdown_path=output_dir / "ANSWER.md",
        json_path=output_dir / "answer.result.json",
    )
    write_basis(
        output_dir / "basis.ndjson",
        build_pack_basis(pack_dir, claim_path="answer.question", claim=question, limit=limit),
    )
    _write_json(output_dir / "run.accounting.json", _accounting("answer"))
    return {**payload, "output_dir": str(output_dir.resolve()), "source_pack_dir": str(pack_dir)}


def entities_target(
    target: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    limit: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {"schema_version": FREE_CORE_SCHEMA_VERSION, "workflow": "entities", "status": "dry_run"}
    pack_dir = _pack_from_target(target, output_dir=output_dir / "_source-pack", policy=policy)
    payload = entities_pack(pack_dir, output_dir=output_dir, limit=limit)
    write_basis(
        output_dir / "basis.ndjson",
        build_pack_basis(pack_dir, claim_path="entities.source", claim=f"Extract entities from {target}"),
    )
    _write_json(output_dir / "run.accounting.json", _accounting("entities"))
    return {**payload, "source_pack_dir": str(pack_dir)}


def brief_target(
    target: str,
    *,
    objective: str | None,
    search_queries: list[str] | None,
    output_dir: Path,
    policy: PolicyConfig,
    max_excerpts: int,
    graph_entity_limit: int = DEFAULT_BRIEF_ENTITY_LIMIT,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {"schema_version": FREE_CORE_SCHEMA_VERSION, "workflow": "brief", "status": "dry_run"}
    pack_dir = _pack_from_target(target, output_dir=output_dir / "_source-pack", policy=policy)
    payload = prepare_pack(
        pack_dir,
        objective=objective or f"Brief {target}",
        search_queries=search_queries,
        max_excerpts=max_excerpts,
        graph_entity_limit=graph_entity_limit,
        output=output_dir / "brief.prepare.json",
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _copy_pack_artifacts(
        pack_dir,
        output_dir,
        [
            "RESEARCH_BRIEF.md",
            "research.brief.json",
            "ENTITIES.md",
            "entities.json",
            "SEARCH.md",
            "pack.search.json",
            "CITATIONS.md",
            "citations.json",
            "pack.score.json",
        ],
    )
    write_basis(
        output_dir / "basis.ndjson",
        build_pack_basis(pack_dir, claim_path="brief.objective", claim=objective or f"Brief {target}"),
    )
    _write_json(output_dir / "run.accounting.json", _accounting("brief"))
    return {**payload, "output_dir": str(output_dir.resolve()), "source_pack_dir": str(pack_dir)}


def image_target(
    target: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    download_assets: bool,
    max_assets: int,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "schema_version": FREE_CORE_SCHEMA_VERSION,
            "workflow": "image-pack",
            "status": "dry_run",
            "output_dir": str(output_dir.resolve()),
            "input": {"target": target, "download_assets": download_assets, "max_assets": max_assets},
        }
    payload = build_image_pack(
        target,
        output_dir=output_dir,
        policy=policy,
        download_assets=download_assets,
        max_assets=max_assets,
    )
    _standardize_optional_pack(output_dir, workflow="images", objective=f"Extract images from {target}")
    return payload


def screenshot_url(
    url: str,
    *,
    output_dir: Path,
    policy: PolicyConfig,
    viewport: str,
    full_page: bool,
    wait_for: str,
    agent_browser_binary: str | None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "schema_version": FREE_CORE_SCHEMA_VERSION,
            "workflow": "screenshot-pack",
            "status": "dry_run",
            "output_dir": str(output_dir.resolve()),
            "input": {"url": url, "viewport": viewport, "full_page": full_page, "wait_for": wait_for},
        }
    payload = capture_screenshot_pack(
        url,
        output_dir=output_dir,
        policy=policy,
        viewport=viewport,
        full_page=full_page,
        wait_for=wait_for,
        agent_browser_binary=agent_browser_binary,
    )
    _standardize_optional_pack(output_dir, workflow="screenshot", objective=f"Capture screenshot for {url}")
    return payload


def monitor_target(
    target: str,
    *,
    name: str | None,
    state_dir: Path,
    output_dir: Path,
    run_once: bool,
) -> dict[str, Any]:
    source = _resolve_target_url_or_pack(target)
    monitor_name = name or _safe_name(target)
    if source.exists():
        pack_dir = source
    else:
        scrape_url(target, output_dir=output_dir, policy=PolicyConfig())
        pack_dir = output_dir
    payload = init_monitor(monitor_name, pack_dir, state_dir=state_dir)
    if run_once:
        payload["run"] = run_monitor_once(monitor_name, state_dir=state_dir, dry_run=True)
    return payload


def _standardize_pack(pack_dir: Path, *, workflow: str, objective: str) -> None:
    _write_json(pack_dir / "run.accounting.json", _accounting(workflow))
    if not (pack_dir / "citations.json").exists():
        _write_json(pack_dir / "citations.json", build_citation_map(pack_dir))
    if not (pack_dir / "pack.score.json").exists():
        _write_json(pack_dir / "pack.score.json", score_pack(pack_dir))
    with suppress(LocalWorkflowError, PackToolError):
        audit_pack(pack_dir)
    if not (pack_dir / "chunks.jsonl").exists():
        _write_chunks_jsonl(pack_dir)
    if not (pack_dir / "basis.ndjson").exists():
        write_basis(
            pack_dir / "basis.ndjson",
            build_pack_basis(pack_dir, claim_path="pack.objective", claim=objective),
        )
    _write_context_lock(pack_dir, workflow=workflow)
    _publish_if_pack_readable(pack_dir)


def _standardize_optional_pack(pack_dir: Path, *, workflow: str, objective: str) -> None:
    _write_json(pack_dir / "run.accounting.json", _accounting(workflow))
    if (pack_dir / "documents.ndjson").exists():
        _standardize_pack(pack_dir, workflow=workflow, objective=objective)
    else:
        _write_context_lock(pack_dir, workflow=workflow)


def _publish_if_pack_readable(pack_dir: Path) -> None:
    try:
        publish_agent_docs(pack_dir)
    except (AgentPublishError, PackToolError):
        return


def _write_context_lock(pack_dir: Path, *, workflow: str) -> None:
    payload = {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": workflow,
        "pack_dir": str(pack_dir.resolve()),
    }
    _write_json(pack_dir / "context.lock.json", payload)


def _write_extraction_basis(output_dir: Path, target: str) -> None:
    basis_path = output_dir / "basis.ndjson"
    if basis_path.exists() and basis_path.read_text(encoding="utf-8").strip():
        return
    records = [
        basis_record(
            claim_path="extract.target",
            claim=f"Structured extraction target: {target}",
            source_urls=[target] if target.startswith("http") else [],
            confidence="medium",
            evidence_state="partial",
            warnings=["target-level basis; inspect field-level extraction basis when available"],
            producer="docpull.free-core.extract",
        )
    ]
    write_basis(basis_path, records)


def _write_chunks_jsonl(pack_dir: Path) -> None:
    documents_path = pack_dir / "documents.ndjson"
    if not documents_path.exists():
        return
    lines: list[str] = []
    for line_number, line in enumerate(documents_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        chunk = {
            "schema_version": FREE_CORE_SCHEMA_VERSION,
            "chunk_id": record.get("chunk_id") or f"chunk_{line_number:04d}",
            "document_id": record.get("document_id"),
            "url": record.get("url"),
            "title": record.get("title"),
            "content": record.get("content", ""),
            "content_hash": record.get("content_hash"),
            "chunk_index": record.get("chunk_index") or 0,
        }
        lines.append(json.dumps(chunk, sort_keys=True))
    (pack_dir / "chunks.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _write_validation_report(output_dir: Path, payload: dict[str, Any]) -> None:
    validation = payload.get("validation") if isinstance(payload, dict) else None
    _write_json(
        output_dir / "validation.report.json",
        {
            "schema_version": FREE_CORE_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "valid": validation.get("valid") if isinstance(validation, dict) else None,
            "validation": validation,
        },
    )


def _accounting(command: str) -> dict[str, Any]:
    return {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "command": command,
        "budget_limit_usd": 0,
        "estimated_paid_cost_usd": 0,
        "paid_request_count": 0,
    }


def _policy_with_default_domain(policy: PolicyConfig, url: str) -> PolicyConfig:
    """Default free local map/crawl to same-origin unless policy explicitly expands it."""
    if policy.allowed_domains:
        return policy
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return policy
    data = policy.model_dump(mode="json")
    data["allowed_domains"] = [host]
    return PolicyConfig.model_validate(data)


def _records_allowed_by_policy(records: list[Any], policy: PolicyConfig) -> list[Any]:
    return [record for record in records if policy.allows_url(str(record.url))[0]]


def _base_parser(prog: str, description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--output-dir", "-o", type=Path, default=Path("packs/free-core"))
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--max-results", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget", type=float, default=0)
    parser.add_argument("--render", choices=["off", "fallback", "agent-browser"], default="off")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


async def _scan_site(url: str, *, policy: PolicyConfig, max_per_source: int) -> list[Any]:
    async with AsyncHttpClient(
        rate_limiter=PerHostRateLimiter(default_delay=0.2, default_concurrent=2),
        max_retries=1,
        log_retry_warnings=False,
    ) as client:
        return await records_from_site_scan(
            url,
            client=client,
            expected_domains=policy.allowed_domains,
            max_results_per_source=max_per_source,
        )


async def _scan_site_recursive(
    url: str,
    *,
    policy: PolicyConfig,
    max_per_source: int,
    max_depth: int,
    max_records: int,
) -> list[CandidateSourceRecord]:
    if max_depth < 1:
        max_depth = 1
    max_scan_pages = min(max(max_records // 2, 1), 40)
    records_by_url: dict[str, CandidateSourceRecord] = {}
    scanned: set[str] = set()
    queued: set[str] = {normalize_url(url)}
    queue: list[tuple[str, int]] = [(url, 0)]

    async with AsyncHttpClient(
        rate_limiter=PerHostRateLimiter(default_delay=0.2, default_concurrent=2),
        max_retries=1,
        log_retry_warnings=False,
    ) as client:
        while queue and len(scanned) < max_scan_pages and len(records_by_url) < max_records:
            current_url, depth = queue.pop(0)
            current_key = normalize_url(current_url)
            if current_key in scanned:
                continue
            scanned.add(current_key)
            sources = None if depth == 0 else ["links"]
            site_records = await records_from_site_scan(
                current_url,
                client=client,
                sources=sources,
                expected_domains=policy.allowed_domains,
                max_results_per_source=max_per_source,
            )
            for record in site_records:
                allowed, _reason = policy.allows_url(record.url)
                if not allowed:
                    continue
                key = normalize_url(record.url)
                records_by_url.setdefault(key, record)
                if (
                    depth + 1 < max_depth
                    and key not in scanned
                    and key not in queued
                    and len(queue) + len(scanned) < max_scan_pages
                ):
                    queue.append((record.url, depth + 1))
                    queued.add(key)
                if len(records_by_url) >= max_records:
                    break
    return list(records_by_url.values())


def _policy(path: Path | None) -> PolicyConfig:
    return PolicyConfig.from_file(path) if path else PolicyConfig()


def _batch_urls(urls: list[str], *, input_path: Path | None) -> list[str]:
    values = list(urls)
    if input_path is not None:
        try:
            text = input_path.read_text(encoding="utf-8")
        except OSError as err:
            raise FreeCoreError(f"Could not read --input file: {err}") from err
        if input_path.suffix.lower() == ".json":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as err:
                raise FreeCoreError(f"--input JSON is invalid: {err}") from err
            if isinstance(parsed, list):
                values.extend(_url_from_item(item) for item in parsed)
            else:
                raise FreeCoreError("--input JSON must be a list")
        else:
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if stripped.startswith("{"):
                    try:
                        values.append(_url_from_item(json.loads(stripped)))
                    except json.JSONDecodeError as err:
                        raise FreeCoreError(f"--input NDJSON line is invalid: {err}") from err
                else:
                    values.append(stripped)
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        url = str(value).strip()
        if not url:
            continue
        if not url.startswith(("http://", "https://")):
            raise FreeCoreError(f"batch input must be absolute http(s) URLs: {url}")
        if url not in seen:
            output.append(url)
            seen.add(url)
    return output


def _url_from_item(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("url", "href", "link"):
            value = item.get(key)
            if value:
                return str(value)
    return ""


def _pack_from_target(target: str, *, output_dir: Path, policy: PolicyConfig) -> Path:
    source = _resolve_target_url_or_pack(target)
    if source.exists():
        return source
    scrape_url(target, output_dir=output_dir, policy=policy)
    return output_dir


def _copy_pack_artifacts(source_dir: Path, output_dir: Path, names: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = source_dir / name
        if source.exists() and source.is_file():
            (output_dir / name).write_bytes(source.read_bytes())


def _print_result(action: Any, *, json_output: bool, label: str) -> int:
    console = Console()
    try:
        payload = action()
    except (
        AgentPublishError,
        ContextPackError,
        DiscoveryError,
        FreeCoreError,
        LocalWorkflowError,
        MonitorError,
        PackToolError,
        ParityWorkflowError,
        PolicyError,
    ) as err:
        console.print(f"[red]{label} error:[/red] " + escape(str(err)))
        return 1
    if json_output:
        console.print_json(data=payload)
    else:
        out = payload.get("output_dir") if isinstance(payload, dict) else None
        console.print(f"[green]{label}:[/green] {out or 'ok'}")
    if isinstance(payload, dict) and payload.get("exit_code") is not None:
        return int(payload["exit_code"])
    return 0


def _provider_search(provider: str, args: argparse.Namespace) -> int:
    if args.budget <= 0:
        Console().print("[red]Search error:[/red] provider-backed search requires --budget greater than 0")
        return 1
    if provider in {"tavily", "exa"}:
        from .provider_cli import run_provider_extension_cli

        tail = [
            "context-pack",
            args.query,
            "--query",
            args.query,
            "--output-dir",
            str(args.output_dir),
        ]
        if args.dry_run:
            tail.append("--dry-run")
        if args.json_output:
            tail.append("--json")
        return run_provider_extension_cli(provider, tail)
    if provider == "parallel":
        from .parallel_workflows import run_parallel_cli

        tail = ["context-pack", args.query, "--query", args.query, "--output-dir", str(args.output_dir)]
        if args.dry_run:
            tail.append("--dry-run")
        if args.json_output:
            tail.append("--json")
        return run_parallel_cli(tail)
    raise FreeCoreError(f"Unsupported provider: {provider}")


def _resolve_target_url_or_pack(target: str) -> Path:
    path = Path(target).expanduser()
    return path.resolve() if path.exists() else path


def _safe_name(value: str) -> str:
    alias = get_context_alias(value)
    if alias:
        return alias.name
    return "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-") or "monitor"


def _write_selected(path: Path, records: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(record.model_dump_json() + "\n" for record in records), encoding="utf-8")


def _sitegraph(root_url: str, records: list[Any]) -> dict[str, Any]:
    return {
        "schema_version": FREE_CORE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "root_url": root_url,
        "node_count": len(records),
        "nodes": [{"url": record.url, "title": record.title, "source": record.source} for record in records],
        "edges": [],
    }


def _map_markdown(url: str, report: dict[str, Any]) -> str:
    return (
        f"# Map: {url}\n\n"
        f"- Candidates: {report.get('candidate_count', 0)}\n"
        f"- Skipped: {report.get('skipped_count', 0)}\n"
        "- Content fetched: no\n"
    )


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
