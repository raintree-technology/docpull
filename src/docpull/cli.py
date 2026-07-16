"""Command-line interface for docpull."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

if "--doctor" in sys.argv:
    from .doctor import run_doctor

    output_dir = None
    if "--output-dir" in sys.argv or "-o" in sys.argv:
        flag = "--output-dir" if "--output-dir" in sys.argv else "-o"
        flag_idx = sys.argv.index(flag)
        if flag_idx + 1 < len(sys.argv):
            output_dir = Path(sys.argv[flag_idx + 1])
    sys.exit(run_doctor(output_dir=output_dir))

from . import __version__
from .surface import PRUNED_CLI_COMMANDS, format_cli_subcommands

if TYPE_CHECKING:
    from .models.config import DocpullConfig
    from .models.events import SkipReason
    from .rendering import Renderer

RenderBackend = Literal["agent-browser", "vercel-sandbox", "e2b-sandbox"]

_CLI_LAZY_EXPORTS = {
    "Fetcher": (".core.fetcher", "Fetcher"),
    "check_render_backend_availability": (
        ".rendering",
        "check_render_backend_availability",
    ),
    "render_url_to_directory": (".rendering", "render_url_to_directory"),
}


def __getattr__(name: str) -> object:
    """Preserve CLI test/integration seams without eager heavy imports."""
    target = _CLI_LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __package__), attribute_name)
    globals()[name] = value
    return value


def _parse_budget_value(value: str) -> float:
    """Load budget parsing only when argparse sees a budget value."""
    from .accounting import parse_budget_value

    return parse_budget_value(value)


def _core_dependencies_available() -> bool:
    """Keep the friendly fetch dependency error off unrelated CLI paths."""
    try:
        import aiohttp  # noqa: F401
        import bs4  # noqa: F401
        import defusedxml  # noqa: F401
        import html2text  # noqa: F401
        import rich  # noqa: F401
    except ImportError as err:
        print(f"\nERROR: Missing required dependency: {err.name}", file=sys.stderr)
        print("\nDocpull requires all core dependencies to be installed.", file=sys.stderr)
        print("\nRecommended fixes:", file=sys.stderr)
        print("  1. For pipx users: pipx reinstall docpull --force", file=sys.stderr)
        print("  2. For pip users: pip install --upgrade --force-reinstall docpull", file=sys.stderr)
        print("  3. For development: pip install -e .[dev]", file=sys.stderr)
        print("\nTo diagnose issues, run: docpull --doctor", file=sys.stderr)
        return False
    return True


def _write_fetch_accounting(
    *,
    config: DocpullConfig,
    stats: object | None,
    route_steps: list,
    render_estimated_cost: float,
    paid_capable: bool,
    skip_counts: dict[SkipReason, int] | None = None,
) -> None:
    from .accounting import RunAccounting, maybe_write_run_accounting
    from .models.events import SkipReason

    cache_hits = 0
    if skip_counts:
        cache_hits = int(skip_counts.get(SkipReason.CACHE_UNCHANGED, 0))
    http_request_count = 0
    if stats is not None:
        http_request_count = int(getattr(stats, "pages_fetched", 0)) + int(getattr(stats, "pages_failed", 0))
    accounting = RunAccounting(
        budget_limit_usd=config.budget.maximum_paid_cost_usd,
        estimated_paid_cost_usd=render_estimated_cost if paid_capable else 0.0,
        paid_request_count=1 if paid_capable and render_estimated_cost > 0 else 0,
        http_request_count=http_request_count,
        cache_hit_count=cache_hits,
        route_steps=route_steps,
        command="fetch",
    )
    maybe_write_run_accounting(
        config.output.directory,
        budget_limit_usd=config.budget.maximum_paid_cost_usd,
        paid_capable=paid_capable,
        accounting=accounting,
    )


def _output_dir_has_records(output_dir: Path) -> bool:
    """Return whether an output directory contains at least one readable record."""
    manifest_path = output_dir / "corpus.manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = None
    if isinstance(manifest, dict):
        for key in ("record_count", "document_count", "chunk_count"):
            value = manifest.get(key)
            if isinstance(value, int) and value > 0:
                return True
        records = manifest.get("records")
        if isinstance(records, list) and records:
            return True

    ndjson_path = output_dir / "documents.ndjson"
    try:
        if ndjson_path.exists():
            return any(line.strip() for line in ndjson_path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False
    return False


def _fetch_exit_code(
    stats: object,
    output_dir: Path,
    *,
    allow_empty: bool = False,
    exit_policy: str = "strict",
) -> int:
    if int(getattr(stats, "pages_failed", 0)) > 0:
        return 1
    if allow_empty:
        return 0
    if int(getattr(stats, "pages_fetched", 0)) > 0:
        return 0
    if exit_policy == "usable-output" and _output_dir_has_records(output_dir):
        return 0
    return 1


def _add_render_options(parser: argparse.ArgumentParser) -> None:
    render_group = parser.add_argument_group("rendering")
    render_group.add_argument(
        "--render",
        choices=["off", "agent-browser", "fallback"],
        default="off",
        help="Optional browser rendering mode (default: off)",
    )
    render_group.add_argument(
        "--render-runtime",
        choices=["local", "vercel", "e2b"],
        default="local",
        help="Renderer runtime for --render agent-browser/fallback",
    )
    render_group.add_argument(
        "--render-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Renderer timeout per page",
    )
    render_group.add_argument(
        "--render-wait-for",
        choices=["load", "domcontentloaded", "networkidle"],
        default=None,
        metavar="STATE",
        help="Renderer load state to wait for before reading HTML",
    )
    render_group.add_argument(
        "--render-allowed-domain",
        action="append",
        default=None,
        metavar="DOMAIN",
        help=(
            "Domain allowed during rendering. May be repeated. Defaults to the target URL host when omitted."
        ),
    )
    render_group.add_argument(
        "--render-viewport",
        default=None,
        metavar="WIDTHxHEIGHT",
        help="Renderer viewport, for example 1280x720",
    )
    render_group.add_argument(
        "--render-max-html-bytes",
        default=None,
        metavar="SIZE",
        help="Maximum rendered HTML size, for example 10mb",
    )
    render_group.add_argument(
        "--render-cloud-agent-browser-install",
        choices=["auto", "skip"],
        default=None,
        help="Install agent-browser inside cloud sandboxes, or skip for prebuilt templates",
    )
    render_group.add_argument(
        "--render-cloud-max-estimated-cost",
        type=float,
        default=None,
        metavar="USD",
        help="Fail cloud rendering when the estimated per-page cost exceeds this cap",
    )
    render_group.add_argument(
        "--render-template",
        default=None,
        metavar="TEMPLATE",
        help="Cloud runtime template name; currently used by --render-runtime e2b",
    )


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for CLI."""
    epilog = f"""
Examples:
  # Fetch with default settings (RAG profile)
  docpull https://docs.example.com

  # Use a specific profile
  docpull https://docs.example.com --profile mirror

  # Control crawl behavior
  docpull https://example.com --max-pages 100 --max-depth 3

  # Filter paths
  docpull https://example.com --include-paths "/api/*" --exclude-paths "/changelog/*"

{format_cli_subcommands()}
        """
    parser = argparse.ArgumentParser(
        prog="docpull",
        description="Fetch and convert static/server-rendered web content to markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )

    parser.add_argument(
        "url",
        nargs="?",
        help="URL to fetch content from",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run diagnostic checks",
    )
    parser.add_argument(
        "--budget",
        type=_parse_budget_value,
        default=None,
        metavar="USD",
        help="Maximum paid-capable provider/cloud spend for this run. Use 0 for zero paid calls.",
    )
    parser.add_argument(
        "--explain-route",
        action="store_true",
        help="Print the local-first acquisition route and exit without fetching.",
    )

    parser.add_argument(
        "--profile",
        "-p",
        choices=["rag", "mirror", "quick", "llm", "okf", "sec-filing"],
        default="rag",
        help=(
            "Preset profile (default: rag). 'llm' streams chunked NDJSON; "
            "'okf' writes an OKF bundle; 'sec-filing' tunes extraction for EDGAR filings."
        ),
    )

    parser.add_argument(
        "--single",
        action="store_true",
        help="Fetch the given URL only (no discovery/crawl). Fast path for agents.",
    )

    parser.add_argument(
        "--skill",
        type=str,
        metavar="NAME",
        help=(
            "Generate an agent skill/rule export. Scraped pages go under "
            "references/ with hierarchical naming; Claude Code and Codex "
            "receive SKILL.md folders, and Cursor receives an .mdc rule."
        ),
    )
    parser.add_argument(
        "--skill-description",
        type=str,
        metavar="TEXT",
        help="Override the auto-derived `description` in SKILL.md.",
    )
    parser.add_argument(
        "--skill-agent",
        action="append",
        choices=["claude", "codex", "cursor", "all"],
        metavar="AGENT",
        help=(
            "Agent export target for --skill: claude, codex, cursor, or all. "
            "May be repeated. Default: claude."
        ),
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Output directory (default: ./docs)",
    )
    parser.add_argument(
        "--format",
        "-f",
        choices=["markdown", "json", "ndjson", "sqlite", "okf"],
        default=None,
        help="Output format (default: markdown; 'ndjson' streams records; 'okf' writes an OKF bundle)",
    )
    parser.add_argument(
        "--naming-strategy",
        choices=["full", "hierarchical"],
        default=None,
        help=(
            "URL-to-filename strategy. 'full' flattens with underscores; "
            "'hierarchical' preserves the URL path as nested directories. "
            "Mirror profile defaults to hierarchical unless explicitly overridden."
        ),
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream NDJSON records to stdout as each page completes (implies --format ndjson)",
    )
    parser.add_argument(
        "--remote-documents",
        choices=["off", "pdf"],
        default=None,
        help="Explicitly download and locally parse selected remote document types (default: off)",
    )
    parser.add_argument(
        "--remote-document-backend",
        choices=["auto", "pypdf", "markitdown", "unstructured"],
        default=None,
        help="Local parser backend for --remote-documents (default: auto)",
    )
    parser.add_argument(
        "--remote-document-timeout-seconds",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Wall-time limit for each isolated remote-document parser (default: 60)",
    )
    parser.add_argument(
        "--remote-document-memory-mib",
        type=int,
        default=None,
        metavar="MIB",
        help="Address-space limit for each isolated remote-document parser (default: 1024)",
    )

    # Crawl settings
    crawl_group = parser.add_argument_group("crawl settings")
    crawl_group.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum pages to fetch",
    )
    crawl_group.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Maximum crawl depth",
    )
    crawl_group.add_argument(
        "--max-concurrent",
        type=int,
        default=None,
        help="Maximum concurrent requests",
    )
    crawl_group.add_argument(
        "--per-host-concurrent",
        type=int,
        default=None,
        help="Maximum concurrent requests per host",
    )
    crawl_group.add_argument(
        "--rate-limit",
        "-r",
        type=float,
        default=None,
        help="Seconds between requests",
    )
    crawl_group.add_argument(
        "--include-paths",
        nargs="+",
        metavar="PATTERN",
        help="Only crawl URLs matching these patterns",
    )
    crawl_group.add_argument(
        "--exclude-paths",
        nargs="+",
        metavar="PATTERN",
        help="Skip URLs matching these patterns",
    )
    crawl_group.add_argument(
        "--adaptive-rate-limit",
        action="store_true",
        help="Automatically adjust rate limits based on server responses",
    )
    crawl_group.add_argument(
        "--no-streaming-discovery",
        action="store_true",
        help=(
            "Fall back to discover-all-then-fetch instead of piping URLs "
            "through a worker pool as discovery yields them. Backstop for "
            "queue-backpressure regressions."
        ),
    )

    # Content filtering
    filter_group = parser.add_argument_group("content filtering")
    filter_group.add_argument(
        "--streaming-dedup",
        action="store_true",
        help="Enable real-time deduplication",
    )
    filter_group.add_argument(
        "--extractor",
        choices=["default", "trafilatura", "ensemble"],
        default=None,
        help="Content extractor (ensemble uses available local candidates; trafilatura is optional)",
    )
    filter_group.add_argument(
        "--no-special-cases",
        action="store_true",
        help="Disable framework-specific fast extractors (Next.js, OpenAPI, etc.)",
    )
    filter_group.add_argument(
        "--strict-js-required",
        action="store_true",
        help="Fail loud on pages that appear to require JavaScript (instead of silently skipping)",
    )

    # LLM / chunking
    llm_group = parser.add_argument_group("LLM / chunking")
    llm_group.add_argument(
        "--max-tokens-per-file",
        type=int,
        default=None,
        metavar="N",
        help="Split each page into chunks of at most N tokens (requires tiktoken for exact counts)",
    )
    llm_group.add_argument(
        "--tokenizer",
        type=str,
        default=None,
        metavar="NAME",
        help="tiktoken encoding for chunking (default: cl100k_base)",
    )
    llm_group.add_argument(
        "--emit-chunks",
        action="store_true",
        help="Write one file/record per chunk instead of per page",
    )

    # Network settings
    network_group = parser.add_argument_group("network settings")
    network_group.add_argument(
        "--proxy",
        type=str,
        metavar="URL",
        help="HTTP, HTTPS, or SOCKS proxy URL (SOCKS requires docpull[proxy])",
    )
    network_group.add_argument(
        "--user-agent",
        type=str,
        help="Custom User-Agent string",
    )
    network_group.add_argument(
        "--insecure-tls",
        action="store_true",
        help="Deprecated and rejected; docpull always verifies TLS certificates",
    )
    network_group.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Maximum retry attempts",
    )
    network_group.add_argument(
        "--require-pinned-dns",
        action="store_true",
        help=(
            "Refuse configurations that delegate DNS to a proxy. With this "
            "flag, --proxy is rejected so the SSRF posture cannot silently "
            "weaken in agent-driven crawls."
        ),
    )

    # Authentication settings
    auth_group = parser.add_argument_group("authentication")
    auth_group.add_argument(
        "--auth-policy",
        choices=["none", "explicit-private", "public-token-only"],
        default="none",
        help="Authenticated source mode label for manifests and audit reports",
    )
    auth_group.add_argument(
        "--auth-bearer",
        type=str,
        metavar="TOKEN",
        help="Bearer token for authentication",
    )
    auth_group.add_argument(
        "--auth-basic",
        type=str,
        metavar="USER:PASS",
        help="Basic auth credentials (username:password)",
    )
    auth_group.add_argument(
        "--auth-cookie",
        type=str,
        metavar="COOKIE",
        help="Cookie string for authentication",
    )
    auth_group.add_argument(
        "--auth-header",
        nargs=2,
        metavar=("NAME", "VALUE"),
        help="Custom auth header (name value)",
    )

    _add_render_options(parser)

    cache_group = parser.add_argument_group("cache settings")
    cache_group.add_argument(
        "--cache",
        action="store_true",
        help="Enable caching for incremental updates",
    )
    cache_group.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Cache directory (default: .docpull-cache)",
    )
    cache_group.add_argument(
        "--cache-ttl",
        type=int,
        default=None,
        metavar="DAYS",
        help="Days before cache entries expire (default: 30)",
    )
    cache_group.add_argument(
        "--no-skip-unchanged",
        action="store_true",
        help="Re-fetch pages even if unchanged",
    )
    cache_group.add_argument(
        "--resume",
        action="store_true",
        help="Resume from previous interrupted run (requires --cache)",
    )

    output_group = parser.add_argument_group("output control")
    output_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without downloading",
    )
    output_group.add_argument(
        "--preview-urls",
        action="store_true",
        help="List discovered URLs without fetching",
    )
    output_group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output",
    )
    output_group.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress output",
    )
    output_group.add_argument(
        "--exit-policy",
        choices=["strict", "usable-output"],
        default="strict",
        help=(
            "Exit based on this run only (strict, default), or accept usable records "
            "already present in the output directory (usable-output)"
        ),
    )

    return parser


