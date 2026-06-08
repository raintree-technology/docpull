"""Repeatable docpull benchmark harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from rich.console import Console
from rich.markup import escape

from .conversion.chunking import TokenCounter
from .core.fetcher import Fetcher
from .models.config import CacheConfig, CrawlConfig, DocpullConfig, OutputConfig, ProfileName
from .models.document import DocumentRecord
from .models.events import EventType
from .pack_tools import score_pack, score_pack_sources
from .parallel_workflows import (
    DEFAULT_MAX_ESTIMATED_COST_USD,
    DEFAULT_MODE,
    ParallelWorkflowError,
    _build_source_policy,
    _parallel_sdk_installed,
    estimate_context_pack_cost,
    estimate_search_pack_cost,
    run_live_context_pack,
    run_search_pack,
)
from .pipeline.manifest import CorpusManifest
from .provider_keys import (
    PROVIDER_CONFIGS,
    PROVIDER_NAMES,
    ProviderName,
    lookup_api_key_env_var,
    lookup_provider_api_key,
)
from .time_utils import utc_now_iso

BENCHMARK_SCHEMA_VERSION = 1
DEFAULT_TARGET_URL = "https://docs.parallel.ai"
DEFAULT_INCLUDE_DOMAIN = "docs.parallel.ai"
DEFAULT_OBJECTIVE = "Build an agent context pack for Parallel API docs"
DEFAULT_QUERY = "Parallel API reference Search Extract docs"
EXA_API_KEY_ENV = "EXA_API_KEY"
TAVILY_API_KEY_ENV = "TAVILY_API_KEY"
RAINDROP_WRITE_KEY_ENV = "RAINDROP_WRITE_KEY"
EXA_SEARCH_URL = "https://api.exa.ai/search"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


class BenchmarkError(RuntimeError):
    """User-facing benchmark error."""


@dataclass
class _ProviderDocument:
    url: str
    title: str
    content: str
    metadata: dict[str, Any]
    source_type: str


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
    quick.add_argument("--target-url", default=DEFAULT_TARGET_URL, help="Docs URL to crawl")
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
        "--no-cached-pass",
        action="store_true",
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
        default=DEFAULT_OBJECTIVE,
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
        "--max-estimated-cost",
        type=float,
        default=DEFAULT_MAX_ESTIMATED_COST_USD,
        help="Local pre-call spend guard for providers with known cost estimates",
    )
    quick.add_argument(
        "--trace",
        choices=["none", "raindrop"],
        default="none",
        help=(
            "Optional observability trace backend. Raindrop requires "
            "RAINDROP_WRITE_KEY and docpull[observability]."
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
                output_dir=args.output_dir,
                max_pages=args.max_pages,
                max_depth=args.max_depth,
                max_concurrent=args.max_concurrent,
                per_host_concurrent=args.per_host_concurrent,
                cache_enabled=not args.no_cache,
                cached_pass=not args.no_cached_pass,
                parallel=args.parallel,
                tavily=args.tavily,
                exa=args.exa,
                live_providers=args.provider,
                parallel_objective=args.parallel_objective,
                parallel_queries=args.parallel_queries or [DEFAULT_QUERY],
                include_domains=args.include_domains or [DEFAULT_INCLUDE_DOMAIN],
                mode=args.mode,
                max_search_results=args.max_search_results,
                extract_limit=args.extract_limit,
                max_estimated_cost=args.max_estimated_cost,
                trace_backend=args.trace,
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
    cached_pass: bool,
    parallel: bool,
    parallel_objective: str,
    parallel_queries: list[str],
    include_domains: list[str],
    mode: str,
    max_search_results: int,
    extract_limit: int,
    max_estimated_cost: float,
    trace_backend: str = "none",
    tavily: bool = False,
    exa: bool = False,
    live_providers: list[str] | None = None,
) -> dict[str, Any]:
    """Run the default real-site benchmark matrix."""
    _validate_positive_int(max_pages, "max_pages")
    _validate_positive_int(max_depth, "max_depth")
    _validate_positive_int(max_concurrent, "max_concurrent")
    _validate_positive_int(per_host_concurrent, "per_host_concurrent")
    _validate_positive_int(max_search_results, "max_search_results")
    _validate_positive_int(extract_limit, "extract_limit")
    if max_estimated_cost < 0:
        raise BenchmarkError("max_estimated_cost cannot be negative.")
    requested_providers = _normalize_live_providers(
        parallel=parallel,
        tavily=tavily,
        exa=exa,
        live_providers=live_providers,
    )
    provider_status = _live_provider_statuses(requested_providers)
    providers = [
        provider
        for provider in requested_providers
        if provider_status[provider]["ready"]
    ]
    skipped_providers = [
        {
            "provider": provider,
            "reason": provider_status[provider]["reason"],
            "api_key_env_var": provider_status[provider]["api_key_env_var"],
        }
        for provider in requested_providers
        if provider not in providers
    ]
    parallel = "parallel" in providers

    run_dir = (output_dir or _default_run_dir()).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = _make_trace_recorder(
        trace_backend,
        target_url=target_url,
        output_dir=run_dir,
        parallel_enabled=parallel,
        max_estimated_cost=max_estimated_cost,
    )

    source_policy: dict[str, Any] | None = None
    estimated_search_cost = 0.0
    estimated_context_cost = 0.0
    if parallel:
        estimated_search_cost = estimate_search_pack_cost(max_search_results=max_search_results)
        estimated_context_cost = estimate_context_pack_cost(
            extract_limit=extract_limit,
            max_search_results=max_search_results,
        )
        estimated_total_cost = round(estimated_search_cost + estimated_context_cost, 6)
        if estimated_total_cost > max_estimated_cost:
            raise BenchmarkError(
                "Estimated Parallel benchmark cost "
                f"${estimated_total_cost:.6f} exceeds guard ${max_estimated_cost:.6f}."
            )
        source_policy = _build_source_policy(include_domains=include_domains)

    cases: list[dict[str, Any]] = []
    cache_dir = run_dir / "cache-core"
    core_output = run_dir / "core-llm"
    case = asyncio.run(
        _run_core_case(
            name="core-llm",
            target_url=target_url,
            output_dir=core_output,
            cache_dir=cache_dir,
            cache_enabled=cache_enabled,
            max_pages=max_pages,
            max_depth=max_depth,
            max_concurrent=max_concurrent,
            per_host_concurrent=per_host_concurrent,
            include_domains=include_domains,
        )
    )
    trace.record_case(case)
    cases.append(case)

    if cache_enabled and cached_pass:
        case = asyncio.run(
            _run_core_case(
                name="core-llm-cached",
                target_url=target_url,
                output_dir=run_dir / "core-llm-cached",
                cache_dir=cache_dir,
                cache_enabled=True,
                max_pages=max_pages,
                max_depth=max_depth,
                max_concurrent=max_concurrent,
                per_host_concurrent=per_host_concurrent,
                include_domains=include_domains,
            )
        )
        trace.record_case(case)
        cases.append(case)

    if parallel:
        assert source_policy is not None
        case = _run_parallel_search_case(
            objective=parallel_objective,
            queries=parallel_queries,
            output_dir=run_dir / "parallel-search",
            include_domains=include_domains,
            source_policy=source_policy,
            mode=mode,
            max_search_results=max_search_results,
            estimated_cost=estimated_search_cost,
        )
        trace.record_case(case)
        cases.append(case)
        case = _run_parallel_context_case(
            objective=parallel_objective,
            queries=parallel_queries,
            output_dir=run_dir / "parallel-context",
            include_domains=include_domains,
            source_policy=source_policy,
            mode=mode,
            max_search_results=max_search_results,
            extract_limit=extract_limit,
            estimated_cost=estimated_context_cost,
        )
        trace.record_case(case)
        cases.append(case)

    if "tavily" in providers:
        case = _run_tavily_case(
            objective=parallel_objective,
            queries=parallel_queries,
            output_dir=run_dir / "tavily-search-extract",
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=extract_limit,
        )
        trace.record_case(case)
        cases.append(case)

    if "exa" in providers:
        case = _run_exa_case(
            objective=parallel_objective,
            queries=parallel_queries,
            output_dir=run_dir / "exa-search-contents",
            include_domains=include_domains,
            max_search_results=max_search_results,
        )
        trace.record_case(case)
        cases.append(case)

    report_path = run_dir / "benchmark.report.json"
    markdown_path = run_dir / "benchmark.summary.md"
    report = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "run_dir": str(run_dir),
        "target_url": target_url,
        "parallel_enabled": parallel,
        "providers": ["core", *providers],
        "requested_providers": requested_providers,
        "skipped_providers": skipped_providers,
        "provider_status": provider_status,
        "trace": trace.metadata(),
        "cases": cases,
        "summary": _summary(cases),
        "artifacts": {
            "json": str(report_path),
            "markdown": str(markdown_path),
        },
    }
    trace.finish(report)
    report["trace"] = trace.metadata()
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


def _normalize_live_providers(
    *,
    parallel: bool,
    tavily: bool,
    exa: bool,
    live_providers: list[str] | None,
) -> list[ProviderName]:
    selected: list[str] = []
    if parallel:
        selected.append("parallel")
    if tavily:
        selected.append("tavily")
    if exa:
        selected.append("exa")
    selected.extend(live_providers or [])
    normalized: list[ProviderName] = []
    for raw_provider in selected:
        provider = raw_provider.strip().lower()
        if provider in {"auto", "all"}:
            for name in PROVIDER_NAMES:
                if name not in normalized:
                    normalized.append(name)
            continue
        if provider not in PROVIDER_CONFIGS:
            raise BenchmarkError(f"Unsupported live benchmark provider: {raw_provider}")
        name = provider  # type: ignore[assignment]
        if name not in normalized:
            normalized.append(name)
    return normalized


def _live_provider_statuses(providers: list[ProviderName]) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for provider in providers:
        config = PROVIDER_CONFIGS[provider]
        lookup = lookup_provider_api_key(provider)
        api_key_present = bool(lookup.value)
        sdk_installed = True
        reason = "ready"
        ready = api_key_present
        if provider == "parallel":
            sdk_installed = _parallel_sdk_installed()
            ready = api_key_present and sdk_installed
            if api_key_present and not sdk_installed:
                reason = "missing_optional_sdk"
        if not api_key_present:
            reason = "missing_api_key"
        statuses[provider] = {
            "provider": provider,
            "label": config.label,
            "ready": ready,
            "reason": reason,
            "api_key_env_var": config.api_key_env_var,
            "api_key_present": api_key_present,
            "api_key_source": lookup.source,
            "api_key_source_path": str(lookup.path) if lookup.path else None,
            "sdk_installed": sdk_installed,
        }
    return statuses


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
                "Raindrop tracing requires the optional SDK. "
                "Install with: pip install 'docpull[observability]'"
            ) from err
        self._raindrop: Any = raindrop
        try:
            raindrop.init(api_key, tracing_enabled=True, bypass_otel_for_tools=True)
        except TypeError:
            raindrop.init(api_key, tracing_enabled=True)
        self._interaction = raindrop.begin(
            user_id="docpull-benchmark",
            event="docpull_benchmark",
            input=_json_trace_text(
                {
                    "target_url": target_url,
                    "output_dir": str(output_dir),
                    "parallel_enabled": parallel_enabled,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            ),
        )
        self._event_id = str(getattr(self._interaction, "event_id", "") or "")
        self._case_count = 0
        self._status = "recording"

    def record_case(self, case: dict[str, Any]) -> None:
        self._case_count += 1
        self._interaction.track_tool(
            name=str(case.get("name") or "benchmark_case"),
            input={
                "workflow": case.get("workflow"),
                "output_dir": case.get("output_dir"),
            },
            output=_trace_case_output(case),
            duration_ms=int(float(case.get("wall_seconds") or 0.0) * 1000),
            properties={
                "provider": "docpull",
                "workflow": case.get("workflow"),
                "estimated_cost_usd": case.get("estimated_cost_usd", 0.0),
            },
        )

    def finish(self, report: dict[str, Any]) -> None:
        self._interaction.finish(
            output=_json_trace_text(
                {
                    "summary": report.get("summary"),
                    "artifacts": report.get("artifacts"),
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
            "content_policy": "metadata_only",
        }


def _make_trace_recorder(
    backend: str,
    *,
    target_url: str,
    output_dir: Path,
    parallel_enabled: bool,
    max_estimated_cost: float,
) -> _TraceRecorder:
    if backend == "none":
        return _TraceRecorder()
    if backend == "raindrop":
        return _RaindropTraceRecorder(
            target_url=target_url,
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
        "wall_seconds": case.get("wall_seconds"),
        "rss_delta_mb": case.get("rss_delta_mb"),
        "artifact_size_bytes": case.get("artifact_size_bytes"),
        "cache_size_bytes": case.get("cache_size_bytes"),
        "estimated_cost_usd": case.get("estimated_cost_usd", 0.0),
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
        "source_score_count": case.get("source_score_count"),
        "selected_urls": selected_urls,
    }


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
    _attach_pack_scores(payload, output_dir, include_domains)
    _attach_pack_metadata(payload, output_dir / "search.pack.json")
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
    _attach_pack_scores(payload, output_dir, include_domains)
    _attach_pack_metadata(payload, output_dir / "parallel.pack.json")
    return payload


def _run_tavily_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int,
) -> dict[str, Any]:
    api_key = _require_benchmark_api_key(TAVILY_API_KEY_ENV, "Tavily")
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    query = queries[0]
    search_body: dict[str, Any] = {
        "query": query,
        "search_depth": "basic",
        "max_results": max_search_results,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
        "include_favicon": True,
    }
    if include_domains:
        search_body["include_domains"] = include_domains
    search_payload = _http_json_post(
        label="Tavily Search",
        url=TAVILY_SEARCH_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        body=search_body,
        timeout=60,
    )
    search_results = _json_list(search_payload.get("results"))
    selected_urls = _select_result_urls(search_results, extract_limit)
    if not selected_urls:
        raise BenchmarkError("Tavily Search returned no extractable URLs.")

    extract_body = {
        "urls": selected_urls,
        "extract_depth": "basic",
        "format": "markdown",
        "include_favicon": True,
        "include_usage": True,
    }
    extract_payload = _http_json_post(
        label="Tavily Extract",
        url=TAVILY_EXTRACT_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        body=extract_body,
        timeout=90,
    )
    search_by_url = {str(item.get("url")): item for item in search_results if item.get("url")}
    documents: list[_ProviderDocument] = []
    for item in _json_list(extract_payload.get("results")):
        url = str(item.get("url") or "")
        if not url:
            continue
        search_item = search_by_url.get(url, {})
        raw_content = str(item.get("raw_content") or "").strip()
        fallback_content = str(search_item.get("content") or "").strip()
        content = raw_content or fallback_content
        if not content:
            continue
        documents.append(
            _ProviderDocument(
                url=url,
                title=str(search_item.get("title") or url),
                content=content,
                source_type="tavily",
                metadata={
                    "provider": "tavily",
                    "search_score": search_item.get("score"),
                    "favicon": item.get("favicon") or search_item.get("favicon"),
                    "content_source": "extract.raw_content" if raw_content else "search.content",
                },
            )
        )
    failed_results = _json_list(extract_payload.get("failed_results"))
    if not documents:
        raise BenchmarkError("Tavily Extract returned no non-empty documents.")

    pack_path = _write_provider_pack(
        output_dir=output_dir,
        provider="tavily",
        workflow="tavily-search-extract",
        objective=objective,
        queries=queries,
        documents=documents,
        include_domains=include_domains,
        max_search_results=max_search_results,
        extract_limit=extract_limit,
        selected_urls=selected_urls,
        search_result_count=len(search_results),
        extract_result_count=len(documents),
        extract_error_count=len(failed_results),
        usage={
            "search": search_payload.get("usage"),
            "extract": extract_payload.get("usage"),
        },
        response_metadata={
            "search_request_id": search_payload.get("request_id"),
            "extract_request_id": extract_payload.get("request_id"),
            "search_response_time": search_payload.get("response_time"),
            "extract_response_time": extract_payload.get("response_time"),
        },
    )
    payload = _base_case(
        name="tavily-search-extract",
        workflow="tavily-search-extract-pack",
        output_dir=output_dir,
        wall_seconds=time.perf_counter() - t0,
        rss_before=rss_before,
    )
    payload["artifact_size_bytes"] = _dir_size(output_dir)
    _attach_pack_scores(payload, output_dir, include_domains)
    _attach_pack_metadata(payload, pack_path)
    return payload


def _run_exa_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    max_search_results: int,
) -> dict[str, Any]:
    api_key = _require_benchmark_api_key(EXA_API_KEY_ENV, "Exa")
    rss_before = _peak_rss_bytes()
    t0 = time.perf_counter()
    query = queries[0]
    search_body: dict[str, Any] = {
        "query": query,
        "numResults": max_search_results,
        "contents": {
            "text": {"verbosity": "standard"},
            "highlights": True,
        },
    }
    if include_domains:
        search_body["includeDomains"] = include_domains
    search_payload = _http_json_post(
        label="Exa Search",
        url=EXA_SEARCH_URL,
        headers={"x-api-key": api_key},
        body=search_body,
        timeout=90,
    )
    results = _json_list(search_payload.get("results"))
    documents: list[_ProviderDocument] = []
    for item in results:
        url = str(item.get("url") or "")
        if not url:
            continue
        content = str(item.get("text") or "").strip()
        if not content:
            highlights = [str(value) for value in _json_list(item.get("highlights")) if value]
            content = "\n\n".join(highlights).strip()
        if not content:
            content = str(item.get("summary") or "").strip()
        if not content:
            continue
        documents.append(
            _ProviderDocument(
                url=url,
                title=str(item.get("title") or url),
                content=content,
                source_type="exa",
                metadata={
                    "provider": "exa",
                    "id": item.get("id"),
                    "published_date": item.get("publishedDate"),
                    "author": item.get("author"),
                    "favicon": item.get("favicon"),
                    "content_source": "search.text",
                },
            )
        )
    if not documents:
        raise BenchmarkError("Exa Search returned no non-empty documents.")
    cost_dollars = search_payload.get("costDollars")
    estimated_cost = _cost_dollars_total(cost_dollars)
    selected_urls = [document.url for document in documents]
    pack_path = _write_provider_pack(
        output_dir=output_dir,
        provider="exa",
        workflow="exa-search-contents",
        objective=objective,
        queries=queries,
        documents=documents,
        include_domains=include_domains,
        max_search_results=max_search_results,
        extract_limit=len(documents),
        selected_urls=selected_urls,
        search_result_count=len(results),
        extract_result_count=len(documents),
        extract_error_count=max(0, len(results) - len(documents)),
        usage={"cost_dollars": cost_dollars},
        response_metadata={
            "request_id": search_payload.get("requestId"),
            "resolved_search_type": search_payload.get("resolvedSearchType"),
        },
        cost_dollars=cost_dollars if isinstance(cost_dollars, dict) else None,
    )
    payload = _base_case(
        name="exa-search-contents",
        workflow="exa-search-contents-pack",
        output_dir=output_dir,
        wall_seconds=time.perf_counter() - t0,
        rss_before=rss_before,
    )
    if estimated_cost is not None:
        payload["estimated_cost_usd"] = estimated_cost
    payload["artifact_size_bytes"] = _dir_size(output_dir)
    _attach_pack_scores(payload, output_dir, include_domains)
    _attach_pack_metadata(payload, pack_path)
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
    payload["pack_score"] = {
        "score": score["score"],
        "grade": score["grade"],
        "summary": score["summary"],
        "issues": score["issues"],
        "warnings": score["warnings"],
    }
    payload["source_score_count"] = sources["source_count"]


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
        lines.append(f"{index}. [{title}]({url})")
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
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **headers,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise BenchmarkError(f"{label} returned HTTP {err.code}: {_short_error_detail(detail)}") from err
    except URLError as err:
        raise BenchmarkError(f"{label} request failed: {err.reason}") from err
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as err:
        raise BenchmarkError(f"{label} returned invalid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise BenchmarkError(f"{label} returned JSON {type(parsed).__name__}, expected object.")
    return parsed


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


def _short_error_detail(value: str) -> str:
    compact = " ".join(value.split())
    return compact[:500]


def _summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [
        int(score["score"])
        for case in cases
        if isinstance((score := case.get("pack_score")), dict) and isinstance(score.get("score"), int)
    ]
    total_estimated_cost = sum(float(case.get("estimated_cost_usd") or 0.0) for case in cases)
    total_parallel_cost = sum(
        float(case.get("estimated_cost_usd") or 0.0)
        for case in cases
        if str(case.get("workflow") or "").startswith("parallel-")
    )
    cache_only_cases = [_is_cache_only_case(case) for case in cases]
    return {
        "case_count": len(cases),
        "best_pack_score": max(scores) if scores else None,
        "total_estimated_live_cost_usd": round(total_estimated_cost, 6),
        "total_estimated_parallel_cost_usd": round(total_parallel_cost, 6),
        "cache_only_case_count": sum(cache_only_cases),
        "unscored_case_count": sum(
            1
            for case, cache_only in zip(cases, cache_only_cases, strict=True)
            if case.get("pack_score") is None and not cache_only
        ),
    }


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


def _markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# docpull Benchmark Summary",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Target: `{report['target_url']}`",
        f"Run directory: `{report['run_dir']}`",
        "",
        "## Cases",
        "",
        "| Case | Wall seconds | Pack score | Records | Estimated cost |",
        "| --- | ---: | ---: | ---: | ---: |",
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
        lines.append(
            f"| `{case['name']}` | {case['wall_seconds']} | {score_value} | {record_count} | {cost_text} |"
        )
    skipped = report.get("skipped_providers")
    skipped = skipped if isinstance(skipped, list) else []
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- Cases: {report['summary']['case_count']}",
            f"- Best pack score: {report['summary']['best_pack_score']}",
            f"- Cache-only cases: {report['summary']['cache_only_case_count']}",
            f"- Skipped providers: {_format_skipped_providers(skipped)}",
            (
                "- Total estimated live provider cost: "
                f"${report['summary'].get('total_estimated_live_cost_usd', 0):.6f}"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _article_markdown(report: dict[str, Any], *, title: str) -> str:
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
        f"- Raindrop trace: `{trace.get('provider', 'none')}` / `{trace.get('status', 'disabled')}`",
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
            "- Raindrop tracing was enabled, so each benchmark case was emitted as a tool trace for "
            "follow-up investigation and experiment tracking."
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
            "pip install 'docpull[parallel,observability]'",
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
            f"- JSON report: `{artifacts.get('json')}`",
            f"- Summary: `{artifacts.get('markdown')}`",
            "",
        ]
    )
    return "\n".join(lines)


def _best_scored_case(cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    scored = [
        case
        for case in cases
        if isinstance(case.get("pack_score"), dict) and isinstance(case["pack_score"].get("score"), int)
    ]
    return max(scored, key=lambda item: int(item["pack_score"]["score"])) if scored else None


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
