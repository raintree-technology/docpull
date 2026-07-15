"""Repeatable docpull benchmark harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import resource
import shlex
import sys
import time
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from rich.console import Console
from rich.markup import escape

from .accounting import (
    RunAccounting,
    default_route_steps,
    effective_budget_limit,
    maybe_write_run_accounting,
    parse_budget_value,
)
from .conversion.chunking import TokenCounter
from .core.fetcher import Fetcher
from .models.config import CacheConfig, CrawlConfig, DocpullConfig, OutputConfig, ProfileName, RenderConfig
from .models.document import DocumentRecord
from .models.events import EventType
from .pack_tools import prepare_pack, score_pack, score_pack_sources
from .parallel_workflows import (
    DEFAULT_MAX_ESTIMATED_COST_USD,
    DEFAULT_MODE,
    ParallelWorkflowError,
    _build_source_policy,
    _md_link,
    _parallel_sdk_installed,
    estimate_context_pack_cost,
    estimate_search_pack_cost,
    run_live_context_pack,
    run_search_pack,
)
from .passk import pass_at_k
from .pipeline.manifest import CorpusManifest
from .provider_adapters import (
    ProviderAdapterError,
    live_provider_statuses,
    normalize_live_providers,
    provider_adapter,
    provider_case_payload,
)
from .provider_keys import ProviderName, lookup_api_key_env_var
from .rendering import estimate_cloud_render_cost_usd
from .time_utils import utc_now_iso

BENCHMARK_SCHEMA_VERSION = 2
DEFAULT_TARGET_URL = "https://docs.parallel.ai"
DEFAULT_INCLUDE_DOMAIN = "docs.parallel.ai"
DEFAULT_OBJECTIVE = "Build an agent context pack for Parallel API docs"
DEFAULT_QUERY = "Parallel API reference Search Extract docs"
DEFAULT_TARGET_SET = "single"
EXA_API_KEY_ENV = "EXA_API_KEY"
TAVILY_API_KEY_ENV = "TAVILY_API_KEY"
TAVILY_CREDIT_USD_ENV = "TAVILY_CREDIT_USD"
RAINDROP_WRITE_KEY_ENV = "RAINDROP_WRITE_KEY"
EXA_SEARCH_URL = "https://api.exa.ai/search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
HTTP_RETRY_MAX_ATTEMPTS = 3
HTTP_RETRY_TRANSIENT_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
HTTP_RETRY_CAP_SECONDS = 30.0
BENCHMARK_SCORE_WEIGHTS = {
    "coverage": 0.30,
    "cleanliness": 0.20,
    "source_fidelity": 0.20,
    "freshness": 0.15,
    "density": 0.15,
}
PASS_AT_K_THRESHOLDS: tuple[int, ...] = (70, 80, 90)
TARGET_SET_CHOICES = ("single", "tool-docs", "provider-matrix", "zero-dollar", "phase2", "v2")
HTTP_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
HTTP_MAX_ERROR_BYTES = 64 * 1024
# Conservative per-call USD figures used ONLY for the pre-flight
# --max-estimated-cost guard on the live Tavily/Exa search providers. Real
# spend is reconciled from provider usage after the run; these are loose upper
# bounds chosen so the guard fails safe. They never feed published numbers.
APPROX_TAVILY_CREDIT_USD = 0.01
APPROX_EXA_SEARCH_USD = 0.01
ZERO_DOLLAR_CLASSES: tuple[str, ...] = (
    "complete_for_0",
    "complete_with_local_browser",
    "partial_for_0",
    "requires_provider",
    "requires_cloud_browser",
    "blocked_by_policy",
)
ZERO_DOLLAR_COMPLETE_SCORE = 75
ZERO_DOLLAR_ROUTE_CLASSES = {
    "direct_http": "partial_for_0",
    "local_browser": "complete_with_local_browser",
    "provider": "requires_provider",
    "cloud_browser": "requires_cloud_browser",
    "policy": "blocked_by_policy",
}
ESCALATION_PROVIDER_ORDER: tuple[str, ...] = ("tavily", "exa", "parallel")
ESCALATION_MAX_SEARCH_RESULTS = 8
ESCALATION_EXTRACT_LIMIT = 3


class BenchmarkError(RuntimeError):
    """User-facing benchmark error."""


@dataclass
class _ProviderDocument:
    url: str
    title: str
    content: str
    metadata: dict[str, Any]
    source_type: str


@dataclass(frozen=True)
class _BenchmarkTarget:
    id: str
    label: str
    url: str
    include_domains: tuple[str, ...]
    objective: str
    queries: tuple[str, ...]
    kind: str = "docs"
    min_expected_records: int = 3
    freshness_terms: tuple[str, ...] = ()
    notes: str = ""
    zero_dollar_route: str = "direct_http"

    def report_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "url": self.url,
            "include_domains": list(self.include_domains),
            "objective": self.objective,
            "queries": list(self.queries),
            "kind": self.kind,
            "min_expected_records": self.min_expected_records,
            "freshness_terms": list(self.freshness_terms),
            "notes": self.notes,
            "zero_dollar_route": self.zero_dollar_route,
        }


TOOL_DOC_TARGETS: tuple[_BenchmarkTarget, ...] = (
    _BenchmarkTarget(
        id="parallel_docs",
        label="Parallel docs",
        url="https://docs.parallel.ai",
        include_domains=("docs.parallel.ai",),
        objective="Build an agent context pack for Parallel API docs",
        queries=("Parallel API reference Search Extract docs",),
        freshness_terms=("changelog", "release", "latest"),
    ),
    _BenchmarkTarget(
        id="exa_docs",
        label="Exa docs",
        url="https://docs.exa.ai",
        include_domains=("docs.exa.ai",),
        objective="Build an agent context pack for Exa API docs",
        queries=("Exa API documentation search contents docs",),
        freshness_terms=("changelog", "release", "latest"),
    ),
    _BenchmarkTarget(
        id="tavily_docs",
        label="Tavily docs",
        url="https://docs.tavily.com",
        include_domains=("docs.tavily.com",),
        objective="Build an agent context pack for Tavily API docs",
        queries=("Tavily API documentation search extract docs",),
        freshness_terms=("changelog", "release", "latest"),
    ),
    _BenchmarkTarget(
        id="raindrop_docs",
        label="Raindrop docs",
        url="https://www.raindrop.ai/docs",
        include_domains=("www.raindrop.ai", "raindrop.ai"),
        objective="Build an agent context pack for Raindrop observability docs",
        queries=("Raindrop AI SDK Python tracing documentation",),
        freshness_terms=("sdk", "python", "tracing", "latest"),
    ),
    _BenchmarkTarget(
        id="docpull_docs",
        label="DocPull docs",
        url="https://github.com/raintree-technology/docpull",
        include_domains=("github.com",),
        objective="Build an agent context pack for DocPull documentation",
        queries=("DocPull documentation CLI provider benchmark docs",),
        freshness_terms=("changelog", "release", "benchmark"),
    ),
)


ADVERSARIAL_TARGETS: tuple[_BenchmarkTarget, ...] = (
    _BenchmarkTarget(
        id="nextjs_docs_spa",
        label="Next.js docs SPA",
        url="https://nextjs.org/docs",
        include_domains=("nextjs.org",),
        objective="Build an agent context pack for Next.js App Router docs",
        queries=("Next.js App Router documentation rendering data fetching docs",),
        kind="js_heavy_docs",
        min_expected_records=4,
        freshness_terms=("version", "latest", "app router"),
        notes="JS-heavy documentation target with a large rendered navigation surface.",
        zero_dollar_route="local_browser",
    ),
    _BenchmarkTarget(
        id="python27_archived_stdlib",
        label="Python 2.7 archived stdlib index",
        url="https://docs.python.org/2.7/library/index.html",
        include_domains=("docs.python.org",),
        objective="Build an agent context pack for archived Python 2.7 standard library docs",
        queries=("Python 2.7 archived standard library documentation index reference",),
        kind="noisy_archived_docs",
        min_expected_records=4,
        freshness_terms=("2.7", "deprecated", "end-of-life", "legacy"),
        notes="Archived dense navigation/index page that can expose stale-source and boilerplate extraction.",
    ),
    _BenchmarkTarget(
        id="tavily_pricing",
        label="Tavily pricing",
        url="https://www.tavily.com/pricing",
        include_domains=("www.tavily.com", "tavily.com"),
        objective="Build a freshness-sensitive context pack for Tavily pricing",
        queries=("Tavily pricing plans credits API pricing",),
        kind="pricing_freshness",
        min_expected_records=1,
        freshness_terms=("pricing", "credits", "plan", "current"),
        notes="Freshness-sensitive public pricing page; keep crawl caps low.",
    ),
)


PHASE2_MEASUREMENT_TARGETS: tuple[_BenchmarkTarget, ...] = (
    _BenchmarkTarget(
        id="sec_apple_10k",
        label="Apple 10-K filing",
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm",
        include_domains=("sec.gov",),
        objective="Build an evidence pack for Apple's 2024 Form 10-K filing",
        queries=("Apple 2024 10-K SEC filing risk factors business md&a",),
        kind="filing",
        min_expected_records=1,
        freshness_terms=("10-k", "risk factors", "business", "2024"),
        notes="Public SEC filing target for filing extraction without provider search.",
    ),
    _BenchmarkTarget(
        id="pypi_docpull_rss",
        label="DocPull PyPI release feed",
        url="https://pypi.org/rss/project/docpull/releases.xml",
        include_domains=("pypi.org",),
        objective="Capture DocPull release feed entries from PyPI RSS",
        queries=("DocPull PyPI release feed latest releases",),
        kind="feed",
        min_expected_records=1,
        freshness_terms=("docpull", "release", "pypi"),
        notes="RSS/Atom-style target; Phase 3 feed adapters should improve candidate extraction.",
    ),
    _BenchmarkTarget(
        id="python_docs_sitemap",
        label="Python docs sitemap",
        url="https://docs.python.org/sitemap.xml",
        include_domains=("docs.python.org",),
        objective="Use the Python docs sitemap as a source-discovery seed",
        queries=("Python documentation sitemap library reference tutorial",),
        kind="sitemap",
        min_expected_records=1,
        freshness_terms=("python", "library", "tutorial"),
        notes="Sitemap target; Phase 3 richer sitemap discovery should improve completion.",
    ),
    _BenchmarkTarget(
        id="packaging_search_to_evidence",
        label="Packaging docs search-to-evidence",
        url="https://www.python.org",
        include_domains=("packaging.python.org", "docs.python.org"),
        objective="Find official evidence for Python packaging pyproject.toml build-system guidance",
        queries=("official Python packaging pyproject.toml build system guide",),
        kind="search_to_evidence",
        min_expected_records=2,
        freshness_terms=("pyproject.toml", "build-system", "packaging"),
        notes=(
            "Intentional discovery gap: the seed URL is broad and the evidence "
            "lives on related official docs."
        ),
        zero_dollar_route="provider",
    ),
)


TARGET_SETS: dict[str, tuple[_BenchmarkTarget, ...]] = {
    "tool-docs": TOOL_DOC_TARGETS,
    "provider-matrix": (*TOOL_DOC_TARGETS, *ADVERSARIAL_TARGETS),
    "zero-dollar": (*TOOL_DOC_TARGETS, *ADVERSARIAL_TARGETS, *PHASE2_MEASUREMENT_TARGETS),
    "phase2": (*TOOL_DOC_TARGETS, *ADVERSARIAL_TARGETS, *PHASE2_MEASUREMENT_TARGETS),
    "v2": (*TOOL_DOC_TARGETS, *ADVERSARIAL_TARGETS),
}


def create_benchmark_parser() -> argparse.ArgumentParser:
    """Create the ``docpull benchmark`` parser."""
    parser = argparse.ArgumentParser(
        prog="docpull benchmark",
        description="Run repeatable docpull and optional live-provider context-pack benchmarks",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    quick = subparsers.add_parser(
        "quick",
        help="Run a small real-site benchmark and write JSON/Markdown reports",
    )
    quick.add_argument("--target-url", default=DEFAULT_TARGET_URL, help="Target URL to crawl")
    quick.add_argument(
        "--target-set",
        choices=TARGET_SET_CHOICES,
        default=DEFAULT_TARGET_SET,
        help=(
            "Target matrix to run. 'single' preserves --target-url behavior; "
            "'tool-docs' runs the five provider/docpull docs sites; "
            "'provider-matrix' adds low-cap hard targets; 'zero-dollar' adds "
            "Phase 2 measurement targets for JS docs, pricing, filings, feeds, "
            "sitemaps, and search-to-evidence. 'phase2' aliases 'zero-dollar'; "
            "'v2' remains as a compatibility alias."
        ),
    )
    quick.add_argument(
        "--matrix",
        action="store_const",
        const="provider-matrix",
        dest="target_set",
        help="Compatibility alias for --target-set provider-matrix",
    )
    quick.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        help="Benchmark run directory (default: .bench/runs/<timestamp>)",
    )
    quick.add_argument("--max-pages", type=int, default=25, help="Core crawl page cap")
    quick.add_argument("--max-depth", type=int, default=2, help="Core crawl depth cap")
    quick.add_argument("--max-concurrent", type=int, default=8, help="Core crawl concurrency")
    quick.add_argument("--per-host-concurrent", type=int, default=4, help="Core per-host concurrency")
    quick.add_argument("--no-cache", action="store_true", help="Disable cache for the core crawl")
    quick.add_argument(
        "--runs",
        type=int,
        default=1,
        help=(
            "Repeat each case N times and report median wall seconds and score "
            "with min/max spread. Per-run artifacts land under run-1/, run-2/, ... "
            "subdirs. N>1 forces --no-cached-pass since the cached pass shares state "
            "with the prior run by design."
        ),
    )
    quick.set_defaults(cached_pass=None)
    quick.add_argument(
        "--cached-pass",
        dest="cached_pass",
        action="store_true",
        help="Force the second core cache-measurement pass, including matrix target sets",
    )
    quick.add_argument(
        "--no-cached-pass",
        dest="cached_pass",
        action="store_false",
        help="Skip the second core run that measures cache skips",
    )
    quick.add_argument(
        "--parallel",
        action="store_true",
        help="Compatibility alias for --provider parallel",
    )
    quick.add_argument("--tavily", action="store_true", help="Compatibility alias for --provider tavily")
    quick.add_argument("--exa", action="store_true", help="Compatibility alias for --provider exa")
    quick.add_argument(
        "--provider",
        action="append",
        choices=["auto", "all", "parallel", "tavily", "exa"],
        default=[],
        help=(
            "Live provider case to add. Repeat for parallel, tavily, and exa; "
            "use auto/all to run every configured provider and skip missing keys."
        ),
    )
    quick.add_argument(
        "--objective",
        "--parallel-objective",
        dest="parallel_objective",
        default=None,
        help="Live-provider research objective",
    )
    quick.add_argument(
        "--query",
        "--parallel-query",
        action="append",
        dest="parallel_queries",
        default=[],
        help="Live-provider search query. Repeat as needed.",
    )
    quick.add_argument(
        "--include-domain",
        action="append",
        dest="include_domains",
        default=[],
        help="Expected source domain for live providers and pack scoring. Repeat as needed.",
    )
    quick.add_argument("--mode", choices=["turbo", "basic", "advanced"], default=DEFAULT_MODE)
    quick.add_argument("--max-search-results", type=int, default=8)
    quick.add_argument("--extract-limit", type=int, default=3)
    quick.add_argument(
        "--tavily-credit-usd",
        type=float,
        default=None,
        help=(
            f"Optional Tavily credit-to-dollar value. If omitted, {TAVILY_CREDIT_USD_ENV} "
            "is used when present and Tavily credit costs remain unnormalized otherwise."
        ),
    )
    quick.add_argument(
        "--max-estimated-cost",
        type=float,
        default=DEFAULT_MAX_ESTIMATED_COST_USD,
        help="Local pre-call spend guard for providers with known cost estimates",
    )
    quick.add_argument(
        "--budget",
        type=parse_budget_value,
        default=None,
        metavar="USD",
        help="Maximum paid-capable provider/cloud spend for this benchmark. Use 0 for zero paid calls.",
    )
    quick.add_argument(
        "--zero-dollar",
        action="store_true",
        help="Classify zero-dollar completion and block live provider cases.",
    )
    quick.add_argument(
        "--trace",
        choices=["none", "raindrop"],
        default="none",
        help=(
            "Optional observability trace backend. Raindrop requires "
            "RAINDROP_WRITE_KEY and the raindrop-ai package."
        ),
    )
    quick.add_argument("--json", action="store_true", dest="json_output", help="Print report JSON")

    article = subparsers.add_parser(
        "article",
        help="Generate a publishable Markdown article from a benchmark report",
    )
    article.add_argument("report", type=Path, help="benchmark.report.json path")
    article.add_argument("--output", "-o", type=Path, help="Article Markdown output path")
    article.add_argument(
        "--title",
        default="Benchmarking docpull, Parallel, Tavily, Exa, and Raindrop for Agent Context Packs",
        help="Article title",
    )

    return parser


def run_benchmark_cli(argv: list[str] | None = None) -> int:
    """Entrypoint for ``docpull benchmark``."""
    parser = create_benchmark_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.command == "quick":
            report = run_quick_benchmark(
                target_url=args.target_url,
                target_set=args.target_set,
                output_dir=args.output_dir,
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                max_concurrent=args.max_concurrent,
                per_host_concurrent=args.per_host_concurrent,
                cache_enabled=not args.no_cache,
                cached_pass=args.cached_pass,
                parallel=args.parallel,
                tavily=args.tavily,
                exa=args.exa,
                live_providers=args.provider,
                parallel_objective=args.parallel_objective,
                parallel_queries=args.parallel_queries,
                include_domains=args.include_domains,
                mode=args.mode,
                max_search_results=args.max_search_results,
                extract_limit=args.extract_limit,
                tavily_credit_usd=args.tavily_credit_usd,
                max_estimated_cost=args.max_estimated_cost,
                budget_limit=0.0 if args.zero_dollar else args.budget,
                zero_dollar=args.zero_dollar,
                trace_backend=args.trace,
                runs=args.runs,
            )
            if args.json_output:
                console.print_json(data=report)
            else:
                console.print(
                    "[green]Benchmark report:[/green] "
                    f"{report['artifacts']['json']} "
                    f"({report['summary']['case_count']} cases)"
                )
                console.print(f"[green]Benchmark summary:[/green] {report['artifacts']['markdown']}")
                if report.get("skipped_providers"):
                    skipped = _format_skipped_providers(report["skipped_providers"])
                    console.print(f"[yellow]Skipped unavailable providers:[/yellow] {skipped}")
            return 0
        if args.command == "article":
            output = write_article_from_report(args.report, output=args.output, title=args.title)
            console.print(f"[green]Benchmark article:[/green] {output}")
            return 0
        parser.error(f"Unknown command: {args.command}")
    except BenchmarkError as err:
        console.print("[red]Benchmark error:[/red] " + escape(str(err)))
        return 1
    except ParallelWorkflowError as err:
        console.print("[red]Parallel benchmark error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Benchmark failed:[/red] " + escape(str(err)))
        return 1
    return 1


def run_quick_benchmark(
    *,
    target_url: str,
    output_dir: Path | None,
    max_pages: int,
    max_depth: int,
    max_concurrent: int,
    per_host_concurrent: int,
    cache_enabled: bool,
    cached_pass: bool | None,
    parallel: bool,
    parallel_objective: str | None,
    parallel_queries: list[str],
    include_domains: list[str],
    mode: str,
    max_search_results: int,
    extract_limit: int,
    max_estimated_cost: float,
    budget_limit: float | None = None,
    zero_dollar: bool = False,
    target_set: str = DEFAULT_TARGET_SET,
    tavily_credit_usd: float | None = None,
    trace_backend: str = "none",
    tavily: bool = False,
    exa: bool = False,
    live_providers: list[str] | None = None,
    runs: int = 1,
) -> dict[str, Any]:
    """Run the default real-site benchmark matrix."""
    _validate_positive_int(max_pages, "max_pages")
    _validate_positive_int(max_depth, "max_depth")
    _validate_positive_int(max_concurrent, "max_concurrent")
    _validate_positive_int(per_host_concurrent, "per_host_concurrent")
    _validate_positive_int(max_search_results, "max_search_results")
    _validate_positive_int(extract_limit, "extract_limit")
    _validate_positive_int(runs, "runs")
    if max_estimated_cost < 0:
        raise BenchmarkError("max_estimated_cost cannot be negative.")
    effective_max_estimated_cost = effective_budget_limit(max_estimated_cost, budget_limit)
    if effective_max_estimated_cost is None:
        effective_max_estimated_cost = max_estimated_cost
    zero_dollar = zero_dollar or budget_limit == 0
    target_set = _canonical_target_set(target_set)
    tavily_credit_usd = _resolve_tavily_credit_usd(tavily_credit_usd)
    targets = _resolve_benchmark_targets(
        target_url=target_url,
        target_set=target_set,
        include_domains=include_domains,
        objective=parallel_objective,
        queries=parallel_queries,
    )
    if cached_pass is None:
        cached_pass = len(targets) == 1
    if runs > 1:
        # Cached pass shares cache state with its prior pass by design; that
        # composes poorly with N-run aggregation. Force it off and surface in
        # the report so users notice.
        cached_pass = False
    requested_providers = _normalize_live_providers(
        parallel=parallel,
        tavily=tavily,
        exa=exa,
        live_providers=live_providers,
    )
    if zero_dollar and requested_providers:
        provider_status = _live_provider_statuses(requested_providers)
        providers = []
        skipped_providers = [
            {
                "provider": provider,
                "reason": "blocked_by_budget",
            }
            for provider in requested_providers
        ]
    else:
        provider_status = _live_provider_statuses(requested_providers)
        providers = [provider for provider in requested_providers if provider_status[provider]["ready"]]
        skipped_providers = [
            {
                "provider": provider,
                "reason": provider_status[provider]["reason"],
            }
            for provider in requested_providers
            if provider not in providers
        ]
    parallel = "parallel" in providers

    run_dir = (output_dir or _default_run_dir()).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    estimated_search_cost = 0.0
    estimated_context_cost = 0.0
    estimated_costs: dict[str, float] = {}
    if parallel:
        estimated_search_cost = estimate_search_pack_cost(max_search_results=max_search_results)
        estimated_context_cost = estimate_context_pack_cost(
            extract_limit=extract_limit,
            max_search_results=max_search_results,
        )
        estimated_costs["parallel"] = round(
            (estimated_search_cost + estimated_context_cost) * len(targets) * runs,
            6,
        )
    if "tavily" in providers:
        credit_usd = tavily_credit_usd if tavily_credit_usd is not None else APPROX_TAVILY_CREDIT_USD
        estimated_costs["tavily"] = round(
            (1 + extract_limit) * credit_usd * len(targets) * runs,
            6,
        )
    if "exa" in providers:
        estimated_costs["exa"] = round(APPROX_EXA_SEARCH_USD * len(targets) * runs, 6)
    estimated_total_cost = round(sum(estimated_costs.values()), 6)
    if estimated_total_cost > effective_max_estimated_cost:
        breakdown = ", ".join(f"{name}=${value:.6f}" for name, value in estimated_costs.items())
        raise BenchmarkError(
            f"Estimated live-provider benchmark cost ${estimated_total_cost:.6f} "
            f"({breakdown}) exceeds guard ${effective_max_estimated_cost:.6f}."
        )

    trace = _make_trace_recorder(
        trace_backend,
        target_url=targets[0].url,
        targets=targets,
        target_set=target_set,
        output_dir=run_dir,
        parallel_enabled=parallel,
        max_estimated_cost=max_estimated_cost,
    )

    cases: list[dict[str, Any]] = []
    matrix_run = len(targets) > 1

    def run_and_record(
        *,
        name: str,
        workflow: str,
        provider: str,
        target: _BenchmarkTarget,
        output_dir: Path,
        cache_dir: Path | None = None,
        prompt: str,
        settings: dict[str, Any],
        runner_factory: Callable[..., dict[str, Any]],
    ) -> None:
        per_run: list[dict[str, Any]] = []
        for run_index in range(1, runs + 1):
            if runs == 1:
                run_output = output_dir
                run_cache = cache_dir
            else:
                run_output = output_dir / f"run-{run_index}"
                run_cache = (cache_dir / f"run-{run_index}") if cache_dir else None
            rss_before = _peak_rss_bytes()
            t0 = time.perf_counter()
            try:
                case = runner_factory(output_dir=run_output, cache_dir=run_cache)
            except Exception as err:  # noqa: BLE001
                case = _failed_case(
                    name=name,
                    workflow=workflow,
                    output_dir=run_output,
                    wall_seconds=time.perf_counter() - t0,
                    rss_before=rss_before,
                    error=err,
                )
            per_run.append(case)
        case = (
            per_run[0]
            if runs == 1
            else _aggregate_runs(
                per_run,
                name=name,
                workflow=workflow,
                output_dir=output_dir,
                runs_total=runs,
            )
        )
        _annotate_case(
            case,
            provider=provider,
            target=target,
            prompt=prompt,
            settings=settings,
            matrix_run=matrix_run,
        )
        trace.record_case(case)
        cases.append(case)

    for target in targets:
        target_root = run_dir / _safe_slug(target.id) if matrix_run else run_dir
        core_cache_dir = target_root / "cache-core"
        core_output = target_root / "core-llm"

        def core_factory(
            *,
            output_dir: Path,
            cache_dir: Path | None,
            target: _BenchmarkTarget = target,
        ) -> dict[str, Any]:
            if cache_dir is None:
                raise BenchmarkError("cache_dir is required for the core benchmark case.")
            return asyncio.run(
                _run_core_case(
                    name="core-llm",
                    target_url=target.url,
                    output_dir=output_dir,
                    cache_dir=cache_dir,
                    cache_enabled=cache_enabled,
                    max_pages=max_pages,
                    max_depth=max_depth,
                    max_concurrent=max_concurrent,
                    per_host_concurrent=per_host_concurrent,
                    include_domains=list(target.include_domains),
                    target=target,
                )
            )

        run_and_record(
            name="core-llm",
            workflow="core-llm",
            provider="docpull",
            target=target,
            output_dir=core_output,
            cache_dir=core_cache_dir,
            prompt=target.objective,
            settings={
                "profile": "llm",
                "max_pages": max_pages,
                "max_depth": max_depth,
                "max_concurrent": max_concurrent,
                "per_host_concurrent": per_host_concurrent,
                "cache_enabled": cache_enabled,
            },
            runner_factory=core_factory,
        )

        if cache_enabled and cached_pass:
            cached_output = target_root / "core-llm-cached"

            def cached_factory(
                *,
                output_dir: Path,
                cache_dir: Path | None,
                target: _BenchmarkTarget = target,
                shared_cache: Path = core_cache_dir,
            ) -> dict[str, Any]:
                # Reuse the prior pass's cache regardless of the per-run cache
                # arg — the cached pass is the second half of a paired
                # measurement. (Only reachable when runs == 1.)
                return asyncio.run(
                    _run_core_case(
                        name="core-llm-cached",
                        target_url=target.url,
                        output_dir=output_dir,
                        cache_dir=shared_cache,
                        cache_enabled=True,
                        max_pages=max_pages,
                        max_depth=max_depth,
                        max_concurrent=max_concurrent,
                        per_host_concurrent=per_host_concurrent,
                        include_domains=list(target.include_domains),
                        target=target,
                    )
                )

            run_and_record(
                name="core-llm-cached",
                workflow="core-llm",
                provider="docpull",
                target=target,
                output_dir=cached_output,
                prompt=target.objective,
                settings={
                    "profile": "llm",
                    "max_pages": max_pages,
                    "max_depth": max_depth,
                    "max_concurrent": max_concurrent,
                    "per_host_concurrent": per_host_concurrent,
                    "cache_enabled": True,
                    "cache_measurement": True,
                },
                runner_factory=cached_factory,
            )

        if parallel:
            source_policy = _build_source_policy(include_domains=list(target.include_domains))
            search_output = target_root / "parallel-search"

            def parallel_search_factory(
                *,
                output_dir: Path,
                cache_dir: Path | None,
                target: _BenchmarkTarget = target,
                source_policy: dict[str, Any] = source_policy,
            ) -> dict[str, Any]:
                return _run_parallel_search_case(
                    objective=target.objective,
                    queries=list(target.queries),
                    output_dir=output_dir,
                    include_domains=list(target.include_domains),
                    source_policy=source_policy,
                    mode=mode,
                    max_search_results=max_search_results,
                    estimated_cost=estimated_search_cost,
                    target=target,
                )

            run_and_record(
                name="parallel-search",
                workflow="parallel-search-pack",
                provider="parallel",
                target=target,
                output_dir=search_output,
                prompt=target.objective,
                settings={"mode": mode, "max_search_results": max_search_results},
                runner_factory=parallel_search_factory,
            )
            context_output = target_root / "parallel-context"

            def parallel_context_factory(
                *,
                output_dir: Path,
                cache_dir: Path | None,
                target: _BenchmarkTarget = target,
                source_policy: dict[str, Any] = source_policy,
            ) -> dict[str, Any]:
                return _run_parallel_context_case(
                    objective=target.objective,
                    queries=list(target.queries),
                    output_dir=output_dir,
                    include_domains=list(target.include_domains),
                    source_policy=source_policy,
                    mode=mode,
                    max_search_results=max_search_results,
                    extract_limit=extract_limit,
                    estimated_cost=estimated_context_cost,
                    target=target,
                )

            run_and_record(
                name="parallel-context",
                workflow="parallel-context-pack",
                provider="parallel",
                target=target,
                output_dir=context_output,
                prompt=target.objective,
                settings={
                    "mode": mode,
                    "max_search_results": max_search_results,
                    "extract_limit": extract_limit,
                },
                runner_factory=parallel_context_factory,
            )

        if "tavily" in providers:
            tavily_output = target_root / "tavily-search-extract"

            def tavily_factory(
                *,
                output_dir: Path,
                cache_dir: Path | None,
                target: _BenchmarkTarget = target,
            ) -> dict[str, Any]:
                return _run_tavily_case(
                    objective=target.objective,
                    queries=list(target.queries),
                    output_dir=output_dir,
                    include_domains=list(target.include_domains),
                    max_search_results=max_search_results,
                    extract_limit=extract_limit,
                    tavily_credit_usd=tavily_credit_usd,
                    target=target,
                )

            run_and_record(
                name="tavily-search-extract",
                workflow="tavily-search-extract-pack",
                provider="tavily",
                target=target,
                output_dir=tavily_output,
                prompt=target.objective,
                settings={
                    "max_search_results": max_search_results,
                    "extract_limit": extract_limit,
                    "tavily_credit_usd": tavily_credit_usd,
                },
                runner_factory=tavily_factory,
            )

        if "exa" in providers:
            exa_output = target_root / "exa-search-contents"

            def exa_factory(
                *,
                output_dir: Path,
                cache_dir: Path | None,
                target: _BenchmarkTarget = target,
            ) -> dict[str, Any]:
                return _run_exa_case(
                    objective=target.objective,
                    queries=list(target.queries),
                    output_dir=output_dir,
                    include_domains=list(target.include_domains),
                    max_search_results=max_search_results,
                    target=target,
                )

            run_and_record(
                name="exa-search-contents",
                workflow="exa-search-contents-pack",
                provider="exa",
                target=target,
                output_dir=exa_output,
                prompt=target.objective,
                settings={"max_search_results": max_search_results},
                runner_factory=exa_factory,
            )

    report_path = run_dir / "benchmark.report.json"
    markdown_path = run_dir / "benchmark.summary.md"
    config_path = run_dir / "benchmark.config.json"
    generated_at = utc_now_iso()
    safe_provider_status = _benchmark_provider_statuses(provider_status)
    artifacts: dict[str, str] = {
        "config": str(config_path),
        "json": str(report_path),
        "markdown": str(markdown_path),
    }
    benchmark_config = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": generated_at,
        "run_dir": str(run_dir),
        "target_url": targets[0].url,
        "target_set": target_set,
        "targets": [target.report_dict() for target in targets],
        "requested_providers": requested_providers,
        "enabled_providers": ["core", *providers],
        "skipped_providers": skipped_providers,
        "provider_status": safe_provider_status,
        "runs_per_case": runs,
        "budget_limit_usd": budget_limit,
        "zero_dollar": zero_dollar,
        "settings": {
            "max_pages": max_pages,
            "max_depth": max_depth,
            "max_concurrent": max_concurrent,
            "per_host_concurrent": per_host_concurrent,
            "cache_enabled": cache_enabled,
            "cached_pass": cached_pass,
            "mode": mode,
            "max_search_results": max_search_results,
            "extract_limit": extract_limit,
            "max_estimated_cost": max_estimated_cost,
        },
        "cost_normalization": _cost_normalization_metadata(tavily_credit_usd),
    }
    report = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": generated_at,
        "run_dir": str(run_dir),
        "target_url": targets[0].url,
        "target_set": target_set,
        "targets": [target.report_dict() for target in targets],
        "parallel_enabled": parallel,
        "providers": ["core", *providers],
        "matrix_providers": _matrix_provider_keys(providers),
        "requested_providers": requested_providers,
        "skipped_providers": skipped_providers,
        "budget_limit_usd": budget_limit,
        "zero_dollar": zero_dollar,
        "provider_status": safe_provider_status,
        "cost_normalization": _cost_normalization_metadata(tavily_credit_usd),
        "runs_per_case": runs,
        "trace": trace.metadata(),
        "cases": cases,
        "summary": _summary(cases),
        "artifacts": artifacts,
    }
    if budget_limit is not None or zero_dollar:
        accounting_path = maybe_write_run_accounting(
            run_dir,
            budget_limit_usd=budget_limit,
            paid_capable=bool(requested_providers),
            accounting=RunAccounting(
                budget_limit_usd=budget_limit,
                estimated_paid_cost_usd=estimated_total_cost,
                paid_request_count=0 if zero_dollar else len(providers),
                blocked_actions=[],
                route_steps=default_route_steps(
                    include_provider=bool(requested_providers),
                    budget_limit_usd=budget_limit,
                ),
                command="benchmark quick",
            ),
        )
        if accounting_path:
            artifacts["accounting"] = str(accounting_path)
        report["zero_dollar_completion"] = _zero_dollar_completion(cases)
    trace.finish(report)
    report["trace"] = trace.metadata()
    config_path.write_text(
        json.dumps(benchmark_config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown_report(report), encoding="utf-8")
    return report


def write_article_from_report(
    report_path: Path,
    *,
    output: Path | None = None,
    title: str,
) -> Path:
    """Write a publishable article draft from a benchmark JSON report."""
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except OSError as err:
        raise BenchmarkError(f"Could not read benchmark report {report_path}: {err}") from err
    except json.JSONDecodeError as err:
        raise BenchmarkError(f"Invalid benchmark report JSON {report_path}: {err}") from err
    if not isinstance(report, dict) or not isinstance(report.get("cases"), list):
        raise BenchmarkError("Benchmark report must be a JSON object with a cases list.")
    article_path = output or (report_path.parent / "benchmark.article.md")
    article_path.write_text(_article_markdown(report, title=title), encoding="utf-8")
    return article_path


def _resolve_benchmark_targets(
    *,
    target_url: str,
    target_set: str,
    include_domains: list[str],
    objective: str | None,
    queries: list[str],
) -> list[_BenchmarkTarget]:
    target_set = _canonical_target_set(target_set)
    if target_set == "single":
        return [
            _single_benchmark_target(
                target_url=target_url,
                include_domains=include_domains,
                objective=objective,
                queries=queries,
            )
        ]
    if target_set not in TARGET_SETS:
        raise BenchmarkError(f"Unsupported target set: {target_set}")
    targets: list[_BenchmarkTarget] = []
    for target in TARGET_SETS[target_set]:
        targets.append(
            _BenchmarkTarget(
                id=target.id,
                label=target.label,
                url=target.url,
                include_domains=target.include_domains,
                objective=objective or target.objective,
                queries=tuple(queries) if queries else target.queries,
                kind=target.kind,
                min_expected_records=target.min_expected_records,
                freshness_terms=target.freshness_terms,
                notes=target.notes,
                zero_dollar_route=target.zero_dollar_route,
            )
        )
    return targets


def _canonical_target_set(target_set: str) -> str:
    if target_set == "v2":
        return "provider-matrix"
    if target_set == "phase2":
        return "zero-dollar"
    return target_set


def _single_benchmark_target(
    *,
    target_url: str,
    include_domains: list[str],
    objective: str | None,
    queries: list[str],
) -> _BenchmarkTarget:
    domain = _domain_for_url(target_url) or DEFAULT_INCLUDE_DOMAIN
    normalized_domains = tuple(_normalize_domain(domain) for domain in (include_domains or [domain]))
    target_objective = objective or (
        DEFAULT_OBJECTIVE if target_url == DEFAULT_TARGET_URL else f"Build an agent context pack for {domain}"
    )
    default_queries = [DEFAULT_QUERY] if target_url == DEFAULT_TARGET_URL else [f"{domain} docs"]
    target_queries = tuple(queries or default_queries)
    return _BenchmarkTarget(
        id=_safe_slug(domain),
        label=domain,
        url=target_url,
        include_domains=normalized_domains,
        objective=target_objective,
        queries=target_queries,
        min_expected_records=1,
        freshness_terms=("changelog", "release", "latest") if "docs" in domain else (),
    )


def _resolve_tavily_credit_usd(value: float | None) -> float | None:
    if value is not None:
        if value < 0:
            raise BenchmarkError("tavily_credit_usd cannot be negative.")
        return value
    raw = _lookup_benchmark_secret(TAVILY_CREDIT_USD_ENV)
    if not raw:
        return None
    try:
        parsed = float(raw)
    except ValueError as err:
        raise BenchmarkError(f"{TAVILY_CREDIT_USD_ENV} must be a number.") from err
    if parsed < 0:
        raise BenchmarkError(f"{TAVILY_CREDIT_USD_ENV} cannot be negative.")
    return parsed


def _cost_normalization_metadata(tavily_credit_usd: float | None) -> dict[str, Any]:
    return {
        "currency": "USD",
        "tavily": {
            "credit_usd": tavily_credit_usd,
            "source": "cli_or_env" if tavily_credit_usd is not None else "not_configured",
            "env_var": TAVILY_CREDIT_USD_ENV,
        },
        "policy": (
            "Tavily credits are converted to estimated USD when a per-credit value is configured; "
            "Parallel and Exa report dollar estimates directly."
        ),
    }


def _matrix_provider_keys(providers: list[ProviderName]) -> list[str]:
    values = ["docpull-core"]
    if "parallel" in providers:
        values.extend(["parallel-search", "parallel-context"])
    if "tavily" in providers:
        values.append("tavily-search-extract")
    if "exa" in providers:
        values.append("exa-search-contents")
    return values


def _annotate_case(
    case: dict[str, Any],
    *,
    provider: str,
    target: _BenchmarkTarget,
    prompt: str,
    settings: dict[str, Any],
    matrix_run: bool,
) -> None:
    original_name = str(case.get("name") or provider)
    if matrix_run:
        case["name"] = f"{target.id}/{original_name}"
    case["provider"] = provider
    case["target"] = target.report_dict()
    case["target_id"] = target.id
    case["target_url"] = target.url
    case["target_kind"] = target.kind
    case["prompt"] = prompt
    case["settings"] = settings


def _domain_for_url(url: str) -> str | None:
    parsed = urlparse(url)
    return _normalize_domain(parsed.netloc) if parsed.netloc else None


def _normalize_domain(value: str) -> str:
    return value.lower().removeprefix("www.")


def _safe_slug(value: str) -> str:
    slug = "".join(char if char.isalnum() else "-" for char in value.lower()).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "target"


def _normalize_live_providers(
    *,
    parallel: bool,
    tavily: bool,
    exa: bool,
    live_providers: list[str] | None,
) -> list[ProviderName]:
    try:
        return normalize_live_providers(
            parallel=parallel,
            tavily=tavily,
            exa=exa,
            live_providers=live_providers,
        )
    except ProviderAdapterError as err:
        raise BenchmarkError(str(err)) from err


def _live_provider_statuses(providers: list[ProviderName]) -> dict[str, dict[str, Any]]:
    return live_provider_statuses(
        providers,
        parallel_sdk_installed=_parallel_sdk_installed,
    )


def _benchmark_provider_statuses(
    statuses: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    safe_statuses: dict[str, dict[str, Any]] = {}
    for provider, status in statuses.items():
        safe_statuses[provider] = {
            "provider": status.get("provider", provider),
            "label": status.get("label", provider),
            "ready": bool(status.get("ready")),
            "reason": status.get("reason", "unknown"),
            "sdk_installed": bool(status.get("sdk_installed", True)),
        }
    return safe_statuses


def _path_basename(value: Any) -> str | None:
    if not value:
        return None
    return Path(str(value)).name


def _basename_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _path_basename(item) for key, item in value.items()}
    return value


class _TraceRecorder:
    provider = "none"

    def record_case(self, _case: dict[str, Any]) -> None:
        return

    def finish(self, _report: dict[str, Any]) -> None:
        return

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "enabled": False,
            "status": "disabled",
        }


class _RaindropTraceRecorder(_TraceRecorder):
    provider = "raindrop"

    def __init__(
        self,
        *,
        target_url: str,
        targets: list[_BenchmarkTarget],
        target_set: str,
        output_dir: Path,
        parallel_enabled: bool,
        max_estimated_cost: float,
    ) -> None:
        api_key = _lookup_benchmark_secret(RAINDROP_WRITE_KEY_ENV)
        if not api_key:
            raise BenchmarkError(
                "Raindrop tracing requires RAINDROP_WRITE_KEY. Store it in "
                "~/.config/docpull/secrets.env or export it in the environment."
            )
        try:
            import raindrop.analytics as raindrop  # type: ignore[import-not-found]
        except ImportError as err:
            raise BenchmarkError(
                "Raindrop tracing requires the optional SDK. Install with: pip install raindrop-ai"
            ) from err
        self._raindrop: Any = raindrop
        try:
            raindrop.init(api_key, tracing_enabled=True, bypass_otel_for_tools=True)
        except TypeError:
            raindrop.init(api_key, tracing_enabled=True)
        self._event_id = str(uuid.uuid4())
        self._interaction = raindrop.begin(
            user_id="docpull-benchmark",
            event="docpull_benchmark",
            event_id=self._event_id,
            properties={
                "target_set": target_set,
                "target_count": len(targets),
                "output_dir": output_dir.name,
                "parallel_enabled": parallel_enabled,
                "max_estimated_cost_usd": max_estimated_cost,
                "content_policy": "metadata_only",
            },
            input=_json_trace_text(
                {
                    "target_url": target_url,
                    "target_set": target_set,
                    "targets": [target.report_dict() for target in targets],
                    "output_dir": output_dir.name,
                    "parallel_enabled": parallel_enabled,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            ),
        )
        self._case_count = 0
        self._signal_count = 0
        self._positive_signal_count = 0
        self._negative_signal_count = 0
        self._signal_names: Counter[str] = Counter()
        self._status = "recording"

    def record_case(self, case: dict[str, Any]) -> None:
        self._case_count += 1
        self._interaction.track_tool(
            name=str(case.get("name") or "benchmark_case"),
            input={
                "workflow": case.get("workflow"),
                "target": case.get("target"),
                "prompt": case.get("prompt"),
                "settings": case.get("settings"),
                "output_dir": _path_basename(case.get("output_dir")),
            },
            output=_trace_case_output(case),
            duration_ms=int(float(case.get("wall_seconds") or 0.0) * 1000),
            properties={
                "provider": case.get("provider", "docpull"),
                "workflow": case.get("workflow"),
                "target_id": case.get("target_id"),
                "target_url": case.get("target_url"),
                "target_kind": case.get("target_kind"),
                "prompt": case.get("prompt"),
                "estimated_cost_usd": case.get("estimated_cost_usd", 0.0),
            },
        )
        for signal in _raindrop_case_signals(case):
            self._raindrop.track_signal(
                event_id=self._event_id,
                name=signal["name"],
                properties=signal["properties"],
                sentiment=signal["sentiment"],
            )
            self._signal_count += 1
            self._signal_names[str(signal["name"])] += 1
            if signal["sentiment"] == "POSITIVE":
                self._positive_signal_count += 1
            else:
                self._negative_signal_count += 1

    def finish(self, report: dict[str, Any]) -> None:
        self._interaction.set_properties(
            {
                "summary": report.get("summary"),
                "artifacts": _basename_only(report.get("artifacts")),
                "signal_count": self._signal_count,
                "positive_signal_count": self._positive_signal_count,
                "negative_signal_count": self._negative_signal_count,
                "signal_names": dict(self._signal_names),
            }
        )
        self._interaction.finish(
            output=_json_trace_text(
                {
                    "summary": report.get("summary"),
                    "artifacts": _basename_only(report.get("artifacts")),
                    "trace_signals": {
                        "total": self._signal_count,
                        "positive": self._positive_signal_count,
                        "negative": self._negative_signal_count,
                        "names": dict(self._signal_names),
                    },
                }
            )
        )
        self._raindrop.flush()
        self._raindrop.shutdown()
        self._status = "recorded"

    def metadata(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "enabled": True,
            "status": self._status,
            "event_id": self._event_id or None,
            "case_count": self._case_count,
            "signal_count": self._signal_count,
            "positive_signal_count": self._positive_signal_count,
            "negative_signal_count": self._negative_signal_count,
            "signal_names": dict(self._signal_names),
            "content_policy": "metadata_only",
        }


def _make_trace_recorder(
    backend: str,
    *,
    target_url: str,
    targets: list[_BenchmarkTarget],
    target_set: str,
    output_dir: Path,
    parallel_enabled: bool,
    max_estimated_cost: float,
) -> _TraceRecorder:
    if backend == "none":
        return _TraceRecorder()
    if backend == "raindrop":
        return _RaindropTraceRecorder(
            target_url=target_url,
            targets=targets,
            target_set=target_set,
            output_dir=output_dir,
            parallel_enabled=parallel_enabled,
            max_estimated_cost=max_estimated_cost,
        )
    raise BenchmarkError(f"Unsupported trace backend: {backend}")


def _trace_case_output(case: dict[str, Any]) -> dict[str, Any]:
    score = case.get("pack_score")
    score_summary = score.get("summary") if isinstance(score, dict) else None
    metadata = case.get("pack_metadata")
    selected_urls = metadata.get("selected_urls") if isinstance(metadata, dict) else None
    return {
        "status": case.get("status", "ok"),
        "error": case.get("error"),
        "provider": case.get("provider"),
        "target_id": case.get("target_id"),
        "target_url": case.get("target_url"),
        "wall_seconds": case.get("wall_seconds"),
        "rss_delta_mb": case.get("rss_delta_mb"),
        "artifact_size_bytes": case.get("artifact_size_bytes"),
        "cache_size_bytes": case.get("cache_size_bytes"),
        "estimated_cost_usd": case.get("estimated_cost_usd", 0.0),
        "cost_units": case.get("cost_units"),
        "stats": case.get("stats"),
        "skip_counts": case.get("skip_counts"),
        "pack_score": {
            "score": score.get("score"),
            "grade": score.get("grade"),
            "summary": score_summary,
            "issue_count": len(score.get("issues") or []),
            "warning_count": len(score.get("warnings") or []),
        }
        if isinstance(score, dict)
        else None,
        "benchmark_score": case.get("benchmark_score"),
        "source_score_count": case.get("source_score_count"),
        "selected_urls": selected_urls,
    }


def _raindrop_case_signals(case: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    base = _raindrop_signal_properties(case)
    if case.get("status") == "failed":
        signals.append(
            _raindrop_signal(
                "benchmark_case_failed",
                base,
                sentiment="NEGATIVE",
                reason=case.get("error"),
            )
        )
        return signals

    benchmark_score = _score_value(case.get("benchmark_score"))
    if benchmark_score is not None:
        if benchmark_score >= 95:
            signals.append(
                _raindrop_signal(
                    "benchmark_high_score",
                    base,
                    sentiment="POSITIVE",
                    benchmark_score=benchmark_score,
                )
            )
        elif benchmark_score < 90:
            signals.append(
                _raindrop_signal(
                    "benchmark_low_score",
                    base,
                    sentiment="NEGATIVE",
                    benchmark_score=benchmark_score,
                )
            )

    wall_seconds = _optional_number(case.get("wall_seconds"))
    if wall_seconds is not None and wall_seconds >= 10:
        signals.append(
            _raindrop_signal(
                "benchmark_slow_case",
                base,
                sentiment="NEGATIVE",
                wall_seconds=round(wall_seconds, 3),
            )
        )

    estimated_cost = _optional_number(case.get("estimated_cost_usd"))
    if estimated_cost is not None and estimated_cost >= 0.01:
        signals.append(
            _raindrop_signal(
                "benchmark_high_cost_case",
                base,
                sentiment="NEGATIVE",
                estimated_cost_usd=round(estimated_cost, 6),
            )
        )

    raw_score = case.get("benchmark_score")
    dimensions = raw_score.get("dimensions") if isinstance(raw_score, dict) else None
    if isinstance(dimensions, dict):
        for dimension_name, dimension in dimensions.items():
            if not isinstance(dimension, dict):
                continue
            dimension_signals = dimension.get("signals")
            if not dimension_signals:
                continue
            signals.append(
                _raindrop_signal(
                    "benchmark_dimension_signal",
                    base,
                    sentiment="NEGATIVE",
                    dimension=dimension_name,
                    dimension_score=dimension.get("score"),
                    dimension_signals=dimension_signals,
                )
            )
    return signals


def _raindrop_signal(
    name: str,
    base: dict[str, Any],
    *,
    sentiment: str,
    **extra: Any,
) -> dict[str, Any]:
    properties = dict(base)
    properties.update({key: value for key, value in extra.items() if value is not None})
    return {
        "name": name,
        "sentiment": sentiment,
        "properties": properties,
    }


def _raindrop_signal_properties(case: dict[str, Any]) -> dict[str, Any]:
    metadata = case.get("pack_metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    score = case.get("pack_score")
    score_summary = score.get("summary") if isinstance(score, dict) else {}
    score_summary = score_summary if isinstance(score_summary, dict) else {}
    benchmark_score = case.get("benchmark_score")
    return {
        "case": case.get("name"),
        "provider": case.get("provider"),
        "workflow": case.get("workflow"),
        "target_id": case.get("target_id"),
        "target_url": case.get("target_url"),
        "target_kind": case.get("target_kind"),
        "status": case.get("status", "ok"),
        "pack_score": _score_value(score),
        "benchmark_score": _score_value(benchmark_score),
        "wall_seconds": case.get("wall_seconds"),
        "estimated_cost_usd": case.get("estimated_cost_usd", 0.0),
        "record_count": score_summary.get("record_count"),
        "total_tokens": score_summary.get("total_tokens"),
        "selected_url_count": len(metadata.get("selected_urls") or []),
        "extract_error_count": metadata.get("extract_error_count"),
        "content_policy": "metadata_only",
    }


def _score_value(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    score = value.get("score")
    if isinstance(score, int):
        return score
    return None


def _json_trace_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


async def _run_core_case(
    *,
    name: str,
    target_url: str,
    output_dir: Path,
    cache_dir: Path,
    cache_enabled: bool,
    max_pages: int,
    max_depth: int,
    max_concurrent: int,
    per_host_concurrent: int,
    include_domains: list[str],
    target: _BenchmarkTarget | None = None,
) -> dict[str, Any]:
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    skip_counts: Counter[str] = Counter()
    cfg = DocpullConfig(
        url=target_url,
        profile=ProfileName.LLM,
        output=OutputConfig(directory=output_dir),
        cache=CacheConfig(enabled=cache_enabled, directory=cache_dir, skip_unchanged=True),
        crawl=CrawlConfig(
            max_pages=max_pages,
            max_depth=max_depth,
            max_concurrent=max_concurrent,
            per_host_concurrent=per_host_concurrent,
        ),
    )
    async with Fetcher(cfg) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_SKIPPED and event.skip_reason:
                skip_counts[event.skip_reason.value] += 1
        stats = fetcher.stats
    wall_seconds = time.perf_counter() - t0
    payload = _base_case(
        name=name,
        workflow="core-llm",
        output_dir=output_dir,
        wall_seconds=wall_seconds,
        rss_before=rss_before,
    )
    payload.update(
        {
            "stats": stats.to_dict(),
            "skip_counts": dict(skip_counts),
            "artifact_size_bytes": _dir_size(output_dir),
            "cache_size_bytes": _dir_size(cache_dir),
        }
    )
    _attach_pack_scores(payload, output_dir, include_domains)
    _attach_benchmark_score(payload, output_dir, include_domains, target=target)
    return payload


def _run_parallel_search_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    source_policy: dict[str, Any],
    mode: str,
    max_search_results: int,
    estimated_cost: float,
    target: _BenchmarkTarget | None = None,
) -> dict[str, Any]:
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    run_search_pack(
        objective=objective,
        queries=queries,
        mode=mode,
        output_dir=output_dir,
        source_policy=source_policy,
        fetch_policy=None,
        max_search_results=max_search_results,
        max_search_chars_total=None,
        excerpt_chars_per_result=None,
        location=None,
        client_model=None,
        estimated_cost_usd=estimated_cost,
    )
    payload = _base_case(
        name="parallel-search",
        workflow="parallel-search-pack",
        output_dir=output_dir,
        wall_seconds=time.perf_counter() - t0,
        rss_before=rss_before,
    )
    payload["estimated_cost_usd"] = estimated_cost
    payload["artifact_size_bytes"] = _dir_size(output_dir)
    _attach_pack_metadata(payload, output_dir / "search.pack.json")
    _attach_pack_intelligence(
        payload,
        output_dir,
        include_domains,
        objective=objective,
        queries=queries,
    )
    _attach_benchmark_score(payload, output_dir, include_domains, target=target)
    return payload


def _run_parallel_context_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    source_policy: dict[str, Any],
    mode: str,
    max_search_results: int,
    extract_limit: int,
    estimated_cost: float,
    target: _BenchmarkTarget | None = None,
) -> dict[str, Any]:
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    run_live_context_pack(
        objective=objective,
        queries=queries,
        output_dir=output_dir,
        mode=mode,
        extract_limit=extract_limit,
        source_policy=source_policy,
        max_search_results=max_search_results,
        estimated_cost_usd=estimated_cost,
    )
    payload = _base_case(
        name="parallel-context",
        workflow="parallel-context-pack",
        output_dir=output_dir,
        wall_seconds=time.perf_counter() - t0,
        rss_before=rss_before,
    )
    payload["estimated_cost_usd"] = estimated_cost
    payload["artifact_size_bytes"] = _dir_size(output_dir)
    _attach_pack_metadata(payload, output_dir / "parallel.pack.json")
    _attach_pack_intelligence(
        payload,
        output_dir,
        include_domains,
        objective=objective,
        queries=queries,
    )
    _attach_benchmark_score(payload, output_dir, include_domains, target=target)
    return payload


def _run_tavily_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int,
    tavily_credit_usd: float | None = None,
    target: _BenchmarkTarget | None = None,
) -> dict[str, Any]:
    api_key = _require_benchmark_api_key(TAVILY_API_KEY_ENV, "Tavily")
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    try:
        result = provider_adapter("tavily", api_key=api_key, http_post=_http_json_post).search_extract_pack(
            objective=objective,
            queries=queries,
            output_dir=output_dir,
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=extract_limit,
            mode="basic",
        )
    except ProviderAdapterError as err:
        raise BenchmarkError(str(err)) from err
    payload = provider_case_payload(
        result,
        name="tavily-search-extract",
        workflow="tavily-search-extract-pack",
        wall_seconds=time.perf_counter() - t0,
        rss_before=rss_before,
        include_domains=include_domains,
        objective=objective,
        queries=queries,
        tavily_credit_usd=tavily_credit_usd,
    )
    _attach_benchmark_score(payload, output_dir, include_domains, target=target)
    return payload


def _run_exa_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int | None = None,
    target: _BenchmarkTarget | None = None,
) -> dict[str, Any]:
    api_key = _require_benchmark_api_key(EXA_API_KEY_ENV, "Exa")
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    try:
        result = provider_adapter("exa", api_key=api_key, http_post=_http_json_post).search_extract_pack(
            objective=objective,
            queries=queries,
            output_dir=output_dir,
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=extract_limit or max_search_results,
            mode="advanced",
        )
    except ProviderAdapterError as err:
        raise BenchmarkError(str(err)) from err
    payload = provider_case_payload(
        result,
        name="exa-search-contents",
        workflow="exa-search-contents-pack",
        wall_seconds=time.perf_counter() - t0,
        rss_before=rss_before,
        include_domains=include_domains,
        objective=objective,
        queries=queries,
    )
    _attach_benchmark_score(payload, output_dir, include_domains, target=target)
    return payload


def _base_case(
    *,
    name: str,
    workflow: str,
    output_dir: Path,
    wall_seconds: float,
    rss_before: int,
) -> dict[str, Any]:
    rss_after = _peak_rss_bytes()
    return {
        "name": name,
        "workflow": workflow,
        "output_dir": str(output_dir),
        "wall_seconds": round(wall_seconds, 3),
        "rss_baseline_mb": round(rss_before / (1024 * 1024), 1),
        "rss_peak_mb": round(rss_after / (1024 * 1024), 1),
        "rss_delta_mb": round(max(0, rss_after - rss_before) / (1024 * 1024), 1),
    }


def _failed_case(
    *,
    name: str,
    workflow: str,
    output_dir: Path,
    wall_seconds: float,
    rss_before: int,
    error: BaseException,
) -> dict[str, Any]:
    payload = _base_case(
        name=name,
        workflow=workflow,
        output_dir=output_dir,
        wall_seconds=wall_seconds,
        rss_before=rss_before,
    )
    payload.update(
        {
            "status": "failed",
            "error": {
                "type": type(error).__name__,
                "message": _short_error_detail(str(error)),
            },
            "artifact_size_bytes": _dir_size(output_dir),
            "pack_score": None,
            "benchmark_score": None,
            "source_score_count": 0,
        }
    )
    return payload


def _attach_pack_scores(payload: dict[str, Any], output_dir: Path, include_domains: list[str]) -> None:
    documents_path = output_dir / "documents.ndjson"
    if not documents_path.exists():
        payload["pack_score"] = None
        payload["source_score_count"] = 0
        return
    score = score_pack(output_dir, required_domains=include_domains)
    sources = score_pack_sources(output_dir, required_domains=include_domains)
    (output_dir / "pack.score.json").write_text(
        json.dumps(score, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "source.scores.json").write_text(
        json.dumps(sources, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _attach_pack_score_payload(payload, score, sources)


def _attach_pack_score_payload(
    payload: dict[str, Any],
    score: dict[str, Any],
    sources: dict[str, Any],
) -> None:
    payload["pack_score"] = {
        "score": score["score"],
        "grade": score["grade"],
        "summary": score["summary"],
        "issues": score["issues"],
        "warnings": score["warnings"],
    }
    payload["source_score_count"] = sources["source_count"]


def _attach_pack_intelligence(
    payload: dict[str, Any],
    output_dir: Path,
    include_domains: list[str],
    *,
    objective: str,
    queries: list[str],
) -> None:
    documents_path = output_dir / "documents.ndjson"
    if not documents_path.exists():
        payload["pack_score"] = None
        payload["source_score_count"] = 0
        payload["pack_intelligence"] = None
        return
    try:
        prepared = prepare_pack(
            output_dir,
            objective=objective,
            search_queries=queries,
            required_domains=include_domains,
        )
    except Exception as err:  # noqa: BLE001
        payload["pack_intelligence"] = None
        payload["pack_intelligence_error"] = {
            "type": type(err).__name__,
            "message": _short_error_detail(str(err)),
        }
        try:
            _attach_pack_scores(payload, output_dir, include_domains)
        except Exception as score_err:  # noqa: BLE001
            payload["pack_score"] = None
            payload["source_score_count"] = 0
            payload["pack_score_error"] = {
                "type": type(score_err).__name__,
                "message": _short_error_detail(str(score_err)),
            }
        payload["artifact_size_bytes"] = _dir_size(output_dir)
        return

    score = json.loads((output_dir / "pack.score.json").read_text(encoding="utf-8"))
    sources = json.loads((output_dir / "source.scores.json").read_text(encoding="utf-8"))
    _attach_pack_score_payload(payload, score, sources)
    payload["pack_intelligence"] = {
        "summary": prepared["summary"],
        "artifacts": prepared["artifacts"],
        "search_queries": prepared["search_queries"],
    }
    payload["artifact_size_bytes"] = _dir_size(output_dir)


def _attach_pack_metadata(payload: dict[str, Any], path: Path) -> None:
    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return
    raw_metadata = raw.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    payload["pack_metadata"] = {
        "workflow": raw.get("workflow"),
        "item_count": raw.get("item_count"),
        "record_count": raw.get("record_count"),
        "search_id": raw.get("search_id") or metadata.get("search_id"),
        "session_id": raw.get("session_id") or metadata.get("session_id"),
        "selected_urls": raw.get("selected_urls"),
        "search_result_count": raw.get("search_result_count"),
        "extract_result_count": raw.get("extract_result_count"),
        "extract_error_count": raw.get("extract_error_count"),
        "usage": raw.get("usage") or metadata.get("usage"),
        "provider": raw.get("provider"),
        "cost_dollars": raw.get("cost_dollars"),
    }


def _attach_tavily_cost(
    payload: dict[str, Any],
    search_usage: Any,
    extract_usage: Any,
    tavily_credit_usd: float | None,
) -> None:
    search_credits = _usage_credits(search_usage)
    extract_credits = _usage_credits(extract_usage)
    total_credits = round(search_credits + extract_credits, 6)
    payload["cost_units"] = {
        "provider": "tavily",
        "unit": "credit",
        "search_credits": search_credits,
        "extract_credits": extract_credits,
        "total_credits": total_credits,
        "credit_usd": tavily_credit_usd,
    }
    if tavily_credit_usd is not None:
        payload["estimated_cost_usd"] = round(total_credits * tavily_credit_usd, 6)


def _usage_credits(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        for key in ("credits", "credit", "total_credits", "totalCredits"):
            raw = value.get(key)
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                return float(raw)
    return 0.0


def _attach_benchmark_score(
    payload: dict[str, Any],
    output_dir: Path,
    include_domains: list[str],
    *,
    target: _BenchmarkTarget | None = None,
) -> None:
    score = payload.get("pack_score")
    if not isinstance(score, dict):
        payload["benchmark_score"] = None
        return
    records = _read_benchmark_records(output_dir)
    payload["benchmark_score"] = _benchmark_score(
        payload=payload,
        records=records,
        include_domains=include_domains,
        target=target,
    )


def _benchmark_score(
    *,
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    include_domains: list[str],
    target: _BenchmarkTarget | None,
) -> dict[str, Any]:
    if not records:
        return _empty_benchmark_score()
    dimensions = {
        "coverage": _coverage_dimension(payload, records, target),
        "cleanliness": _cleanliness_dimension(payload, records),
        "source_fidelity": _source_fidelity_dimension(payload, records, include_domains),
        "freshness": _freshness_dimension(records, target),
        "density": _density_dimension(payload, records),
    }
    weighted_score = 0.0
    for name, dimension in dimensions.items():
        weight = BENCHMARK_SCORE_WEIGHTS[name]
        dimension["weight"] = weight
        weighted_score += dimension["score"] * weight
    score = _clamp_score(round(weighted_score))
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "score": score,
        "grade": _benchmark_grade(score),
        "weights": BENCHMARK_SCORE_WEIGHTS,
        "dimensions": dimensions,
    }


def _aggregate_runs(
    runs: list[dict[str, Any]],
    *,
    name: str,
    workflow: str,
    output_dir: Path,
    runs_total: int,
) -> dict[str, Any]:
    """Synthesize a single case dict from N per-run case dicts.

    Headline fields (wall_seconds, pack_score.score, benchmark_score.score)
    are reported as medians across successful runs, with min/max alongside.
    The full per-run list is preserved under ``runs`` for raw inspection.
    """
    successful = [run for run in runs if run.get("status") != "failed"]
    # Headline wall time is the median across *successful* runs only; a run that
    # aborts quickly on a network error must not pull the reported latency down.
    wall_seconds_list = [float(run.get("wall_seconds") or 0.0) for run in (successful or runs)]
    estimated_costs = [float(run.get("estimated_cost_usd") or 0.0) for run in runs]
    artifact_sizes = [int(run.get("artifact_size_bytes") or 0) for run in runs]
    cache_sizes = [int(run.get("cache_size_bytes") or 0) for run in runs]
    rss_deltas = [float(run.get("rss_delta_mb") or 0.0) for run in runs]
    rss_baselines = [float(run.get("rss_baseline_mb") or 0.0) for run in runs]
    rss_peaks = [float(run.get("rss_peak_mb") or 0.0) for run in runs]

    case: dict[str, Any] = {
        "name": name,
        "workflow": workflow,
        "output_dir": str(output_dir),
        "wall_seconds": round(median(wall_seconds_list), 3),
        "wall_seconds_min": round(min(wall_seconds_list), 3),
        "wall_seconds_max": round(max(wall_seconds_list), 3),
        "wall_seconds_runs": [round(value, 3) for value in wall_seconds_list],
        "rss_baseline_mb": round(min(rss_baselines), 1) if rss_baselines else 0.0,
        "rss_peak_mb": round(max(rss_peaks), 1) if rss_peaks else 0.0,
        "rss_delta_mb": round(max(rss_deltas), 1) if rss_deltas else 0.0,
        "artifact_size_bytes": sum(artifact_sizes),
        "cache_size_bytes": sum(cache_sizes),
        "estimated_cost_usd": round(sum(estimated_costs), 6),
        "runs_total": runs_total,
        "runs_succeeded": len(successful),
        "runs": runs,
    }

    pack_scores = [
        int(run["pack_score"]["score"])
        for run in successful
        if isinstance(run.get("pack_score"), dict) and isinstance(run["pack_score"].get("score"), int)
    ]
    if pack_scores:
        med = _median_int(pack_scores)
        representative = next(
            run
            for run in successful
            if isinstance(run.get("pack_score"), dict) and int(run["pack_score"].get("score", -1)) == med
        )
        case["pack_score"] = {
            **representative["pack_score"],
            "score": med,
            "score_min": min(pack_scores),
            "score_max": max(pack_scores),
            "score_runs": pack_scores,
        }
    else:
        case["pack_score"] = None

    benchmark_scores = [
        int(run["benchmark_score"]["score"])
        for run in successful
        if isinstance(run.get("benchmark_score"), dict)
        and isinstance(run["benchmark_score"].get("score"), int)
    ]
    if benchmark_scores:
        med = _median_int(benchmark_scores)
        representative = next(
            run
            for run in successful
            if isinstance(run.get("benchmark_score"), dict)
            and int(run["benchmark_score"].get("score", -1)) == med
        )
        case["benchmark_score"] = {
            **representative["benchmark_score"],
            "score": med,
            "score_min": min(benchmark_scores),
            "score_max": max(benchmark_scores),
            "score_runs": benchmark_scores,
        }
    else:
        case["benchmark_score"] = None

    if not successful:
        case["status"] = "failed"
        first_error = next((run.get("error") for run in runs if run.get("error")), None)
        case["error"] = first_error or {
            "type": "BenchmarkError",
            "message": f"all {runs_total} runs failed",
        }

    if successful:
        first = successful[0]
        for key in ("stats", "skip_counts", "cost_units", "pack_metadata", "source_score_count"):
            if key in first:
                case[key] = first[key]

    return case


def _median_int(values: list[int]) -> int:
    """Lower-median: returns an actual element of ``values`` (no interpolation)."""
    ordered = sorted(values)
    return ordered[(len(ordered) - 1) // 2]


def _empty_benchmark_score() -> dict[str, Any]:
    """Floor the score when a pack has zero records.

    Without this, cleanliness/source_fidelity/freshness return 100 because they
    have nothing to penalize, leaving the weighted score around 50. An empty
    pack should read as failure, not a passing grade.
    """
    dimensions = {
        name: {"score": 0, "weight": weight, "signals": ["empty pack"]}
        for name, weight in BENCHMARK_SCORE_WEIGHTS.items()
    }
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "score": 0,
        "grade": _benchmark_grade(0),
        "weights": BENCHMARK_SCORE_WEIGHTS,
        "dimensions": dimensions,
    }


def _coverage_dimension(
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    target: _BenchmarkTarget | None,
) -> dict[str, Any]:
    signals: list[str] = []
    record_count = len(records)
    unique_urls = {str(record.get("url") or "") for record in records if record.get("url")}
    min_expected = target.min_expected_records if target else min(3, max(1, record_count))
    score = 100
    if record_count == 0:
        return _dimension(0, ["no records"])
    if len(unique_urls) < min_expected:
        missing = min_expected - len(unique_urls)
        score -= min(45, missing * 15)
        signals.append(f"{len(unique_urls)}/{min_expected} expected unique URLs")
    raw_metadata = payload.get("pack_metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    extract_errors = _optional_number(metadata.get("extract_error_count"))
    if extract_errors:
        score -= min(30, int(extract_errors) * 8)
        signals.append(f"{int(extract_errors)} extraction errors")
    search_count = _optional_number(metadata.get("search_result_count"))
    extract_count = _optional_number(metadata.get("extract_result_count"))
    if search_count and extract_count is not None and extract_count < min(search_count, min_expected):
        score -= min(20, int(min(search_count, min_expected) - extract_count) * 5)
        signals.append("fewer extracted docs than available search results")
    return _dimension(score, signals)


def _cleanliness_dimension(payload: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    signals: list[str] = []
    score = 100
    summary = _pack_summary(payload)
    duplicate_chunks = int(summary.get("duplicate_chunk_count") or 0)
    if duplicate_chunks:
        score -= min(25, duplicate_chunks * 5)
        signals.append(f"{duplicate_chunks} duplicate chunks")
    empty_records = sum(1 for record in records if not str(record.get("content") or "").strip())
    if empty_records:
        score -= min(40, empty_records * 10)
        signals.append(f"{empty_records} empty records")
    nav_hits = _boilerplate_hit_count(records)
    if nav_hits >= 8:
        score -= min(30, nav_hits)
        signals.append(f"{nav_hits} boilerplate/navigation hits")
    return _dimension(score, signals)


def _source_fidelity_dimension(
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    include_domains: list[str],
) -> dict[str, Any]:
    signals: list[str] = []
    score = 100
    expected = [_normalize_domain(domain) for domain in include_domains]
    urls = [str(record.get("url") or "") for record in records if record.get("url")]
    off_domain = [url for url in urls if expected and not _url_matches_domains(url, expected)]
    if off_domain:
        score -= min(40, len(set(off_domain)) * 12)
        signals.append(f"{len(set(off_domain))} off-domain URLs")
    raw_metadata = payload.get("pack_metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    selected_urls = metadata.get("selected_urls")
    selected = [str(url) for url in selected_urls if url] if isinstance(selected_urls, list) else []
    noisy_selected = [url for url in selected if "?" in url or "#" in url]
    if noisy_selected:
        score -= min(15, len(noisy_selected) * 4)
        signals.append(f"{len(noisy_selected)} selected URLs include query/fragment noise")
    if selected and len(set(selected)) < len(selected):
        score -= 10
        signals.append("duplicate selected URLs")
    return _dimension(score, signals)


def _freshness_dimension(records: list[dict[str, Any]], target: _BenchmarkTarget | None) -> dict[str, Any]:
    if not target or not target.freshness_terms:
        return _dimension(65, ["freshness not evaluated - no freshness terms configured"])
    haystack = "\n".join(
        " ".join(
            [
                str(record.get("url") or ""),
                str(record.get("title") or ""),
                str(record.get("content") or "")[:5000],
            ]
        ).lower()
        for record in records
    )
    matched = sorted({term for term in target.freshness_terms if term.lower() in haystack})
    if matched:
        return _dimension(100, [f"matched freshness terms: {', '.join(matched[:4])}"])
    return _dimension(65, ["freshness-sensitive target without freshness terms"])


def _density_dimension(payload: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    signals: list[str] = []
    summary = _pack_summary(payload)
    total_tokens = int(summary.get("total_tokens") or 0)
    record_count = len(records)
    if total_tokens <= 0 or record_count <= 0:
        return _dimension(0, ["no tokenized content"])
    score = 100
    tokens_per_record = total_tokens / record_count
    if tokens_per_record < 150:
        score -= 25
        signals.append(f"low average density: {tokens_per_record:.0f} tokens/record")
    if total_tokens > 250_000:
        score -= 35
        signals.append(f"very large pack: {total_tokens} tokens")
    elif total_tokens > 100_000:
        score -= 20
        signals.append(f"large pack: {total_tokens} tokens")
    nav_hits = _boilerplate_hit_count(records)
    if nav_hits >= 12:
        score -= min(20, nav_hits // 2)
        signals.append("navigation text may be inflating token load")
    return _dimension(score, signals)


def _dimension(score: int | float, signals: list[str]) -> dict[str, Any]:
    return {
        "score": _clamp_score(round(score)),
        "weight": None,
        "signals": signals,
    }


def _read_benchmark_records(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / "documents.ndjson"
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _pack_summary(payload: dict[str, Any]) -> dict[str, Any]:
    score = payload.get("pack_score")
    summary = score.get("summary") if isinstance(score, dict) else {}
    return summary if isinstance(summary, dict) else {}


def _boilerplate_hit_count(records: list[dict[str, Any]]) -> int:
    needles = (
        "skip to main content",
        "table of contents",
        "edit this page",
        "previous",
        "next",
        "on this page",
        "cookie",
        "subscribe",
        "sign in",
        "all rights reserved",
    )
    count = 0
    for record in records:
        content = str(record.get("content") or "").lower()
        count += sum(content.count(needle) for needle in needles)
    return count


def _url_matches_domains(url: str, expected_domains: list[str]) -> bool:
    domain = _domain_for_url(url)
    return bool(
        domain and any(domain == expected or domain.endswith(f".{expected}") for expected in expected_domains)
    )


def _optional_number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _benchmark_grade(score: int) -> str:
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "needs_review"
    return "poor"


def _write_provider_pack(
    *,
    output_dir: Path,
    provider: str,
    workflow: str,
    objective: str,
    queries: list[str],
    documents: list[_ProviderDocument],
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int,
    selected_urls: list[str],
    search_result_count: int,
    extract_result_count: int,
    extract_error_count: int,
    usage: dict[str, Any],
    response_metadata: dict[str, Any],
    cost_dollars: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = output_dir / "documents.ndjson"
    manifest = CorpusManifest(output_dir, output_format="ndjson")
    counter = TokenCounter()
    with ndjson_path.open("w", encoding="utf-8") as handle:
        for document in documents:
            record = DocumentRecord.from_page(
                url=document.url,
                title=document.title,
                content=document.content,
                metadata=document.metadata,
                extraction={
                    "provider": provider,
                    "workflow": workflow,
                },
                source_type=document.source_type,
                token_count=counter.count(document.content),
            )
            manifest.add_record(record, ndjson_path)
            handle.write(json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False))
            handle.write("\n")
    manifest_path = manifest.finalize()
    sources = [
        {
            "index": index,
            "url": document.url,
            "title": document.title,
            "source_type": document.source_type,
        }
        for index, document in enumerate(documents, start=1)
    ]
    sources_path = _write_provider_sources_md(output_dir, workflow=workflow, sources=sources)
    pack_path = output_dir / f"{provider}.pack.json"
    pack = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "provider": provider,
        "workflow": workflow,
        "objective": objective,
        "queries": queries,
        "record_count": len(documents),
        "item_count": len(documents),
        "search_result_count": search_result_count,
        "extract_result_count": extract_result_count,
        "extract_error_count": extract_error_count,
        "selected_urls": selected_urls,
        "request_options": {
            "source_policy": {"include_domains": include_domains},
            "max_search_results": max_search_results,
            "extract_limit": extract_limit,
            "content_policy": "provider_returned_text",
        },
        "usage": usage,
        "response_metadata": response_metadata,
        "artifacts": {
            "documents_ndjson": _relative_path(ndjson_path, output_dir),
            "manifest": _relative_path(manifest_path, output_dir),
            "sources_md": _relative_path(sources_path, output_dir),
            "pack_metadata": _relative_path(pack_path, output_dir),
        },
        "sources": sources,
    }
    if cost_dollars is not None:
        pack["cost_dollars"] = cost_dollars
    pack_path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return pack_path


def _write_provider_sources_md(
    output_dir: Path,
    *,
    workflow: str,
    sources: list[dict[str, Any]],
) -> Path:
    lines = [
        "# Context Pack Sources",
        "",
        f"Workflow: `{workflow}`",
        "",
        "## Sources",
        "",
    ]
    for source in sources:
        index = source.get("index")
        title = str(source.get("title") or source.get("url") or "Untitled")
        url = str(source.get("url") or "")
        lines.append(f"{index}. {_md_link(title, url)}")
        if source.get("source_type"):
            lines.append(f"   - Source type: `{source['source_type']}`")
        lines.append("   - Records file: `documents.ndjson`")
    path = output_dir / "sources.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _http_json_post(
    *,
    label: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    max_attempts: int = HTTP_RETRY_MAX_ATTEMPTS,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """POST JSON with bounded retry on transient HTTP/URL errors.

    Retries on 429/502/503/504 and URLError up to ``max_attempts`` total.
    Honors ``Retry-After`` (seconds) when present, capped at
    ``HTTP_RETRY_CAP_SECONDS``. Other 4xx, JSON errors, and non-https URLs
    raise immediately.
    """
    last_error: BenchmarkError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return _http_json_post_once(label=label, url=url, headers=headers, body=body, timeout=timeout)
        except _TransientHTTPError as err:
            last_error = BenchmarkError(str(err))
            last_error.__cause__ = err.__cause__
            if attempt >= max_attempts:
                break
            delay = _retry_delay_seconds(attempt=attempt, retry_after=err.retry_after)
            sleep(delay)
    if last_error is None:
        raise BenchmarkError(f"{label} request failed without a captured response error.")
    raise last_error


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse 3xx redirects on authenticated POSTs.

    urllib forwards ``Authorization`` / ``x-api-key`` across redirects (only
    ``content-*`` headers are stripped) and will follow an https->http
    downgrade, so a redirect from a provider endpoint would leak the API key
    in cleartext and could be steered at an internal host. These endpoints
    never legitimately redirect, so surface any 3xx as an error instead.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise HTTPError(req.full_url, code, f"Refused redirect to {newurl!r}", headers, fp)


def _http_json_post_once(
    *,
    label: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https":
        raise BenchmarkError(f"{label} URL must use HTTPS.")
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **headers,
        },
        method="POST",
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            raw_bytes = response.read(HTTP_MAX_RESPONSE_BYTES + 1)
    except HTTPError as err:
        detail = _redact_secret_like(err.read(HTTP_MAX_ERROR_BYTES).decode("utf-8", errors="replace"))
        message = f"{label} returned HTTP {err.code}: {_short_error_detail(detail)}"
        if err.code in HTTP_RETRY_TRANSIENT_STATUSES:
            transient = _TransientHTTPError(message, retry_after=_parse_retry_after(err))
            transient.__cause__ = err
            raise transient from err
        raise BenchmarkError(message) from err
    except URLError as err:
        message = f"{label} request failed: {err.reason}"
        transient = _TransientHTTPError(message, retry_after=None)
        transient.__cause__ = err
        raise transient from err
    if len(raw_bytes) > HTTP_MAX_RESPONSE_BYTES:
        raise BenchmarkError(f"{label} response exceeds {HTTP_MAX_RESPONSE_BYTES}-byte limit.")
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as err:
        raise BenchmarkError(f"{label} returned invalid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise BenchmarkError(f"{label} returned JSON {type(parsed).__name__}, expected object.")
    return parsed


class _TransientHTTPError(Exception):
    """Internal marker for retryable HTTP failures."""

    def __init__(self, message: str, *, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _parse_retry_after(err: HTTPError) -> float | None:
    raw = err.headers.get("Retry-After") if err.headers else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, HTTP_RETRY_CAP_SECONDS)


def _retry_delay_seconds(*, attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    base = min(HTTP_RETRY_CAP_SECONDS, 2.0 ** (attempt - 1))
    # Backoff jitter is not security-sensitive — stdlib random is fine here.
    return base + random.uniform(0.0, 0.5)  # nosec B311


def _require_benchmark_api_key(env_var: str, provider: str) -> str:
    value = _lookup_benchmark_secret(env_var)
    if not value:
        raise BenchmarkError(
            f"{provider} benchmark requires {env_var}. Store it in "
            "~/.config/docpull/secrets.env or export it in the environment."
        )
    return value


def _lookup_benchmark_secret(env_var: str) -> str | None:
    return lookup_api_key_env_var(env_var).value


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _select_result_urls(results: list[Any], limit: int) -> list[str]:
    selected: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url and url not in selected:
            selected.append(url)
        if len(selected) >= limit:
            break
    return selected


def _cost_dollars_total(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    total = value.get("total")
    if isinstance(total, int | float):
        return round(float(total), 6)
    return None


def _relative_path(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve()))
    except ValueError:
        return str(path)


_SECRET_LIKE_RE = re.compile(
    r"(?i)(?:bearer\s+|x-api-key\s*[:=]\s*|api[-_]?key\s*[\"':=]\s*|tvly-|exa_|sk-)[A-Za-z0-9._\-]{6,}"
)


def _redact_secret_like(value: str) -> str:
    """Strip token-shaped substrings out of a third-party error body.

    Provider error responses occasionally echo the submitted credential back;
    this keeps such tokens out of ``benchmark.report.json`` and any trace upload.
    """
    return _SECRET_LIKE_RE.sub("[redacted]", value)


def _short_error_detail(value: str) -> str:
    compact = " ".join(value.split())
    return compact[:500]


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [
        int(score["score"])
        for case in cases
        if isinstance((score := case.get("pack_score")), dict) and isinstance(score.get("score"), int)
    ]
    benchmark_scores = [
        int(score["score"])
        for case in cases
        if isinstance((score := case.get("benchmark_score")), dict) and isinstance(score.get("score"), int)
    ]
    total_estimated_cost = sum(float(case.get("estimated_cost_usd") or 0.0) for case in cases)
    total_parallel_cost = sum(
        float(case.get("estimated_cost_usd") or 0.0)
        for case in cases
        if str(case.get("workflow") or "").startswith("parallel-")
    )
    cache_only_cases = [_is_cache_only_case(case) for case in cases]
    targets = sorted({str(case.get("target_id")) for case in cases if case.get("target_id")})
    return {
        "case_count": len(cases),
        "target_count": len(targets),
        "targets": targets,
        "failed_case_count": sum(1 for case in cases if case.get("status") == "failed"),
        "best_pack_score": max(scores) if scores else None,
        "best_benchmark_score": max(benchmark_scores) if benchmark_scores else None,
        "matrix_case_count": sum(1 for case in cases if not _is_cache_only_case(case)),
        "total_estimated_live_cost_usd": round(total_estimated_cost, 6),
        "total_estimated_parallel_cost_usd": round(total_parallel_cost, 6),
        "cache_only_case_count": sum(cache_only_cases),
        "unscored_case_count": sum(
            1
            for case, cache_only in zip(cases, cache_only_cases, strict=True)
            if case.get("pack_score") is None and not cache_only
        ),
        "best_by_target": _best_by_target(cases),
        "pass_at_k": _pass_at_k_summary(cases, cache_only_cases),
    }


def _zero_dollar_completion(cases: list[dict[str, Any]]) -> dict[str, Any]:
    target_results: dict[str, dict[str, Any]] = {}
    for case in cases:
        if case.get("provider") != "docpull" or _is_cache_only_case(case):
            continue
        target_id = str(case.get("target_id") or "single")
        target = case.get("target")
        target = target if isinstance(target, dict) else {}
        min_expected = int(target.get("min_expected_records") or 1)
        route = str(target.get("zero_dollar_route") or "direct_http")
        record_count = _zero_dollar_record_count(case)
        score = _zero_dollar_score(case)
        signals = _zero_dollar_signals(case)
        completion_class, reason = _zero_dollar_classify_case(
            case,
            record_count=record_count,
            min_expected=min_expected,
            score=score,
            route=route,
        )
        target_results[target_id] = {
            "target_id": target_id,
            "target_label": target.get("label") or target_id,
            "target_kind": target.get("kind") or case.get("target_kind"),
            "target_url": target.get("url") or case.get("target_url"),
            "objective": target.get("objective") or "",
            "queries": target.get("queries") or [],
            "include_domains": target.get("include_domains") or [],
            "zero_dollar_route": route,
            "completion_class": completion_class,
            "reason": reason,
            "record_count": record_count,
            "min_expected_records": min_expected,
            "score": score,
            "signals": signals,
            "notes": target.get("notes") or "",
            "case": case.get("name"),
        }
    counts = Counter(str(result["completion_class"]) for result in target_results.values())
    complete_count = counts.get("complete_for_0", 0)
    local_browser_count = counts.get("complete_with_local_browser", 0)
    target_count = len(target_results)
    return {
        "schema_version": 1,
        "target_count": target_count,
        "counts": {
            class_name: counts[class_name] for class_name in ZERO_DOLLAR_CLASSES if counts.get(class_name)
        },
        "completion_rate": round(complete_count / target_count, 4) if target_count else None,
        "local_or_browser_rate": (
            round((complete_count + local_browser_count) / target_count, 4) if target_count else None
        ),
        "targets": [target_results[key] for key in sorted(target_results)],
        "next_actions": _zero_dollar_next_actions(target_results),
        "escalation_suggestions": _zero_dollar_escalation_suggestions(target_results),
        "classes": list(ZERO_DOLLAR_CLASSES),
    }


def _zero_dollar_record_count(case: dict[str, Any]) -> int:
    score = case.get("pack_score")
    summary = score.get("summary") if isinstance(score, dict) else {}
    if isinstance(summary, dict):
        raw_count = summary.get("record_count")
        if isinstance(raw_count, int):
            return raw_count
    stats = case.get("stats")
    if isinstance(stats, dict):
        fetched = stats.get("pages_fetched")
        if isinstance(fetched, int):
            return fetched
    return 0


def _zero_dollar_score(case: dict[str, Any]) -> int | None:
    benchmark_score = _score_value(case.get("benchmark_score"))
    if benchmark_score is not None:
        return benchmark_score
    return _score_value(case.get("pack_score"))


def _zero_dollar_signals(case: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    raw_score = case.get("benchmark_score")
    dimensions = raw_score.get("dimensions") if isinstance(raw_score, dict) else None
    if isinstance(dimensions, dict):
        for dimension_name, dimension in sorted(dimensions.items()):
            if not isinstance(dimension, dict):
                continue
            for signal in dimension.get("signals") or []:
                signals.append(f"{dimension_name}: {signal}")
    if case.get("status") == "failed":
        error = case.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("type") or "case failed")
        else:
            message = str(error or "case failed")
        signals.append(f"failure: {message}")
    return signals[:8]


def _zero_dollar_classify_case(
    case: dict[str, Any],
    *,
    record_count: int,
    min_expected: int,
    score: int | None,
    route: str,
) -> tuple[str, str]:
    if _zero_dollar_policy_blocked(case):
        return "blocked_by_policy", "core docpull was blocked by source or network policy"
    if case.get("status") == "failed":
        fallback = ZERO_DOLLAR_ROUTE_CLASSES.get(route, "partial_for_0")
        if fallback != "partial_for_0":
            return fallback, f"core docpull failed; target route is {route}"
        return "partial_for_0", "core docpull case failed"
    if record_count >= min_expected and (score is None or score >= ZERO_DOLLAR_COMPLETE_SCORE):
        score_text = f" with score {score}" if score is not None else ""
        return "complete_for_0", (
            f"core docpull produced {record_count}/{min_expected} expected record(s){score_text}"
        )
    fallback = ZERO_DOLLAR_ROUTE_CLASSES.get(route, "partial_for_0")
    if fallback == "complete_with_local_browser":
        return fallback, (
            f"direct HTTP was partial ({record_count}/{min_expected} records); "
            "target is a local-browser candidate"
        )
    if fallback == "requires_provider":
        return fallback, (
            f"direct HTTP was partial ({record_count}/{min_expected} records); "
            "target needs open-web/provider discovery"
        )
    if fallback == "requires_cloud_browser":
        return fallback, (
            f"direct HTTP was partial ({record_count}/{min_expected} records); "
            "target likely needs cloud browser infrastructure"
        )
    if fallback == "blocked_by_policy":
        return fallback, "target is marked as policy-blocked for local capture"
    score_text = f"; score {score} below {ZERO_DOLLAR_COMPLETE_SCORE}" if score is not None else ""
    return (
        "partial_for_0",
        f"core docpull produced {record_count}/{min_expected} expected record(s){score_text}",
    )


def _zero_dollar_policy_blocked(case: dict[str, Any]) -> bool:
    error = case.get("error")
    text = json.dumps(error, sort_keys=True).lower() if isinstance(error, dict) else str(error or "").lower()
    return any(
        needle in text
        for needle in (
            "blocked by policy",
            "blocked_by_policy",
            "robots",
            "private network",
            "url rejected",
            "403",
            "forbidden",
        )
    )


def _zero_dollar_next_actions(target_results: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "try_local_browser": [],
        "try_provider_discovery": [],
        "consider_cloud_browser": [],
        "improve_local_extraction": [],
        "review_policy": [],
    }
    for target_id, result in sorted(target_results.items()):
        class_name = result.get("completion_class")
        if class_name == "complete_with_local_browser":
            buckets["try_local_browser"].append(target_id)
        elif class_name == "requires_provider":
            buckets["try_provider_discovery"].append(target_id)
        elif class_name == "requires_cloud_browser":
            buckets["consider_cloud_browser"].append(target_id)
        elif class_name == "partial_for_0":
            buckets["improve_local_extraction"].append(target_id)
        elif class_name == "blocked_by_policy":
            buckets["review_policy"].append(target_id)
    return {key: value for key, value in buckets.items() if value}


def _zero_dollar_escalation_suggestions(target_results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    suggestions: list[dict[str, Any]] = []
    for target_id, result in sorted(target_results.items()):
        class_name = str(result.get("completion_class") or "")
        if class_name == "complete_for_0":
            continue
        if class_name == "complete_with_local_browser":
            suggestions.append(_local_browser_escalation(target_id, result))
        elif class_name == "requires_provider":
            suggestions.append(_provider_escalation(target_id, result))
        elif class_name == "requires_cloud_browser":
            suggestions.append(_cloud_browser_escalation(target_id, result))
        elif class_name == "partial_for_0":
            suggestions.append(_local_discovery_escalation(target_id, result))
        elif class_name == "blocked_by_policy":
            suggestions.append(_policy_escalation(target_id, result))
    return suggestions


def _local_browser_escalation(target_id: str, result: dict[str, Any]) -> dict[str, Any]:
    url = _target_url(result)
    return {
        "target_id": target_id,
        "action": "try_local_browser",
        "summary": "Retry with local agent-browser rendering before using paid providers or cloud sandboxes.",
        "commands": [
            _command(
                "docpull",
                url,
                "--single",
                "--render",
                "fallback",
                "--budget",
                "0",
                "-o",
                f"./packs/{target_id}-rendered",
            )
        ],
        "estimated_paid_request_count": 0,
        "estimated_paid_cost_usd": 0.0,
        "paid_capable": False,
    }


def _local_discovery_escalation(target_id: str, result: dict[str, Any]) -> dict[str, Any]:
    url = _target_url(result)
    discovery_dir = f"./packs/{target_id}-discovery"
    return {
        "target_id": target_id,
        "action": "improve_local_discovery",
        "summary": "Scan provider-free site hints, then fetch selected candidates.",
        "commands": [
            _command("docpull", "discover", "scan", url, "--source", "all", "-o", discovery_dir),
            _command(
                "docpull",
                "discover",
                "fetch",
                discovery_dir,
                "--select",
                "top:10",
                "-o",
                f"./packs/{target_id}-local",
            ),
        ],
        "estimated_paid_request_count": 0,
        "estimated_paid_cost_usd": 0.0,
        "paid_capable": False,
    }


def _provider_escalation(target_id: str, result: dict[str, Any]) -> dict[str, Any]:
    objective = _target_objective(result)
    query = _target_query(result)
    include_domains = [str(item) for item in result.get("include_domains") or [] if str(item).strip()]
    dry_run_command = [
        "docpull",
        "providers",
        "context-pack",
        objective,
        "--dry-run",
        "--json",
        "--max-estimated-cost",
        f"{_provider_escalation_cost():.6f}",
    ]
    live_command = [
        "docpull",
        "providers",
        "context-pack",
        objective,
        "--max-estimated-cost",
        f"{_provider_escalation_cost():.6f}",
        "-o",
        f"./packs/{target_id}-provider",
    ]
    for provider in ESCALATION_PROVIDER_ORDER:
        dry_run_command.extend(["--provider", provider])
        live_command.extend(["--provider", provider])
    if query:
        dry_run_command.extend(["--query", query])
        live_command.extend(["--query", query])
    for domain in include_domains:
        dry_run_command.extend(["--include-domain", domain])
        live_command.extend(["--include-domain", domain])
    return {
        "target_id": target_id,
        "action": "try_provider_discovery",
        "summary": "Use BYOK providers only after reviewing a dry-run plan and cost guard.",
        "commands": [_command(*dry_run_command), _command(*live_command)],
        "estimated_paid_request_count": len(ESCALATION_PROVIDER_ORDER),
        "estimated_paid_cost_usd": _provider_escalation_cost(),
        "estimated_paid_cost_breakdown": _provider_escalation_cost_breakdown(),
        "paid_capable": True,
    }


def _cloud_browser_escalation(target_id: str, result: dict[str, Any]) -> dict[str, Any]:
    url = _target_url(result)
    costs = _cloud_escalation_cost_breakdown()
    vercel_cost = costs["vercel-sandbox"]
    e2b_cost = costs["e2b-sandbox"]
    return {
        "target_id": target_id,
        "action": "consider_cloud_browser",
        "summary": "Use cloud rendering only after local render fails or local infrastructure is unsuitable.",
        "commands": [
            _command(
                "docpull",
                "render",
                url,
                "--runtime",
                "local",
                "--budget",
                "0",
                "-o",
                f"./packs/{target_id}-local-render",
            ),
            _command(
                "docpull",
                "render",
                url,
                "--runtime",
                "vercel",
                "--cloud-max-estimated-cost",
                f"{vercel_cost:.4f}",
                "-o",
                f"./packs/{target_id}-vercel-render",
            ),
            _command(
                "docpull",
                "render",
                url,
                "--runtime",
                "e2b",
                "--cloud-max-estimated-cost",
                f"{e2b_cost:.4f}",
                "-o",
                f"./packs/{target_id}-e2b-render",
            ),
        ],
        "estimated_paid_request_count": 1,
        "estimated_paid_cost_usd": max(costs.values()),
        "estimated_paid_cost_breakdown": costs,
        "paid_capable": True,
    }


def _policy_escalation(target_id: str, _result: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "action": "review_policy",
        "summary": "Review policy/robots/private-network constraints before retrying capture.",
        "commands": [_command("docpull", "policy", "explain", "./docpull.policy.yml")],
        "estimated_paid_request_count": 0,
        "estimated_paid_cost_usd": 0.0,
        "paid_capable": False,
    }


def _target_url(result: dict[str, Any]) -> str:
    url = str(result.get("target_url") or "").strip()
    return url or "https://example.com"


def _target_objective(result: dict[str, Any]) -> str:
    objective = str(result.get("objective") or "").strip()
    if objective:
        return objective
    label = str(result.get("target_label") or result.get("target_id") or "target").strip()
    return f"Build an evidence pack for {label}"


def _target_query(result: dict[str, Any]) -> str:
    queries = result.get("queries")
    if isinstance(queries, list):
        for query in queries:
            clean = str(query).strip()
            if clean:
                return clean
    return ""


def _provider_escalation_cost_breakdown() -> dict[str, float]:
    tavily_cost = APPROX_TAVILY_CREDIT_USD * (1 + ESCALATION_EXTRACT_LIMIT)
    return {
        "tavily": round(tavily_cost, 6),
        "exa": APPROX_EXA_SEARCH_USD,
        "parallel": estimate_context_pack_cost(
            extract_limit=ESCALATION_EXTRACT_LIMIT,
            max_search_results=ESCALATION_MAX_SEARCH_RESULTS,
        ),
    }


def _provider_escalation_cost() -> float:
    return round(sum(_provider_escalation_cost_breakdown().values()), 6)


def _cloud_escalation_cost_breakdown() -> dict[str, float]:
    vercel_config = RenderConfig(mode="agent-browser", backend="vercel-sandbox")
    e2b_config = RenderConfig(mode="agent-browser", backend="e2b-sandbox")
    return {
        "vercel-sandbox": estimate_cloud_render_cost_usd("vercel-sandbox", vercel_config),
        "e2b-sandbox": estimate_cloud_render_cost_usd("e2b-sandbox", e2b_config),
    }


def _command(*parts: str) -> str:
    return " ".join(shlex.quote(str(part)) for part in parts)


def _pass_at_k_summary(
    cases: list[dict[str, Any]],
    cache_only_cases: list[bool],
) -> dict[str, Any]:
    """Compute pass^k for ``pack_score`` and ``benchmark_score``.

    pass^k = fraction of cases whose *worst* trial meets the threshold. The
    Anthropic "Demystifying evals" post argues this is the right framing when
    consistency matters ("users expect reliable behavior every time"). Median
    tells you the typical run; pass^k tells you how often a case is reliably
    above bar. Cache-only cases are excluded — they're not scored.
    """
    scored_cases = [case for case, cache_only in zip(cases, cache_only_cases, strict=True) if not cache_only]
    if not scored_cases:
        return {"k": 0, "thresholds": list(PASS_AT_K_THRESHOLDS), "results": {}}
    results: dict[str, list[dict[str, Any]]] = {}
    k = 0
    for score_key in ("pack_score", "benchmark_score"):
        per_threshold: list[dict[str, Any]] = []
        for threshold in PASS_AT_K_THRESHOLDS:
            block = pass_at_k(scored_cases, score_key=score_key, threshold=threshold)
            k = max(k, block["k"])
            per_threshold.append(
                {
                    "threshold": threshold,
                    "cases_total": block["cases_total"],
                    "cases_passed": block["cases_passed"],
                    "rate": round(block["rate"], 4),
                    "by_provider": block["by_provider"],
                }
            )
        results[score_key] = per_threshold
    return {"k": k, "thresholds": list(PASS_AT_K_THRESHOLDS), "results": results}


def _best_by_target(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for case in cases:
        if _is_cache_only_case(case):
            continue
        target_id = str(case.get("target_id") or "")
        score = case.get("benchmark_score")
        if not target_id or not isinstance(score, dict) or not isinstance(score.get("score"), int):
            continue
        current = best.get(target_id)
        if not current or int(score["score"]) > int(current["score"]):
            best[target_id] = {
                "case": case.get("name"),
                "provider": case.get("provider"),
                "workflow": case.get("workflow"),
                "score": score["score"],
            }
    return best


def _format_skipped_providers(skipped: list[Any]) -> str:
    values: list[str] = []
    for item in skipped:
        if not isinstance(item, dict):
            continue
        provider = str(item.get("provider") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if provider and reason:
            values.append(f"{provider} ({reason})")
        elif provider:
            values.append(provider)
    return ", ".join(values) if values else "none"


def _runs_disclosure_lines(report: dict[str, Any]) -> list[str]:
    runs = report.get("runs_per_case")
    if not isinstance(runs, int) or runs <= 1:
        return []
    return [
        (
            f"- Repetition: each cell ran `N={runs}` times. Headline wall seconds "
            "and scores are the median across runs, with `[min–max]` shown "
            "inline. Per-run artifacts live under `run-1/`, `run-2/`, ... "
            "subdirs alongside the case."
        ),
    ]


def _workload_disclosure_lines(report: dict[str, Any]) -> list[str]:
    """Render a per-workflow workload table so readers can see the comparison terms."""
    cases = [case for case in report.get("cases", []) if isinstance(case, dict)]
    by_workflow: dict[str, dict[str, Any]] = {}
    workflow_order: list[str] = []
    for case in cases:
        if _is_cache_only_case(case):
            continue
        workflow = str(case.get("workflow") or "")
        if not workflow:
            continue
        if workflow not in by_workflow:
            workflow_order.append(workflow)
            by_workflow[workflow] = {
                "settings": case.get("settings") or {},
                "records": [],
            }
        score = case.get("pack_score")
        if isinstance(score, dict):
            summary = score.get("summary")
            if isinstance(summary, dict):
                value = summary.get("record_count")
                if isinstance(value, int):
                    by_workflow[workflow]["records"].append(value)
    if not by_workflow:
        return []
    lines = [
        "## Workload disclosure",
        "",
        (
            "The five workflows are not the same job. The core crawl walks a "
            "page graph from a seed URL; provider workflows fetch a fixed "
            "number of search results and optionally extract their content. "
            "Compare scores within a row of the heatmap (same workflow across "
            "targets), not down a column."
        ),
        "",
        "| Workflow | Settings | Median records | Records range |",
        "| --- | --- | ---: | --- |",
    ]
    setting_keys = (
        "max_pages",
        "max_depth",
        "max_concurrent",
        "max_search_results",
        "extract_limit",
        "mode",
    )
    for workflow in workflow_order:
        info = by_workflow[workflow]
        settings = info["settings"] if isinstance(info["settings"], dict) else {}
        rendered_settings = ", ".join(f"{key}={settings[key]}" for key in setting_keys if key in settings)
        records = info["records"]
        med_text: str
        range_text: str
        if records:
            med_text = str(_median_int(records))
            range_text = f"{min(records)}–{max(records)}" if min(records) != max(records) else str(records[0])
        else:
            med_text = ""
            range_text = ""
        lines.append(f"| `{workflow}` | {rendered_settings or '—'} | {med_text} | {range_text} |")
    lines.append("")
    return lines


def _raindrop_trace_lines(trace: dict[str, Any]) -> list[str]:
    lines = [
        f"- Raindrop trace: `{trace.get('provider', 'none')}` / `{trace.get('status', 'disabled')}`",
    ]
    if trace.get("event_id"):
        lines.append(f"- Raindrop event id: `{trace['event_id']}`")
    if trace.get("enabled"):
        lines.append(
            "- Raindrop signals: "
            f"`{int(trace.get('signal_count') or 0)}` total, "
            f"`{int(trace.get('positive_signal_count') or 0)}` positive, "
            f"`{int(trace.get('negative_signal_count') or 0)}` negative."
        )
        signal_names = trace.get("signal_names")
        if isinstance(signal_names, dict) and signal_names:
            rendered = ", ".join(f"{name}={count}" for name, count in sorted(signal_names.items()))
            lines.append(f"- Raindrop signal names: `{rendered}`")
    return lines


def _markdown_report(report: dict[str, Any]) -> str:
    raw_targets = report.get("targets")
    targets: list[Any] = raw_targets if isinstance(raw_targets, list) else []
    target_label = (
        f"{len(targets)} targets (`{report.get('target_set', 'single')}`)"
        if len(targets) > 1
        else f"`{report['target_url']}`"
    )
    lines = [
        "# docpull Benchmark Summary",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Target: {target_label}",
        f"Run directory: `{report['run_dir']}`",
        "",
        "## Cases",
        "",
        (
            "| Target | Case | Workflow | Wall seconds | Benchmark score | "
            "Pack score | Records | Estimated cost |"
        ),
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for case in report["cases"]:
        score = case.get("pack_score")
        if isinstance(score, dict):
            score_value = str(score.get("score", ""))
        elif _is_cache_only_case(case):
            score_value = "cache skip"
        else:
            score_value = ""
        if isinstance(score, dict):
            summary = score.get("summary")
            summary = summary if isinstance(summary, dict) else {}
            record_count = summary.get("record_count", "")
        elif _is_cache_only_case(case):
            stats = case.get("stats") if isinstance(case.get("stats"), dict) else {}
            record_count = f"0 fetched / {stats.get('pages_skipped', 0)} skipped"
        else:
            record_count = ""
        estimated_cost = case.get("estimated_cost_usd", "")
        cost_text = f"${estimated_cost:.6f}" if isinstance(estimated_cost, float) else ""
        benchmark_score = _case_benchmark_score_text(case)
        lines.append(
            "| "
            f"`{case.get('target_id', '')}` | "
            f"`{case['name']}` | "
            f"`{case.get('workflow', '')}` | "
            f"{_case_wall_seconds_text(case)} | "
            f"{benchmark_score} | "
            f"{score_value} | "
            f"{record_count} | "
            f"{cost_text} |"
        )
    heatmap = _matrix_heatmap_markdown(report)
    if heatmap:
        lines.extend(["", "## Provider x Target Heatmap", "", *heatmap])
    skipped = report.get("skipped_providers")
    skipped = skipped if isinstance(skipped, list) else []
    raw_trace = report.get("trace")
    trace: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Cases: {report['summary']['case_count']}",
            f"- Targets: {report['summary'].get('target_count', 1)}",
            f"- Failed cases: {report['summary'].get('failed_case_count', 0)}",
            f"- Best benchmark score: {report['summary'].get('best_benchmark_score')}",
            f"- Best pack score: {report['summary']['best_pack_score']}",
            f"- Cache-only cases: {report['summary']['cache_only_case_count']}",
            f"- Skipped providers: {_format_skipped_providers(skipped)}",
            (
                "- Total estimated live provider cost: "
                f"${report['summary'].get('total_estimated_live_cost_usd', 0):.6f}"
            ),
            *_raindrop_trace_lines(trace),
            "",
        ]
    )
    lines.extend(_pass_at_k_lines(report))
    lines.extend(_zero_dollar_completion_lines(report))
    return "\n".join(lines)


def _zero_dollar_completion_lines(report: dict[str, Any]) -> list[str]:
    completion = report.get("zero_dollar_completion")
    if not isinstance(completion, dict):
        return []
    counts = completion.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    rendered_counts = ", ".join(f"{key}={value}" for key, value in counts.items()) or "none"
    lines = [
        "",
        "## Zero-Dollar Completion",
        "",
        f"- Targets classified: `{completion.get('target_count', 0)}`",
        f"- Counts: `{rendered_counts}`",
        f"- Direct local completion rate: `{completion.get('completion_rate')}`",
        f"- Local-or-browser completion rate: `{completion.get('local_or_browser_rate')}`",
    ]
    next_actions = completion.get("next_actions")
    if isinstance(next_actions, dict) and next_actions:
        lines.extend(["", "Next actions:"])
        for action, targets in sorted(next_actions.items()):
            if isinstance(targets, list) and targets:
                lines.append(f"- `{action}`: {', '.join(f'`{target}`' for target in targets)}")
    suggestions = completion.get("escalation_suggestions")
    if isinstance(suggestions, list) and suggestions:
        lines.extend(
            [
                "",
                "Escalation suggestions:",
                "",
                "| Target | Action | Paid requests | Estimated paid cost | First command |",
                "| --- | --- | ---: | ---: | --- |",
            ]
        )
        for suggestion in suggestions:
            if not isinstance(suggestion, dict):
                continue
            commands = suggestion.get("commands")
            first_command = commands[0] if isinstance(commands, list) and commands else ""
            paid_cost = suggestion.get("estimated_paid_cost_usd")
            paid_cost_text = f"${paid_cost:.6f}" if isinstance(paid_cost, int | float) else ""
            lines.append(
                "| "
                f"`{suggestion.get('target_id', '')}` | "
                f"`{suggestion.get('action', '')}` | "
                f"{suggestion.get('estimated_paid_request_count', 0)} | "
                f"{paid_cost_text} | "
                f"`{_escape_markdown_table(str(first_command))}` |"
            )
    targets = completion.get("targets")
    if isinstance(targets, list) and targets:
        lines.extend(
            [
                "",
                "| Target | Class | Route | Records | Score | Reason |",
                "| --- | --- | --- | ---: | ---: | --- |",
            ]
        )
        for target in targets:
            if not isinstance(target, dict):
                continue
            lines.append(
                "| "
                f"`{target.get('target_id', '')}` | "
                f"`{target.get('completion_class', '')}` | "
                f"`{target.get('zero_dollar_route', '')}` | "
                f"{target.get('record_count', '')}/{target.get('min_expected_records', '')} | "
                f"{target.get('score') if target.get('score') is not None else ''} | "
                f"{_escape_markdown_table(str(target.get('reason') or ''))} |"
            )
    return lines


def _escape_markdown_table(value: str) -> str:
    return value.replace("|", "\\|")


def _pass_at_k_lines(report: dict[str, Any]) -> list[str]:
    summary = report.get("summary")
    if not isinstance(summary, dict):
        return []
    block = summary.get("pass_at_k")
    if not isinstance(block, dict):
        return []
    k = block.get("k")
    if not isinstance(k, int) or k < 2:
        return []
    results = block.get("results")
    if not isinstance(results, dict) or not results:
        return []
    thresholds = block.get("thresholds") or list(PASS_AT_K_THRESHOLDS)
    lines = [
        "",
        f"## Reliability (pass^{k})",
        "",
        (
            f"Fraction of cases whose **worst** of {k} trials meets the threshold. "
            "Stricter than the headline median: a case only counts as passing if "
            "every run cleared the bar."
        ),
        "",
        "| Score | " + " | ".join(f"@{t}" for t in thresholds) + " | n |",
        "| --- | " + " | ".join(["---:"] * len(thresholds)) + " | ---: |",
    ]
    for score_key, rows in results.items():
        if not isinstance(rows, list) or not rows:
            continue
        by_threshold = {row["threshold"]: row for row in rows if isinstance(row, dict)}
        cells: list[str] = []
        total = 0
        for threshold in thresholds:
            row = by_threshold.get(threshold)
            if not row:
                cells.append("—")
                continue
            total = max(total, int(row.get("cases_total") or 0))
            rate = float(row.get("rate") or 0.0)
            cells.append(f"{rate:.1%} ({row.get('cases_passed')}/{row.get('cases_total')})")
        lines.append(f"| `{score_key}` | " + " | ".join(cells) + f" | {total} |")
    lines.append("")
    return lines


def _case_benchmark_score_text(case: dict[str, Any]) -> str:
    if case.get("status") == "failed":
        return "failed"
    score = case.get("benchmark_score")
    if isinstance(score, dict) and isinstance(score.get("score"), int):
        text = str(score["score"])
        score_min = score.get("score_min")
        score_max = score.get("score_max")
        if isinstance(score_min, int) and isinstance(score_max, int) and score_min != score_max:
            text += f" [{score_min}–{score_max}]"
        return text
    if _is_cache_only_case(case):
        return "cache skip"
    return ""


def _case_wall_seconds_text(case: dict[str, Any]) -> str:
    seconds = case.get("wall_seconds")
    if seconds is None:
        return ""
    text = str(seconds)
    seconds_min = case.get("wall_seconds_min")
    seconds_max = case.get("wall_seconds_max")
    if (
        isinstance(seconds_min, int | float)
        and isinstance(seconds_max, int | float)
        and seconds_min != seconds_max
    ):
        text += f" [{seconds_min}–{seconds_max}]"
    return text


def _case_provider_key(case: dict[str, Any]) -> str:
    workflow = str(case.get("workflow") or "")
    if workflow == "core-llm":
        return "docpull-core"
    if workflow == "parallel-search-pack":
        return "parallel-search"
    if workflow == "parallel-context-pack":
        return "parallel-context"
    if workflow == "tavily-search-extract-pack":
        return "tavily-search-extract"
    if workflow == "exa-search-contents-pack":
        return "exa-search-contents"
    return workflow or str(case.get("provider") or "unknown")


def _matrix_heatmap_markdown(report: dict[str, Any]) -> list[str]:
    cases = [case for case in report.get("cases", []) if isinstance(case, dict)]
    if len({case.get("target_id") for case in cases if case.get("target_id")}) <= 1:
        return []
    targets = [target for target in report.get("targets", []) if isinstance(target, dict)]
    target_ids = [str(target.get("id")) for target in targets if target.get("id")]
    if not target_ids:
        target_ids = sorted({str(case.get("target_id")) for case in cases if case.get("target_id")})
    provider_keys = _matrix_columns(cases)
    if not provider_keys:
        return []
    by_cell: dict[tuple[str, str], str] = {}
    for case in cases:
        if _is_cache_only_case(case):
            continue
        target_id = str(case.get("target_id") or "")
        provider_key = _case_provider_key(case)
        score = _case_benchmark_score_text(case)
        if target_id and provider_key and score:
            by_cell[(target_id, provider_key)] = score
    lines = [
        "| Target | " + " | ".join(f"`{provider}`" for provider in provider_keys) + " |",
        "| --- | " + " | ".join("---:" for _provider in provider_keys) + " |",
    ]
    for target_id in target_ids:
        cells = [by_cell.get((target_id, provider), "") for provider in provider_keys]
        lines.append(f"| `{target_id}` | " + " | ".join(cells) + " |")
    return lines


def _matrix_columns(cases: list[dict[str, Any]]) -> list[str]:
    preferred = [
        "docpull-core",
        "parallel-search",
        "parallel-context",
        "tavily-search-extract",
        "exa-search-contents",
    ]
    present = {_case_provider_key(case) for case in cases if not _is_cache_only_case(case)}
    ordered = [provider for provider in preferred if provider in present]
    ordered.extend(sorted(present - set(ordered)))
    return ordered


def _article_markdown(report: dict[str, Any], *, title: str) -> str:
    cases = [case for case in report.get("cases", []) if isinstance(case, dict)]
    best_case = _best_scored_case(cases, score_key="benchmark_score")
    fastest_case = min(cases, key=lambda item: float(item.get("wall_seconds") or 0.0)) if cases else None
    raw_trace = report.get("trace")
    trace: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}
    raw_summary = report.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    raw_artifacts = report.get("artifacts")
    artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    raw_providers = report.get("providers")
    providers = (
        ", ".join(str(provider) for provider in raw_providers) if isinstance(raw_providers, list) else "core"
    )
    targets = report.get("targets")
    targets = targets if isinstance(targets, list) else []
    skipped = report.get("skipped_providers")
    skipped = skipped if isinstance(skipped, list) else []
    heatmap = _matrix_heatmap_markdown(report)
    lines = [
        f"# {title}",
        "",
        (
            "We benchmarked DocPull's local LLM-profile crawler against Parallel Search, "
            "Parallel Context, Tavily Search + Extract, and Exa Search Contents across a "
            "provider-target matrix, with Raindrop as the metadata-only observability layer."
        ),
        "",
        "## Methodology",
        "",
        f"- Target set: `{report.get('target_set', 'single')}`",
        f"- Targets: `{len(targets) or 1}`",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Run directory: `{report.get('run_dir')}`",
        f"- Providers: `{providers}`",
        f"- Matrix providers: `{', '.join(str(value) for value in report.get('matrix_providers', []))}`",
        f"- Skipped providers: `{_format_skipped_providers(skipped)}`",
        f"- Parallel enabled: `{bool(report.get('parallel_enabled'))}`",
        *_raindrop_trace_lines(trace),
        (
            "- Trace content policy: metadata only. The benchmark records timings, "
            "counts, scores, costs, selected URLs, and artifact paths; it does not "
            "ship scraped document text by default."
        ),
        (
            "- Weighted score: coverage 30%, cleanliness 20%, source fidelity 20%, "
            "freshness 15%, and density 15%. Weights are heuristic — the sub-score "
            "signals are the load-bearing detail."
        ),
        (
            "- Boilerplate detection (used inside the cleanliness and density "
            "dimensions) is a substring sniff on English navigation phrases; "
            "it will under-report localized boilerplate."
        ),
        (
            "- Freshness is a presence test for target-specific terms in URL, "
            "title, or first 5000 characters of body; it does not check page "
            "modification time."
        ),
        *_runs_disclosure_lines(report),
        "",
        *_workload_disclosure_lines(report),
        "## Targets",
        "",
    ]
    for target in targets:
        if not isinstance(target, dict):
            continue
        notes = f" — {target.get('notes')}" if target.get("notes") else ""
        lines.append(
            f"- `{target.get('id')}`: {target.get('label')} (`{target.get('url')}`), "
            f"{target.get('kind')}{notes}"
        )
    if not targets:
        lines.append(f"- `single`: `{report.get('target_url')}`")
    lines.extend(
        [
            "",
            "## Results",
            "",
            (
                "| Target | Case | Workflow | Wall seconds | Benchmark score | "
                "Pack score | Records | Estimated cost |"
            ),
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for case in cases:
        score = case.get("pack_score")
        if isinstance(score, dict):
            score_value = score.get("score")
            score_summary = score.get("summary")
            record_count = score_summary.get("record_count", "") if isinstance(score_summary, dict) else ""
        elif _is_cache_only_case(case):
            score_value = "cache skip"
            stats = case.get("stats")
            stats = stats if isinstance(stats, dict) else {}
            record_count = f"0 fetched / {stats.get('pages_skipped', 0)} skipped"
        else:
            score_value = ""
            record_count = ""
        estimated_cost = case.get("estimated_cost_usd")
        cost_text = (
            f"${estimated_cost:.6f}" if isinstance(estimated_cost, float) else _case_cost_unit_text(case)
        )
        lines.append(
            "| "
            f"`{case.get('target_id', '')}` | "
            f"`{case.get('name')}` | "
            f"`{case.get('workflow')}` | "
            f"{_case_wall_seconds_text(case)} | "
            f"{_case_benchmark_score_text(case)} | "
            f"{score_value} | "
            f"{record_count} | "
            f"{cost_text} |"
        )
    if heatmap:
        lines.extend(
            [
                "",
                "## Provider x Target Heatmap",
                "",
                (
                    "Read across rows (one target, all providers), not down columns. "
                    "The five workflows are not equivalent jobs: the core crawl walks a "
                    "page graph from a known seed URL, while provider workflows run a "
                    "search query and optionally extract a small number of results. "
                    "A provider that returns zero search results for a lesser-known site "
                    "scores 0 — not because its extractor is weak, but because its index "
                    "doesn't cover that site. See Workload disclosure above."
                ),
                "",
                *heatmap,
            ]
        )
    lines.extend(
        [
            "",
            "## What Stood Out",
            "",
        ]
    )
    if best_case:
        best_score = best_case["benchmark_score"]["score"]
        lines.append(f"- Best weighted benchmark score: `{best_case['name']}` at `{best_score}/100`.")
    if fastest_case:
        lines.append(
            f"- Fastest case: `{fastest_case.get('name')}` at `{fastest_case.get('wall_seconds')}` seconds."
        )
    total_cost = summary.get(
        "total_estimated_live_cost_usd", summary.get("total_estimated_parallel_cost_usd", 0)
    )
    lines.append(f"- Estimated normalized live provider cost for this run: `${float(total_cost):.6f}`.")
    failed_count = int(summary.get("failed_case_count") or 0)
    if failed_count:
        lines.append(f"- Failed provider-target cells were preserved in the matrix: `{failed_count}`.")
    cost_normalization = report.get("cost_normalization")
    if isinstance(cost_normalization, dict):
        tavily_norm = cost_normalization.get("tavily")
        if isinstance(tavily_norm, dict) and tavily_norm.get("credit_usd") is None:
            lines.append(
                f"- Tavily credits were captured but not converted to dollars. Set `{TAVILY_CREDIT_USD_ENV}` "
                "or pass `--tavily-credit-usd` for dollar-for-dollar comparisons."
            )
    if skipped:
        lines.append(
            "- Missing or unavailable providers were skipped without failing the run: "
            f"{_format_skipped_providers(skipped)}."
        )
    if trace.get("enabled"):
        lines.append(
            "- Raindrop tracing was enabled, so each case was emitted as a tool trace and "
            "attention-worthy cells were promoted into Raindrop signals."
        )
    else:
        lines.append(
            "- Raindrop tracing was not enabled in this run. Re-run with `--trace raindrop` and "
            "`RAINDROP_WRITE_KEY` to publish observed spans alongside the report."
        )
    lines.extend(
        [
            "",
            "## Why This Is Useful",
            "",
            (
                "The target matrix creates provider-by-target variance in one run, and the weighted "
                "sub-scores make that variance visible before it reaches the headline score. "
                "Raindrop turns those cells into traces and signals that can be compared over time."
            ),
            "",
            "Raindrop is still not the judge or retriever. It is the trace layer around the eval: "
            "each case can be filtered by provider, workflow, target, prompt, settings, latency, "
            "cost, selected URLs, and score dimensions. Repeated scheduled runs turn those fields "
            "into drift and regression signals.",
            "",
            "## Reproduce",
            "",
            "```bash",
            "pip install parallel-web raindrop-ai",
            "export PARALLEL_API_KEY='<parallel-key>'",
            "export TAVILY_API_KEY='<tavily-key>'",
            "export TAVILY_CREDIT_USD='<account-credit-value>'",
            "export EXA_API_KEY='<exa-key>'",
            "export RAINDROP_WRITE_KEY='<raindrop-write-key>'",
            (
                "docpull benchmark quick --target-set provider-matrix --provider all --trace raindrop "
                "--max-pages 8 --max-depth 1 --max-search-results 5 --extract-limit 2 "
                "--max-estimated-cost 0.10"
            ),
            "docpull benchmark article .bench/runs/<run>/benchmark.report.json",
            "```",
            "",
            "## Crawl Policy",
            "",
            (
                "The provider matrix keeps page caps low and runs on a spaced schedule. Public docs and "
                "pricing pages should still be treated as someone else's infrastructure: respect "
                "robots.txt, keep concurrency conservative, and avoid tight repeated runs."
            ),
            "",
            "## Artifacts",
            "",
            f"- Config: `{artifacts.get('config')}`",
            f"- JSON report: `{artifacts.get('json')}`",
            f"- Summary: `{artifacts.get('markdown')}`",
            "",
        ]
    )
    return "\n".join(lines)


def _case_cost_unit_text(case: dict[str, Any]) -> str:
    units = case.get("cost_units")
    if isinstance(units, dict) and units.get("unit") == "credit":
        return f"{float(units.get('total_credits') or 0):.3f} credits"
    return "n/a"


def _legacy_article_markdown(report: dict[str, Any], *, title: str) -> str:
    cases = [case for case in report.get("cases", []) if isinstance(case, dict)]
    best_case = _best_scored_case(cases)
    fastest_case = min(cases, key=lambda item: float(item.get("wall_seconds") or 0.0)) if cases else None
    raw_trace = report.get("trace")
    trace: dict[str, Any] = raw_trace if isinstance(raw_trace, dict) else {}
    raw_summary = report.get("summary")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    raw_artifacts = report.get("artifacts")
    artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
    raw_providers = report.get("providers")
    providers = (
        ", ".join(str(provider) for provider in raw_providers) if isinstance(raw_providers, list) else "core"
    )
    skipped = report.get("skipped_providers")
    skipped = skipped if isinstance(skipped, list) else []
    lines = [
        f"# {title}",
        "",
        (
            "We benchmarked docpull's local LLM-profile crawler against optional live "
            "Parallel, Tavily, and Exa context-pack providers, with Raindrop available "
            "as the metadata-only observability layer for traced runs."
        ),
        "",
        "## Methodology",
        "",
        f"- Target: `{report.get('target_url')}`",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Run directory: `{report.get('run_dir')}`",
        f"- Providers: `{providers}`",
        f"- Skipped providers: `{_format_skipped_providers(skipped)}`",
        f"- Parallel enabled: `{bool(report.get('parallel_enabled'))}`",
        *_raindrop_trace_lines(trace),
        (
            "- Trace content policy: metadata only. The benchmark records timings, "
            "counts, scores, costs, selected URLs, and artifact paths; it does not "
            "ship scraped document text by default."
        ),
        "",
        "## Results",
        "",
        "| Case | Workflow | Wall seconds | Pack score | Records | Estimated cost |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for case in cases:
        score = case.get("pack_score")
        if isinstance(score, dict):
            score_value = score.get("score")
            score_summary = score.get("summary")
            record_count = score_summary.get("record_count", "") if isinstance(score_summary, dict) else ""
        elif _is_cache_only_case(case):
            score_value = "cache skip"
            stats = case.get("stats")
            stats = stats if isinstance(stats, dict) else {}
            record_count = f"0 fetched / {stats.get('pages_skipped', 0)} skipped"
        else:
            score_value = ""
            record_count = ""
        estimated_cost = case.get("estimated_cost_usd")
        cost_text = f"${estimated_cost:.6f}" if isinstance(estimated_cost, float) else "n/a"
        lines.append(
            "| "
            f"`{case.get('name')}` | "
            f"`{case.get('workflow')}` | "
            f"{case.get('wall_seconds')} | "
            f"{score_value} | "
            f"{record_count} | "
            f"{cost_text} |"
        )
    lines.extend(["", "## What Stood Out", ""])
    if best_case:
        best_score = best_case["pack_score"]["score"]
        lines.append(f"- Best pack score: `{best_case['name']}` at `{best_score}/100`.")
    if fastest_case:
        lines.append(
            f"- Fastest case: `{fastest_case.get('name')}` at `{fastest_case.get('wall_seconds')}` seconds."
        )
    total_cost = summary.get(
        "total_estimated_live_cost_usd", summary.get("total_estimated_parallel_cost_usd", 0)
    )
    lines.append(f"- Estimated live provider cost for this run: `${float(total_cost):.6f}`.")
    if skipped:
        lines.append(
            "- Missing or unavailable providers were skipped without failing the run: "
            f"{_format_skipped_providers(skipped)}."
        )
    if trace.get("enabled"):
        lines.append(
            "- Raindrop tracing was enabled, so each benchmark case was emitted as a tool trace and "
            "attention-worthy cells were promoted into Raindrop signals."
        )
    else:
        lines.append(
            "- Raindrop tracing was not enabled in this run. Re-run with `--trace raindrop` and "
            "`RAINDROP_WRITE_KEY` to publish observed spans alongside the report."
        )
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "pip install parallel-web raindrop-ai",
            "export PARALLEL_API_KEY='<parallel-key>'",
            "export TAVILY_API_KEY='<tavily-key>'",
            "export EXA_API_KEY='<exa-key>'",
            "export RAINDROP_WRITE_KEY='<raindrop-write-key>'",
            ("docpull benchmark quick --provider all --trace raindrop --max-estimated-cost 0.10"),
            "docpull benchmark article .bench/runs/<run>/benchmark.report.json",
            "```",
            "",
            "## Artifacts",
            "",
            f"- Config: `{artifacts.get('config')}`",
            f"- JSON report: `{artifacts.get('json')}`",
            f"- Summary: `{artifacts.get('markdown')}`",
            "",
        ]
    )
    return "\n".join(lines)


def _best_scored_case(
    cases: list[dict[str, Any]],
    *,
    score_key: str = "pack_score",
) -> dict[str, Any] | None:
    scored = [
        case
        for case in cases
        if isinstance(case.get(score_key), dict) and isinstance(case[score_key].get("score"), int)
    ]
    return max(scored, key=lambda item: int(item[score_key]["score"])) if scored else None


def _is_cache_only_case(case: dict[str, Any]) -> bool:
    stats = case.get("stats")
    if not isinstance(stats, dict):
        return False
    fetched = int(stats.get("pages_fetched") or 0)
    skipped = int(stats.get("pages_skipped") or 0)
    failed = int(stats.get("pages_failed") or 0)
    return fetched == 0 and skipped > 0 and failed == 0


def _default_run_dir() -> Path:
    stamp = utc_now_iso().replace(":", "-").replace("+", "-").replace(".", "-")
    return Path(".bench") / "runs" / stamp


def _validate_positive_int(value: int, name: str) -> None:
    if value < 1:
        raise BenchmarkError(f"{name} must be at least 1.")


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _peak_rss_bytes() -> int:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run_benchmark_cli())