def run_fetcher(args: argparse.Namespace) -> int:
    """Run the fetcher with given arguments."""
    if not _core_dependencies_available():
        return 1

    from rich.console import Console
    from rich.markup import escape
    from rich.progress import Progress, SpinnerColumn, TextColumn

    from .accounting import (
        BudgetError,
        RunAccounting,
        blocked_action,
        default_route_steps,
        enforce_paid_budget,
        maybe_write_run_accounting,
    )
    from .models.config import DocpullConfig, ProfileName
    from .models.events import EventType, FetchStats, SkipReason
    from .rendering import estimate_cloud_render_cost_usd
    from .skill_export import default_skill_root, expand_skill_agents
    from .time_utils import utc_now_iso

    fetcher_class: Any = globals().get("Fetcher") or __getattr__("Fetcher")
    console = Console()

    if not args.url:
        console.print("[red]Error:[/red] Please provide a URL to fetch")
        return 1

    profile_map = {
        "rag": ProfileName.RAG,
        "mirror": ProfileName.MIRROR,
        "quick": ProfileName.QUICK,
        "llm": ProfileName.LLM,
        "okf": ProfileName.OKF,
        "sec-filing": ProfileName.SEC_FILING,
    }
    profile = profile_map.get(args.profile, ProfileName.RAG)

    requested_format = args.format or ("okf" if args.profile == "okf" else None)
    if args.skill and requested_format == "okf":
        console.print(
            "[red]Error:[/red] --skill cannot be combined with OKF output. "
            "Write OKF with --format okf, or generate a skill with markdown output."
        )
        return 1
    if args.skill and (args.stream or (requested_format is not None and requested_format != "markdown")):
        console.print(
            "[red]Error:[/red] --skill requires markdown output. "
            "Use --format markdown or omit --format when generating a skill."
        )
        return 1

    config_kwargs: dict = {
        "profile": profile,
        "url": args.url,
        "dry_run": args.dry_run,
    }

    output_kwargs: dict = {}
    if args.skill:
        # Skill mode: place scraped pages under references/ and stamp the
        # agent-specific wrapper files after the crawl.
        skill_agents = expand_skill_agents(args.skill_agent)
        if args.output_dir:
            skill_root = args.output_dir / args.skill
        elif args.skill_agent is not None:
            skill_root = Path(".docpull/skills") / args.skill
        else:
            skill_root = default_skill_root(args.skill, skill_agents)
        output_kwargs["directory"] = skill_root / "references"
        output_kwargs["naming_strategy"] = "hierarchical"
        output_kwargs["skill_name"] = args.skill
        output_kwargs["skill_agents"] = skill_agents
        output_kwargs["skill_root_dir"] = skill_root
        output_kwargs["skill_install_targets"] = args.skill_agent is not None
        if args.skill_description:
            output_kwargs["skill_description"] = args.skill_description
    elif args.output_dir:
        output_kwargs["directory"] = args.output_dir
    if args.naming_strategy and "naming_strategy" not in output_kwargs:
        output_kwargs["naming_strategy"] = args.naming_strategy
    if args.stream:
        output_kwargs["format"] = "ndjson"
        output_kwargs["ndjson_filename"] = "-"
    elif args.format:
        output_kwargs["format"] = args.format
    if args.max_tokens_per_file is not None:
        output_kwargs["max_tokens_per_file"] = args.max_tokens_per_file
    if args.tokenizer:
        output_kwargs["tokenizer"] = args.tokenizer
    if args.emit_chunks:
        output_kwargs["emit_chunks"] = True
    if output_kwargs:
        config_kwargs["output"] = output_kwargs

    # Crawl settings
    crawl_kwargs: dict = {}
    if args.max_pages is not None:
        crawl_kwargs["max_pages"] = args.max_pages
    if args.max_depth is not None:
        crawl_kwargs["max_depth"] = args.max_depth
    if args.max_concurrent is not None:
        crawl_kwargs["max_concurrent"] = args.max_concurrent
    if args.per_host_concurrent is not None:
        crawl_kwargs["per_host_concurrent"] = args.per_host_concurrent
    if args.rate_limit is not None:
        crawl_kwargs["rate_limit"] = args.rate_limit
    if args.adaptive_rate_limit:
        crawl_kwargs["adaptive_rate_limit"] = True
    if args.no_streaming_discovery:
        crawl_kwargs["streaming_discovery"] = False
    if args.include_paths:
        crawl_kwargs["include_paths"] = args.include_paths
    if args.exclude_paths:
        crawl_kwargs["exclude_paths"] = args.exclude_paths
    if crawl_kwargs:
        config_kwargs["crawl"] = crawl_kwargs

    # Content filter settings
    filter_kwargs: dict = {}
    if args.streaming_dedup:
        filter_kwargs["streaming_dedup"] = True
    if args.extractor:
        filter_kwargs["extractor"] = args.extractor
    if args.no_special_cases:
        filter_kwargs["enable_special_cases"] = False
    if args.strict_js_required:
        filter_kwargs["strict_js_required"] = True
    if args.remote_documents:
        filter_kwargs["remote_documents"] = args.remote_documents
    if args.remote_document_backend:
        filter_kwargs["remote_document_backend"] = args.remote_document_backend
    if args.remote_document_timeout_seconds is not None:
        filter_kwargs["remote_document_timeout_seconds"] = args.remote_document_timeout_seconds
    if args.remote_document_memory_mib is not None:
        filter_kwargs["remote_document_memory_mib"] = args.remote_document_memory_mib
    if filter_kwargs:
        config_kwargs["content_filter"] = filter_kwargs

    # Network settings
    network_kwargs: dict = {}
    if args.proxy:
        network_kwargs["proxy"] = args.proxy
    if args.user_agent:
        network_kwargs["user_agent"] = args.user_agent
    if args.insecure_tls:
        console.print(
            "[red]Configuration error:[/red] --insecure-tls is no longer supported; "
            "docpull always verifies TLS certificates"
        )
        return 1
    if args.max_retries is not None:
        network_kwargs["max_retries"] = args.max_retries
    if args.require_pinned_dns:
        network_kwargs["require_pinned_dns"] = True
    if network_kwargs:
        config_kwargs["network"] = network_kwargs

    # Authentication settings
    auth_kwargs: dict = {}
    if args.auth_bearer:
        auth_kwargs["type"] = "bearer"
        auth_kwargs["token"] = args.auth_bearer
    elif args.auth_basic:
        auth_kwargs["type"] = "basic"
        if ":" in args.auth_basic:
            username, password = args.auth_basic.split(":", 1)
            auth_kwargs["username"] = username
            auth_kwargs["password"] = password
        else:
            console.print("[red]Error:[/red] --auth-basic requires format username:password")
            return 1
    elif args.auth_cookie:
        auth_kwargs["type"] = "cookie"
        auth_kwargs["cookie"] = args.auth_cookie
    elif args.auth_header:
        auth_kwargs["type"] = "header"
        auth_kwargs["header_name"] = args.auth_header[0]
        auth_kwargs["header_value"] = args.auth_header[1]
    if auth_kwargs:
        auth_kwargs["policy"] = args.auth_policy if args.auth_policy != "none" else "explicit-private"
        config_kwargs["auth"] = auth_kwargs

    render_kwargs: dict = {}
    if args.render != "off":
        render_kwargs["mode"] = args.render
        render_kwargs["runtime"] = args.render_runtime
    if args.render_timeout is not None:
        render_kwargs["timeout_seconds"] = args.render_timeout
    if args.render_wait_for:
        render_kwargs["wait_for"] = args.render_wait_for
    if args.render_allowed_domain:
        render_kwargs["allowed_domains"] = args.render_allowed_domain
    if args.render_viewport:
        render_kwargs["viewport"] = args.render_viewport
    if args.render_max_html_bytes:
        render_kwargs["max_html_bytes"] = args.render_max_html_bytes
    if args.render_cloud_agent_browser_install:
        render_kwargs["cloud_agent_browser_install"] = args.render_cloud_agent_browser_install
    if args.render_cloud_max_estimated_cost is not None:
        render_kwargs["cloud_max_estimated_cost_usd"] = args.render_cloud_max_estimated_cost
    if args.render_template:
        render_kwargs["e2b_template"] = args.render_template
    if render_kwargs:
        config_kwargs["render"] = render_kwargs

    if args.budget is not None:
        config_kwargs["budget"] = {"maximum_paid_cost_usd": args.budget}

    cache_kwargs: dict = {}
    if args.cache or args.resume:
        cache_kwargs["enabled"] = True
    if args.cache_dir:
        cache_kwargs["directory"] = args.cache_dir
    if args.cache_ttl is not None:
        cache_kwargs["ttl_days"] = args.cache_ttl
    if args.no_skip_unchanged:
        cache_kwargs["skip_unchanged"] = False
    if args.resume:
        cache_kwargs["resume"] = True
    if cache_kwargs:
        config_kwargs["cache"] = cache_kwargs

    # Log level
    if args.verbose:
        config_kwargs["log_level"] = "DEBUG"
    elif args.quiet:
        config_kwargs["log_level"] = "ERROR"

    try:
        config = DocpullConfig(**config_kwargs)
    except Exception as e:
        console.print("[red]Configuration error:[/red] " + escape(str(e)))
        return 1

    budget_limit = config.budget.maximum_paid_cost_usd
    render_is_cloud = config.render.enabled and config.render.backend in {"vercel-sandbox", "e2b-sandbox"}
    render_is_local = config.render.enabled and config.render.backend == "agent-browser"
    render_estimated_cost = (
        estimate_cloud_render_cost_usd(
            cast(Literal["vercel-sandbox", "e2b-sandbox"], config.render.backend),
            config.render,
        )
        if render_is_cloud
        else 0.0
    )
    route_steps = default_route_steps(
        include_local_render=render_is_local,
        include_cloud=render_is_cloud,
        budget_limit_usd=budget_limit,
    )
    if args.explain_route:
        console.print("[bold]Local-first route[/bold]")
        console.print(f"Budget: {'not set' if budget_limit is None else f'${budget_limit:.6f}'}")
        for step in route_steps:
            payload = step.to_dict()
            detail = f" - {payload['detail']}" if payload.get("detail") else ""
            console.print(f"- {payload['name']}: {payload['status']} ({payload['cost_class']}){detail}")
        return 0
    try:
        if render_is_cloud:
            enforce_paid_budget(
                f"render:{config.render.backend}",
                budget_limit_usd=budget_limit,
                estimated_cost_usd=render_estimated_cost,
                provider=config.render.backend,
            )
    except BudgetError as err:
        accounting = RunAccounting(
            budget_limit_usd=budget_limit,
            estimated_paid_cost_usd=render_estimated_cost,
            blocked_actions=[
                blocked_action(
                    f"render:{config.render.backend}",
                    budget_limit_usd=budget_limit,
                    estimated_cost_usd=render_estimated_cost,
                    provider=config.render.backend,
                )
            ],
            route_steps=route_steps,
            command="fetch",
        )
        maybe_write_run_accounting(
            config.output.directory,
            budget_limit_usd=budget_limit,
            paid_capable=True,
            accounting=accounting,
        )
        console.print("[red]Budget error:[/red] " + escape(str(err)))
        return 1

    acquisition_started_at = utc_now_iso()
    run_events: list = []

    def write_structured_result(
        stats: object,
        *,
        contexts: list | None = None,
        extra_failures: list | None = None,
    ) -> None:
        from .acquisition_workflows import write_cli_acquisition_contracts

        write_cli_acquisition_contracts(
            config=config,
            workflow="fetch" if args.single else "crawl",
            started_at=acquisition_started_at,
            stats=stats,
            events=run_events,
            contexts=contexts,
            extra_failures=extra_failures,
        )

    async def run() -> int:
        if not args.quiet:
            console.print(f"[bold blue]docpull[/bold blue] v{__version__}")
            console.print(f"Profile: {profile.value}")
            console.print(f"Target: {config.url}")
            console.print()

        try:
            async with fetcher_class(config) as fetcher:
                # Handle --preview-urls mode
                if args.preview_urls:
                    urls = await fetcher.discover()
                    console.print(f"[bold]Discovered {len(urls)} URLs:[/bold]")
                    for url in urls:
                        console.print(f"  {url}")
                    return 0

                # --single fast path: fetch just this URL, no discovery
                if args.single:
                    if config.url is None:
                        console.print("[red]Error:[/red] --single requires a URL")
                        return 1
                    ctx = await fetcher.fetch_one(config.url)
                    if ctx.error:
                        write_structured_result(fetcher.stats, contexts=[ctx])
                        console.print(f"[red]Failed:[/red] {ctx.error}")
                        return 1
                    if ctx.should_skip:
                        console.print(f"[yellow]Skipped:[/yellow] {ctx.skip_reason}")
                        failure_skips = {
                            SkipReason.URL_VALIDATION_FAILED,
                            SkipReason.ROBOTS_DISALLOWED,
                            SkipReason.HTTP_ERROR,
                            SkipReason.INVALID_CONTENT_TYPE,
                            SkipReason.NO_CONTENT_EXTRACTED,
                            SkipReason.NO_CONTENT_TO_SAVE,
                        }
                        write_structured_result(fetcher.stats, contexts=[ctx])
                        return 1 if ctx.skip_code in failure_skips else 0
                    if not args.quiet:
                        n_chunks = len(ctx.chunks) if ctx.chunks else 0
                        extra = f" ({n_chunks} chunks)" if n_chunks else ""
                        console.print(
                            f"[green]Saved:[/green] {ctx.output_path} [{ctx.source_type or 'generic'}]{extra}"
                        )
                    _write_fetch_accounting(
                        config=config,
                        stats=fetcher.stats,
                        route_steps=route_steps,
                        render_estimated_cost=render_estimated_cost,
                        paid_capable=render_is_cloud,
                    )
                    write_structured_result(fetcher.stats, contexts=[ctx])
                    return 0

                # Track skip reasons for summary
                from collections import defaultdict

                skip_counts: dict[SkipReason, int] = defaultdict(int)

                if args.quiet:
                    async for event in fetcher.run():
                        run_events.append(event)
                        if event.type == EventType.FETCH_SKIPPED and event.skip_reason:
                            skip_counts[event.skip_reason] += 1
                else:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                        transient=True,
                    ) as progress:
                        task = progress.add_task("Starting...", total=None)

                        async for event in fetcher.run():
                            run_events.append(event)
                            if event.type == EventType.STARTED:
                                progress.update(task, description=f"[cyan]{event.message}")
                            elif event.type == EventType.RESUMED:
                                progress.update(
                                    task, description=f"[yellow]Resuming with {event.total} pending URLs"
                                )
                            elif event.type == EventType.DISCOVERY_STARTED:
                                progress.update(task, description="[cyan]Discovering URLs...")
                            elif event.type == EventType.DISCOVERY_COMPLETE:
                                progress.update(task, description=f"[green]Found {event.total} URLs")
                            elif event.type == EventType.FETCH_PROGRESS:
                                processed = (
                                    event.processed_count
                                    if event.processed_count is not None
                                    else event.current
                                )
                                total = event.total if event.total is not None else "?"
                                saved = event.saved_count if event.saved_count is not None else "?"
                                skipped = event.skipped_count if event.skipped_count is not None else "?"
                                failed = event.failed_count if event.failed_count is not None else "?"
                                progress.update(
                                    task,
                                    description=(
                                        f"[cyan]Processed {processed}/{total} "
                                        f"(saved {saved}, skipped {skipped}, failed {failed}): {event.url}"
                                    ),
                                )
                            elif event.type == EventType.FETCH_SKIPPED:
                                if event.skip_reason:
                                    skip_counts[event.skip_reason] += 1
                                if args.verbose:
                                    reason = event.skip_reason.value if event.skip_reason else "unknown"
                                    console.print(f"[dim]Skipped: {event.url} ({reason})[/dim]")
                            elif event.type == EventType.FETCH_FAILED:
                                console.print(f"[red]Failed:[/red] {event.url} - {event.error}")
                            elif event.type == EventType.COMPLETED:
                                progress.update(task, description=f"[green]{event.message}")

                # Print stats
                stats = fetcher.stats
                _write_fetch_accounting(
                    config=config,
                    stats=stats,
                    route_steps=route_steps,
                    render_estimated_cost=render_estimated_cost,
                    paid_capable=render_is_cloud,
                    skip_counts=skip_counts,
                )
                write_structured_result(stats)
                if not args.quiet:
                    console.print()
                    console.print("[bold]Results:[/bold]")
                    console.print(f"  URLs discovered: {stats.urls_discovered}")
                    console.print(f"  Pages fetched: {stats.pages_fetched}")
                    console.print(f"  Pages skipped: {stats.pages_skipped}")
                    console.print(f"  Pages failed: {stats.pages_failed}")
                    console.print(f"  Duration: {stats.duration_seconds:.1f}s")

                    # Print skip reason summary if there were skips
                    if skip_counts:
                        console.print()
                        console.print("[bold]Skip Summary:[/bold]")
                        for reason, count in sorted(skip_counts.items(), key=lambda x: -x[1]):
                            console.print(f"  {reason.value}: {count}")

                exit_code = _fetch_exit_code(
                    stats,
                    config.output.directory,
                    allow_empty=args.dry_run,
                    exit_policy=args.exit_policy,
                )
                if exit_code and not args.quiet and stats.pages_fetched == 0 and stats.pages_failed == 0:
                    console.print("[yellow]No readable pages were fetched; output pack is empty.[/yellow]")
                return exit_code

        except Exception as e:
            from .contracts import workflow_failure_from_mapping

            failure = workflow_failure_from_mapping(
                {"error": str(e), "stage": "workflow", "code": "workflow_error"}
            )
            write_structured_result(
                FetchStats(pages_failed=1),
                extra_failures=[failure],
            )
            console.print("[red]Error:[/red] " + escape(str(e)))
            if args.verbose:
                import traceback

                traceback.print_exc()
            return 1

    return asyncio.run(run())


def run_render_cli(argv: list[str]) -> int:
    """Render one URL to local HTML plus rendered_pages.ndjson."""
    if argv and argv[0] == "init":
        return _run_render_init_cli(argv[1:])
    if argv and argv[0] == "doctor":
        return _run_render_doctor_cli()

    from rich.console import Console
    from rich.markup import escape

    from .accounting import (
        BudgetError,
        RunAccounting,
        blocked_action,
        default_route_steps,
        effective_budget_limit,
        enforce_paid_budget,
        maybe_write_run_accounting,
    )
    from .models.config import DEFAULT_CLOUD_ARTIFACT_PATH, RenderConfig
    from .rendering import (
        RenderError,
        estimate_cloud_render_cost_usd,
    )

    check_render_backend: Any = globals().get("check_render_backend_availability") or __getattr__(
        "check_render_backend_availability"
    )
    render_to_directory: Any = globals().get("render_url_to_directory") or __getattr__(
        "render_url_to_directory"
    )

    output_dir_explicit = any(
        arg in {"--output-dir", "-o"}
        or arg.startswith("--output-dir=")
        or (arg.startswith("-o") and arg != "-o")
        for arg in argv
    )

    parser = argparse.ArgumentParser(
        prog="docpull render",
        description="Render one public URL through an explicit local or cloud browser runtime",
        epilog="Helpers: render doctor | render init e2b|vercel",
    )
    parser.add_argument("url", nargs="?", help="URL to render")
    parser.add_argument(
        "--runtime",
        choices=["local", "vercel", "e2b"],
        default="local",
        help="Renderer runtime: local agent-browser, Vercel Sandbox, or E2B Sandbox",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check whether the selected render runtime is available and exit",
    )
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help=(
            "Actually render the URL with the selected runtime, using https://example.com "
            "when no URL is provided. May consume cloud provider quota."
        ),
    )
    parser.add_argument(
        "--vercel-sandbox-bin",
        default=None,
        metavar="BINARY",
        help="Vercel Sandbox CLI executable path for --runtime vercel",
    )
    parser.add_argument(
        "--agent-browser-bin",
        default=None,
        metavar="BINARY",
        help="agent-browser executable inside the selected runtime",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("./rendered"),
        help="Directory for rendered HTML and rendered_pages.ndjson",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        metavar="SECONDS",
        help="Renderer timeout",
    )
    parser.add_argument(
        "--wait-for",
        choices=["load", "domcontentloaded", "networkidle"],
        default="load",
        metavar="STATE",
        help="Load state to wait for before reading HTML",
    )
    parser.add_argument(
        "--allowed-domain",
        action="append",
        default=None,
        metavar="DOMAIN",
        help="Allowed render domain. May be repeated. Defaults to the URL host.",
    )
    parser.add_argument(
        "--viewport",
        default="1280x720",
        metavar="WIDTHxHEIGHT",
        help="Renderer viewport",
    )
    parser.add_argument(
        "--max-html-bytes",
        default="10mb",
        metavar="SIZE",
        help="Maximum rendered HTML size",
    )
    parser.add_argument(
        "--cloud-agent-browser-install",
        choices=["auto", "skip"],
        default="skip",
        help="Install agent-browser inside cloud sandboxes, or skip for prebuilt templates",
    )
    parser.add_argument(
        "--cloud-result-transport",
        choices=["auto", "stdout", "file"],
        default="auto",
        help="How cloud sandboxes return render payloads",
    )
    parser.add_argument(
        "--cloud-max-estimated-cost",
        type=float,
        default=None,
        metavar="USD",
        help="Fail cloud rendering when the estimated per-render cost exceeds this cap",
    )
    parser.add_argument(
        "--budget",
        type=_parse_budget_value,
        default=None,
        metavar="USD",
        help="Maximum paid-capable provider/cloud spend for this render. Use 0 for zero paid calls.",
    )
    parser.add_argument(
        "--explain-route",
        action="store_true",
        help="Print the local-first render route and exit without rendering.",
    )
    parser.add_argument(
        "--cloud-artifact-path",
        default=DEFAULT_CLOUD_ARTIFACT_PATH,
        metavar="PATH",
        help="Sandbox-local result artifact path for file-capable cloud runtimes",
    )
    parser.add_argument(
        "--template",
        default=None,
        metavar="TEMPLATE",
        help="Cloud runtime template name; currently used by --runtime e2b",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress success output",
    )
    args = parser.parse_args(argv)

    console = Console()
    backend = _render_backend_from_runtime(args.runtime)
    if args.check:
        binary = _availability_binary_for_runtime(
            args.runtime,
            agent_browser_binary=args.agent_browser_bin,
            vercel_sandbox_binary=args.vercel_sandbox_bin,
        )
        available, message = check_render_backend(backend, binary=binary)
        style = "green" if available else "yellow"
        console.print(f"[{style}]{escape(message)}[/{style}]")
        return 0 if available else 1
    if args.live_smoke and not args.url:
        args.url = "https://example.com"
    if not args.url:
        parser.error("url is required unless --check is used")
    try:
        config = RenderConfig(
            mode="agent-browser",
            backend=backend,
            timeout_seconds=args.timeout,
            wait_for=args.wait_for,
            allowed_domains=args.allowed_domain or [],
            viewport=args.viewport,
            max_html_bytes=args.max_html_bytes,
            cloud_agent_browser_install=args.cloud_agent_browser_install,
            cloud_result_transport=args.cloud_result_transport,
            cloud_max_estimated_cost_usd=args.cloud_max_estimated_cost,
            cloud_artifact_path=args.cloud_artifact_path,
            cloud_agent_browser_binary=args.agent_browser_bin or "agent-browser",
            e2b_template=args.template,
        )
    except Exception as e:
        console.print("[red]Configuration error:[/red] " + escape(str(e)))
        return 1
    budget_limit = effective_budget_limit(
        args.budget,
        args.cloud_max_estimated_cost if backend in {"vercel-sandbox", "e2b-sandbox"} else None,
    )
    cloud_estimated_cost = (
        estimate_cloud_render_cost_usd(backend, config)
        if backend in {"vercel-sandbox", "e2b-sandbox"}
        else 0.0
    )
    route_steps = default_route_steps(
        include_local_render=backend == "agent-browser",
        include_cloud=backend in {"vercel-sandbox", "e2b-sandbox"},
        budget_limit_usd=budget_limit,
    )
    if args.explain_route:
        console.print("[bold]Local-first render route[/bold]")
        console.print(f"Budget: {'not set' if budget_limit is None else f'${budget_limit:.6f}'}")
        for step in route_steps:
            payload = step.to_dict()
            detail = f" - {payload['detail']}" if payload.get("detail") else ""
            console.print(f"- {payload['name']}: {payload['status']} ({payload['cost_class']}){detail}")
        return 0
    try:
        if backend in {"vercel-sandbox", "e2b-sandbox"}:
            enforce_paid_budget(
                f"render:{backend}",
                budget_limit_usd=budget_limit,
                estimated_cost_usd=cloud_estimated_cost,
                provider=backend,
            )
    except BudgetError as e:
        if not args.live_smoke:
            maybe_write_run_accounting(
                args.output_dir,
                budget_limit_usd=budget_limit,
                paid_capable=True,
                accounting=RunAccounting(
                    budget_limit_usd=budget_limit,
                    estimated_paid_cost_usd=cloud_estimated_cost,
                    blocked_actions=[
                        blocked_action(
                            f"render:{backend}",
                            budget_limit_usd=budget_limit,
                            estimated_cost_usd=cloud_estimated_cost,
                            provider=backend,
                        )
                    ],
                    route_steps=route_steps,
                    command="render",
                ),
            )
        console.print("[red]Budget error:[/red] " + escape(str(e)))
        return 1

    async def run() -> int:
        output_dir = args.output_dir
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if args.live_smoke and not output_dir_explicit:
            temp_dir = tempfile.TemporaryDirectory(prefix="docpull-render-smoke-")
            output_dir = Path(temp_dir.name)
        try:
            renderer = _renderer_for_render_cli_backend(
                backend,
                vercel_sandbox_binary=args.vercel_sandbox_bin,
                agent_browser_binary=args.agent_browser_bin,
            )
            artifact = await render_to_directory(
                args.url,
                output_dir,
                config=config,
                renderer=renderer,
            )
        except RenderError as e:
            console.print("[red]Render failed:[/red] " + escape(str(e)))
            return 1
        except Exception as e:
            console.print("[red]Error:[/red] " + escape(str(e)))
            return 1
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()
        if not args.quiet:
            label = "Render smoke passed" if args.live_smoke else "Rendered"
            console.print(f"[green]{label}:[/green] {args.url}")
            if not args.live_smoke:
                console.print(f"[green]HTML:[/green] {artifact.html_path}")
                console.print(f"[green]Metadata:[/green] {artifact.sidecar_path}")
        if not args.live_smoke:
            maybe_write_run_accounting(
                output_dir,
                budget_limit_usd=budget_limit,
                paid_capable=backend in {"vercel-sandbox", "e2b-sandbox"},
                accounting=RunAccounting(
                    budget_limit_usd=budget_limit,
                    estimated_paid_cost_usd=cloud_estimated_cost,
                    paid_request_count=1 if backend in {"vercel-sandbox", "e2b-sandbox"} else 0,
                    local_browser_seconds=0.0,
                    route_steps=route_steps,
                    command="render",
                ),
            )
        return 0

    return asyncio.run(run())


def _run_render_doctor_cli() -> int:
    from rich.console import Console
    from rich.markup import escape

    check_render_backend: Any = globals().get("check_render_backend_availability") or __getattr__(
        "check_render_backend_availability"
    )

    console = Console()
    checks = [
        ("local", "agent-browser"),
        ("vercel", "vercel-sandbox"),
        ("e2b", "e2b-sandbox"),
    ]
    exit_code = 0
    for runtime, backend in checks:
        available, message = check_render_backend(backend)
        style = "green" if available else "yellow"
        console.print(f"[{style}]{runtime}:[/{style}] {escape(message)}")
        if runtime == "local" and not available:
            exit_code = 1
    console.print()
    console.print("Cloud runtimes execute the same agent-browser JSON contract inside a sandbox/template.")
    console.print("Set DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 only for trusted render targets.")
    console.print("Use `docpull render init e2b` or `docpull render init vercel` for template recipes.")
    return exit_code


def _run_render_init_cli(argv: list[str]) -> int:
    from rich.console import Console

    parser = argparse.ArgumentParser(
        prog="docpull render init",
        description="Print a sandbox template recipe for agent-browser rendering",
    )
    parser.add_argument("runtime", choices=["e2b", "vercel"])
    parser.add_argument("--template", default="docpull-agent-browser")
    args = parser.parse_args(argv)

    console = Console(width=120)
    if args.runtime == "e2b":
        console.print(_render_init_e2b(args.template))
        return 0
    if args.runtime == "vercel":
        console.print(_render_init_vercel(args.template))
        return 0
    raise AssertionError(f"Unhandled render init runtime: {args.runtime}")


def _render_init_e2b(template: str) -> str:
    return f"""# E2B agent-browser template
# Template name: {template}

# In an E2B template, install agent-browser once:
npm install -g agent-browser
agent-browser install

# Then render through the same DocPull contract:
export E2B_API_KEY=<your-e2b-api-key>
export DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1
docpull render https://example.com --runtime e2b --template {template}

# For smoke tests that may consume provider quota:
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 \\
  DOCPULL_LIVE_CLOUD_RENDER=1 \\
  .venv/bin/python -m pytest tests/test_rendering.py -q
"""


def _render_init_vercel(template: str) -> str:
    return f"""# Vercel Sandbox agent-browser runtime
# Runtime label: {template}

# Build or choose a Vercel Sandbox runtime that has:
npm install -g agent-browser
agent-browser install

# Then render through the same DocPull contract:
export DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1
docpull render https://example.com --runtime vercel --cloud-agent-browser-install skip

# If you intentionally want a cold runtime install and npm is available:
docpull render https://example.com --runtime vercel --cloud-agent-browser-install auto
"""


def _render_backend_from_runtime(runtime: str) -> RenderBackend:
    runtime_to_backend: dict[str, RenderBackend] = {
        "local": "agent-browser",
        "vercel": "vercel-sandbox",
        "e2b": "e2b-sandbox",
    }
    return runtime_to_backend[runtime]


def _availability_binary_for_runtime(
    runtime: str,
    *,
    agent_browser_binary: str | None,
    vercel_sandbox_binary: str | None,
) -> str | None:
    if runtime == "local":
        return agent_browser_binary
    if runtime == "vercel":
        return vercel_sandbox_binary
    return None


def _renderer_for_render_cli_backend(
    backend: RenderBackend,
    *,
    vercel_sandbox_binary: str | None,
    agent_browser_binary: str | None,
) -> Renderer | None:
    from .rendering import AgentBrowserRenderer, VercelSandboxRenderer

    if backend == "agent-browser":
        return AgentBrowserRenderer(binary=agent_browser_binary) if agent_browser_binary else None
    if backend == "vercel-sandbox":
        return VercelSandboxRenderer(binary=vercel_sandbox_binary) if vercel_sandbox_binary else None
    return None


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    if raw_argv and raw_argv[0] in PRUNED_CLI_COMMANDS:
        command = raw_argv[0]
        print(
            f"docpull: error: '{command}' was removed from the public v3 surface. "
            "Use root URL fetch, typed *-pack lanes, `docpull pack`, `docpull export`, or "
            "`docpull ci` instead.",
            file=sys.stderr,
        )
        return 2
    if raw_argv and raw_argv[0] == "render":
        return run_render_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "init":
        from .project import run_init_cli

        return run_init_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "add":
        from .project import run_add_cli

        return run_add_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "install":
        from .project import run_install_cli

        return run_install_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "deps":
        from .project import run_deps_cli

        return run_deps_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "sources":
        from .project import run_sources_cli

        return run_sources_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "sync":
        from .project import run_sync_cli

        return run_sync_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "diff":
        from .project import run_diff_cli

        return run_diff_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "status":
        from .project import run_status_cli

        return run_status_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "history":
        from .project import run_history_cli

        return run_history_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "review":
        from .project import run_review_cli

        return run_review_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "release":
        from .project import run_release_cli

        return run_release_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "ci":
        from .context_ci import run_context_ci_cli

        return run_context_ci_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "watch":
        from .project import run_watch_cli

        return run_watch_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "parse":
        from .document_parse import run_parse_cli

        return run_parse_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "mcp":
        from .mcp.server import run_mcp_server

        return run_mcp_server(raw_argv[1:])
    if raw_argv and raw_argv[0] == "pack":
        from .pack_tools import run_pack_cli

        return run_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "contracts":
        from .contracts_cli import run_contracts_cli

        return run_contracts_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "graph":
        from .graph import run_graph_cli

        return run_graph_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "serve":
        from .server import run_serve_cli

        return run_serve_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "share":
        from .share import run_share_cli

        return run_share_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "export":
        if len(raw_argv) > 1 and raw_argv[1] == "context-pack":
            from .project import run_project_export_cli

            return run_project_export_cli(raw_argv[2:])
        from .exports import run_export_cli

        return run_export_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "refresh":
        from .local_workflows import run_refresh_cli

        return run_refresh_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "policy":
        from .policy_cli import run_policy_cli

        return run_policy_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "auth":
        from .auth_cli import run_auth_cli

        return run_auth_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "monitor":
        from .monitor import run_monitor_cli

        return run_monitor_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] in {
        "brand-pack",
        "product-pack",
        "styleguide-pack",
        "image-pack",
        "screenshot-pack",
        "policy-pack",
        "relationship-pack",
    }:
        from .context_packs.workflow_cli import (
            run_brand_pack_cli,
            run_image_pack_cli,
            run_policy_pack_cli,
            run_product_pack_cli,
            run_relationship_pack_cli,
            run_screenshot_pack_cli,
            run_styleguide_pack_cli,
        )

        workflow_runners = {
            "brand-pack": run_brand_pack_cli,
            "product-pack": run_product_pack_cli,
            "styleguide-pack": run_styleguide_pack_cli,
            "image-pack": run_image_pack_cli,
            "screenshot-pack": run_screenshot_pack_cli,
            "policy-pack": run_policy_pack_cli,
            "relationship-pack": run_relationship_pack_cli,
        }
        return workflow_runners[raw_argv[0]](raw_argv[1:])
    if raw_argv and raw_argv[0] == "openapi-pack":
        from .context_packs.cli import run_openapi_pack_cli

        return run_openapi_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "feed-pack":
        from .context_packs.cli import run_feed_pack_cli

        return run_feed_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "paper-pack":
        from .context_packs.cli import run_paper_pack_cli

        return run_paper_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "repo-pack":
        from .context_packs.cli import run_repo_pack_cli

        return run_repo_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "package-pack":
        from .context_packs.cli import run_package_pack_cli

        return run_package_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "standards-pack":
        from .context_packs.cli import run_standards_pack_cli

        return run_standards_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "dataset-pack":
        from .context_packs.cli import run_dataset_pack_cli

        return run_dataset_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "transcript-pack":
        from .context_packs.cli import run_transcript_pack_cli

        return run_transcript_pack_cli(raw_argv[1:])
    if raw_argv and raw_argv[0] == "wiki-pack":
        from .context_packs.cli import run_wiki_pack_cli

        return run_wiki_pack_cli(raw_argv[1:])

    parser = create_parser()
    args = parser.parse_args(raw_argv)

    if args.doctor:
        from .doctor import run_doctor

        return run_doctor(output_dir=args.output_dir)

    return run_fetcher(args)


if __name__ == "__main__":
    sys.exit(main())
