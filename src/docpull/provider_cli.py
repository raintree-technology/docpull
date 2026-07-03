"""Provider-neutral CLI for optional live context-pack providers."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .accounting import (
    BudgetError,
    RunAccounting,
    blocked_action,
    budget_block_payload,
    default_route_steps,
    effective_budget_limit,
    enforce_paid_budget,
    extract_budget_flags,
    maybe_write_run_accounting,
    paid_action_blocked,
)
from .benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DEFAULT_INCLUDE_DOMAIN,
    DEFAULT_MAX_ESTIMATED_COST_USD,
    DEFAULT_MODE,
    DEFAULT_OBJECTIVE,
    DEFAULT_QUERY,
    BenchmarkError,
    _run_parallel_context_case,
)
from .parallel_workflows import _build_source_policy, _parallel_sdk_installed, estimate_context_pack_cost
from .provider_adapters import (
    ProviderAdapterError,
    live_provider_statuses,
    normalize_live_providers,
    provider_adapter,
    provider_case_payload,
)
from .provider_capabilities import provider_capabilities
from .provider_keys import (
    PROJECT_ENV_FILENAME,
    PROVIDER_CONFIGS,
    PROVIDER_NAMES,
    ProviderKeyError,
    ProviderName,
    user_secrets_path,
    validate_provider_api_key,
    write_provider_secret,
)
from .provider_probes import (
    DEFAULT_PROBE_TIMEOUT_SECONDS,
    DEFAULT_SMOKE_MAX_ESTIMATED_COST_USD,
    ProviderProbeError,
    provider_probe_payload,
)
from .time_utils import utc_now_iso

DEFAULT_PROVIDER_OUTPUT_DIR = Path("packs/provider-context-packs")
DEFAULT_PROVIDER_EXTRACT_OUTPUT_DIR = Path("packs/provider-extract-pack")
DEFAULT_PROVIDER_MAP_OUTPUT_DIR = Path("packs/provider-map-pack")


def _live_provider_statuses(providers: list[ProviderName]) -> dict[str, dict[str, Any]]:
    return live_provider_statuses(providers, parallel_sdk_installed=_parallel_sdk_installed)


_normalize_live_providers = normalize_live_providers
_provider_adapter = provider_adapter
_provider_capabilities = provider_capabilities
_provider_case_payload = provider_case_payload


class ProviderCliError(RuntimeError):
    """User-facing provider CLI error."""


def create_provider_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull providers",
        description="Manage optional Parallel, Tavily, and Exa provider keys and context packs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="Show non-secret local provider readiness")
    auth.add_argument(
        "--provider",
        action="append",
        choices=[*PROVIDER_NAMES],
        default=[],
        help="Provider to check. Repeat as needed; defaults to all providers.",
    )
    auth.add_argument("--json", action="store_true", dest="json_output", help="Print status JSON")
    auth.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless every requested provider is ready.",
    )
    auth.add_argument(
        "--redact-paths",
        action="store_true",
        help="Redact local filesystem paths from JSON output for CI/agent logs.",
    )

    capabilities = subparsers.add_parser("capabilities", help="Show provider capability matrix")
    capabilities.add_argument(
        "--provider",
        action="append",
        choices=[*PROVIDER_NAMES],
        default=[],
        help="Provider to show. Repeat as needed; defaults to all providers.",
    )
    capabilities.add_argument("--json", action="store_true", dest="json_output", help="Print matrix JSON")

    probe = subparsers.add_parser("probe", help="Explicitly validate provider keys with live provider calls")
    probe.add_argument(
        "--provider",
        action="append",
        choices=[*PROVIDER_NAMES],
        default=[],
        help="Provider to probe. Repeat as needed; defaults to all providers.",
    )
    probe.add_argument(
        "--mode",
        choices=["safe", "validation", "smoke"],
        default="safe",
        help=(
            "Probe depth. safe uses non-search account endpoints where available; "
            "validation may call provider APIs without a real workflow; smoke runs minimal "
            "live provider calls."
        ),
    )
    probe.add_argument("--json", action="store_true", dest="json_output", help="Print probe JSON")
    probe.add_argument(
        "--require-verified",
        action="store_true",
        help="Exit non-zero unless every requested provider is live verified and workflow-ready.",
    )
    probe.add_argument(
        "--redact-paths",
        action="store_true",
        help="Redact local filesystem paths from JSON output for CI/agent logs.",
    )
    probe.add_argument(
        "--include-account-metadata",
        action="store_true",
        help="Include provider account/team metadata returned by safe probes.",
    )
    probe.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PROBE_TIMEOUT_SECONDS,
        help=f"Per-request probe timeout in seconds (default: {DEFAULT_PROBE_TIMEOUT_SECONDS}).",
    )
    probe.add_argument(
        "--max-estimated-cost",
        type=float,
        default=DEFAULT_SMOKE_MAX_ESTIMATED_COST_USD,
        help=(f"Local spend guard for smoke probes (default: {DEFAULT_SMOKE_MAX_ESTIMATED_COST_USD})."),
    )

    init = subparsers.add_parser("init", help="Store one provider API key locally")
    init.add_argument("provider", choices=[*PROVIDER_NAMES])
    init.add_argument("--project", action="store_true", help="Write .env.local in the current project")
    init.add_argument("--from-stdin", action="store_true", help="Read the API key from stdin")
    init.add_argument("--force", action="store_true", help="Overwrite an existing key entry")
    init.add_argument(
        "--no-gitignore-update",
        action="store_true",
        help="With --project, do not add .env.local to .gitignore.",
    )

    context = subparsers.add_parser(
        "context-pack",
        help="Build comparable context packs with any configured live providers",
    )
    context.add_argument("objective", nargs="?", default=DEFAULT_OBJECTIVE)
    context.add_argument(
        "--provider",
        action="append",
        choices=["auto", "all", *PROVIDER_NAMES],
        default=[],
        help=(
            "Provider to use. Defaults to auto, which runs locally ready providers. "
            "Use all to request every provider and skip unavailable ones."
        ),
    )
    context.add_argument("--query", action="append", dest="queries", default=[])
    context.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_PROVIDER_OUTPUT_DIR)
    context.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    context.add_argument("--mode", choices=["turbo", "basic", "advanced"], default=DEFAULT_MODE)
    context.add_argument("--max-search-results", type=int, default=8)
    context.add_argument("--extract-limit", type=int, default=3)
    context.add_argument(
        "--max-estimated-cost",
        type=float,
        default=DEFAULT_MAX_ESTIMATED_COST_USD,
        help="Local pre-call spend guard for providers with known cost estimates",
    )
    context.add_argument("--dry-run", action="store_true", help="Plan provider cases without live calls")
    context.add_argument("--json", action="store_true", dest="json_output", help="Print report JSON")

    extract = subparsers.add_parser(
        "extract-pack",
        help="Extract known URLs with Tavily or Exa and write a local context pack",
    )
    extract.add_argument("urls", nargs="*", help="HTTPS URLs to extract")
    extract.add_argument("--provider", required=True, choices=["tavily", "exa"])
    extract.add_argument("--url-file", type=Path, help="JSON array or newline-delimited URL file")
    extract.add_argument("--objective", default="Extract known URLs into a provider context pack")
    extract.add_argument("--query", action="append", dest="queries", default=[])
    extract.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_PROVIDER_EXTRACT_OUTPUT_DIR)
    extract.add_argument("--mode", choices=["turbo", "basic", "advanced"], default=DEFAULT_MODE)
    extract.add_argument("--dry-run", action="store_true", help="Plan provider extraction without live calls")
    extract.add_argument("--json", action="store_true", dest="json_output", help="Print report JSON")

    map_pack = subparsers.add_parser(
        "map-pack",
        help="Map one site with Tavily and write a provider-neutral discovery pack",
    )
    map_pack.add_argument("url", help="HTTPS URL to map")
    map_pack.add_argument("--provider", choices=["tavily"], default="tavily")
    map_pack.add_argument("--objective")
    map_pack.add_argument("--query")
    map_pack.add_argument("--instructions", help="Provider instructions for the Tavily Map request")
    map_pack.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_PROVIDER_MAP_OUTPUT_DIR)
    map_pack.add_argument(
        "--include-domain",
        action="append",
        dest="include_domains",
        default=[],
        help="Allowed domain for the resulting discovery policy. Defaults to the URL host.",
    )
    map_pack.add_argument(
        "--exclude-domain",
        action="append",
        dest="exclude_domains",
        default=[],
        help="Denied domain for the resulting discovery policy and Tavily request.",
    )
    map_pack.add_argument(
        "--select-path",
        action="append",
        dest="select_paths",
        default=[],
        help="Path glob to pass to Tavily Map and the resulting source policy.",
    )
    map_pack.add_argument(
        "--select-domain",
        action="append",
        dest="select_domains",
        default=[],
        help="Domain selector to pass to Tavily Map.",
    )
    map_pack.add_argument(
        "--exclude-path",
        action="append",
        dest="exclude_paths",
        default=[],
        help="Path glob to exclude from Tavily Map and the resulting source policy.",
    )
    map_pack.add_argument("--max-depth", type=int, default=1)
    map_pack.add_argument("--max-breadth", type=int, default=20)
    map_pack.add_argument("--limit", type=int, default=50)
    map_pack.add_argument(
        "--allow-external",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Allow Tavily Map to include external links.",
    )
    map_pack.add_argument("--timeout", type=float, default=150.0)
    map_pack.add_argument("--dry-run", action="store_true", help="Plan map-pack without a live call")
    map_pack.add_argument("--json", action="store_true", dest="json_output", help="Print report JSON")

    return parser


def run_provider_cli(argv: list[str] | None = None) -> int:
    try:
        parsed_argv, budget_limit, explain_route = extract_budget_flags(argv)
    except ValueError as err:
        Console().print("[red]Provider error:[/red] " + escape(str(err)))
        return 1
    parser = create_provider_parser()
    args = parser.parse_args(parsed_argv)
    console = Console()
    route_steps = default_route_steps(include_provider=True, budget_limit_usd=budget_limit)

    if explain_route:
        console.print("[bold]Provider route[/bold]")
        console.print(f"Budget: {'not set' if budget_limit is None else f'${budget_limit:.6f}'}")
        for step in route_steps:
            payload = step.to_dict()
            detail = f" - {payload['detail']}" if payload.get("detail") else ""
            console.print(f"- {payload['name']}: {payload['status']} ({payload['cost_class']}){detail}")
        return 0

    try:
        if args.command == "auth":
            statuses = provider_auth_statuses(args.provider or list(PROVIDER_NAMES))
            payload = _provider_auth_payload(statuses, redact_paths=args.redact_paths)
            if args.json_output:
                console.print_json(data=payload)
            else:
                _print_provider_auth_status(console, payload)
            if args.require_ready and not all(status["ready"] for status in statuses.values()):
                return 1
            return 0
        if args.command == "capabilities":
            capabilities = provider_capability_payload(args.provider or list(PROVIDER_NAMES))
            if args.json_output:
                console.print_json(data=capabilities)
            else:
                _print_provider_capabilities(console, capabilities)
            return 0
        if args.command == "probe":
            if args.mode in {"validation", "smoke"}:
                estimate = args.max_estimated_cost if args.mode == "smoke" else 0.0
                try:
                    enforce_paid_budget(
                        f"providers:probe:{args.mode}",
                        budget_limit_usd=budget_limit,
                        estimated_cost_usd=estimate,
                        provider="providers",
                    )
                except BudgetError as err:
                    if args.json_output:
                        console.print_json(
                            data={
                                "schema_version": 1,
                                "generated_at": utc_now_iso(),
                                "mode": args.mode,
                                **budget_block_payload(
                                    f"providers:probe:{args.mode}",
                                    budget_limit_usd=budget_limit,
                                    estimated_cost_usd=estimate,
                                    provider="providers",
                                ),
                            }
                        )
                    else:
                        console.print("[red]Provider error:[/red] " + escape(str(err)))
                    return 0 if args.json_output else 1
            payload = provider_probe_payload(
                args.provider or list(PROVIDER_NAMES),
                mode=args.mode,
                include_account_metadata=args.include_account_metadata,
                redact_paths=args.redact_paths,
                timeout=args.timeout,
                max_estimated_cost=args.max_estimated_cost,
            )
            if args.json_output:
                console.print_json(data=payload)
            else:
                _print_provider_probe_status(console, payload)
            if args.require_verified and not _provider_probe_payload_verified(payload):
                return 1
            return 0
        if args.command == "init":
            result = init_provider_auth(
                args.provider,
                project=args.project,
                from_stdin=args.from_stdin,
                force=args.force,
                update_gitignore=not args.no_gitignore_update,
            )
            console.print(
                f"[green]Stored {result['label']} API key:[/green] {result['key_source']} -> {result['path']}"
            )
            if result.get("gitignore_updated"):
                console.print(f"[green]Updated .gitignore:[/green] {result['gitignore_path']}")
            console.print("Secret handling: key value was not printed and is not written to pack artifacts.")
            return 0
        if args.command == "context-pack":
            report = run_provider_context_packs(
                objective=args.objective,
                queries=args.queries or [DEFAULT_QUERY],
                providers=args.provider or ["auto"],
                output_dir=args.output_dir,
                include_domains=args.include_domains or [DEFAULT_INCLUDE_DOMAIN],
                mode=args.mode,
                max_search_results=args.max_search_results,
                extract_limit=args.extract_limit,
                max_estimated_cost=args.max_estimated_cost,
                dry_run=args.dry_run,
                budget_limit=budget_limit,
            )
            _write_provider_report_accounting(
                args.output_dir,
                budget_limit=budget_limit,
                estimated_cost=_estimated_provider_report_cost(report),
                blocked_actions=_blocked_actions_from_report(report),
                route_steps=route_steps,
                command="providers context-pack",
                dry_run=args.dry_run,
            )
            if args.json_output or args.dry_run:
                console.print_json(data=report)
            else:
                console.print(
                    "[green]Provider context-pack report:[/green] "
                    f"{report['artifacts']['json']} "
                    f"({len(report['cases'])} provider cases)"
                )
                if report["skipped_providers"]:
                    skipped = ", ".join(item["provider"] for item in report["skipped_providers"])
                    console.print(f"[yellow]Skipped unavailable providers:[/yellow] {skipped}")
            return 0
        if args.command == "extract-pack":
            report = run_provider_extract_pack(
                provider=args.provider,
                urls=args.urls,
                url_file=args.url_file,
                objective=args.objective,
                queries=args.queries,
                output_dir=args.output_dir,
                mode=args.mode,
                dry_run=args.dry_run,
                budget_limit=budget_limit,
            )
            _write_provider_report_accounting(
                args.output_dir,
                budget_limit=budget_limit,
                estimated_cost=float(report.get("estimated_cost_usd") or 0.0),
                blocked_actions=_blocked_actions_from_report(report),
                route_steps=route_steps,
                command="providers extract-pack",
                dry_run=args.dry_run,
            )
            if args.json_output or args.dry_run:
                console.print_json(data=report)
            else:
                console.print(
                    "[green]Provider extract pack:[/green] "
                    f"{report['artifacts']['pack']} "
                    f"({report['record_count']} records)"
                )
            return 0
        if args.command == "map-pack":
            report = run_provider_map_pack(
                provider=args.provider,
                url=args.url,
                objective=args.objective,
                query=args.query,
                instructions=args.instructions,
                output_dir=args.output_dir,
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                select_paths=args.select_paths,
                select_domains=args.select_domains,
                exclude_paths=args.exclude_paths,
                max_depth=args.max_depth,
                max_breadth=args.max_breadth,
                limit=args.limit,
                allow_external=args.allow_external,
                timeout=args.timeout,
                dry_run=args.dry_run,
                budget_limit=budget_limit,
            )
            _write_provider_report_accounting(
                args.output_dir,
                budget_limit=budget_limit,
                estimated_cost=float(report.get("estimated_cost_usd") or 0.0),
                blocked_actions=_blocked_actions_from_report(report),
                route_steps=route_steps,
                command="providers map-pack",
                dry_run=args.dry_run,
            )
            if args.json_output or args.dry_run:
                console.print_json(data=report)
            else:
                console.print(
                    "[green]Provider map pack:[/green] "
                    f"{report['artifacts']['pack']} "
                    f"({report['candidate_count']} candidates)"
                )
            return 0
        parser.error(f"Unknown command: {args.command}")
    except (BenchmarkError, ProviderAdapterError, ProviderCliError, ProviderProbeError) as err:
        if getattr(args, "json_output", False):
            console.print_json(data=_provider_error_payload(err, command=args.command))
            return 1
        console.print("[red]Provider error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        if getattr(args, "json_output", False):
            console.print_json(data=_provider_error_payload(err, command=args.command))
            return 1
        console.print("[red]Provider command failed:[/red] " + escape(str(err)))
        return 1
    return 1


def run_provider_extension_cli(provider: str, argv: list[str] | None = None) -> int:
    name = _provider_name(provider)
    raw_argv = list(argv or [])
    if not raw_argv or raw_argv[0] in {"-h", "--help"}:
        console = Console()
        console.print(f"[bold]{PROVIDER_CONFIGS[name].label} provider extension[/bold]")
        commands = ["auth", "probe", "init", "capabilities", "context-pack", "extract-pack"]
        if name == "tavily":
            commands.append("map-pack")
        console.print(f"Usage: docpull {name} <command> [options]", markup=False)
        console.print("Commands: " + ", ".join(commands))
        console.print("")
        console.print("Common agent-safe commands:")
        console.print(f"  docpull {name} auth --json --require-ready --redact-paths")
        console.print(f"  docpull {name} probe --json --require-verified --redact-paths")
        console.print(f"  docpull {name} capabilities --json")
        console.print(f'  docpull {name} context-pack "Find official docs" --dry-run --json')
        if name == "tavily":
            console.print("  docpull tavily map-pack https://docs.example.com --dry-run --json")
        console.print(f"Equivalent provider command: docpull providers <command> --provider {name}")
        return 0
    command = raw_argv[0]
    tail = raw_argv[1:]
    if command == "auth":
        return run_provider_cli(["auth", "--provider", name, *tail])
    if command == "probe":
        return run_provider_cli(["probe", "--provider", name, *tail])
    if command == "init":
        return run_provider_cli(["init", name, *tail])
    if command == "capabilities":
        return run_provider_cli(["capabilities", "--provider", name, *tail])
    if command == "map-pack" and name != "tavily":
        console = Console()
        console.print(
            "[red]Provider error:[/red] "
            + escape("map-pack is currently backed by Tavily Map; use `docpull tavily map-pack`.")
        )
        return 1
    if command in {"context-pack", "extract-pack", "map-pack"}:
        return run_provider_cli([command, *tail, "--provider", name])
    console = Console()
    console.print(
        "[red]Provider error:[/red] "
        + escape(f"Unsupported {PROVIDER_CONFIGS[name].label} command: {command}")
    )
    return 1


def provider_auth_statuses(providers: list[str]) -> dict[str, dict[str, Any]]:
    normalized = _normalize_live_providers(
        parallel=False,
        tavily=False,
        exa=False,
        live_providers=providers,
    )
    return _live_provider_statuses(normalized)


def provider_capability_payload(providers: list[str]) -> dict[str, Any]:
    selected = [_provider_name(provider) for provider in providers] if providers else list(PROVIDER_NAMES)
    capabilities: dict[str, list[dict[str, Any]]] = {}
    for provider in selected:
        capabilities.update(_provider_capabilities(provider))
    available_count = sum(
        1
        for provider_capabilities in capabilities.values()
        for capability in provider_capabilities
        if capability["status"] == "available"
    )
    planned_count = sum(
        1
        for provider_capabilities in capabilities.values()
        for capability in provider_capabilities
        if capability["status"] == "planned"
    )
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "providers": selected,
        "capabilities": capabilities,
        "available_count": available_count,
        "planned_count": planned_count,
        "guidance": (
            "All providers share context-pack and extract-pack where possible; provider-specific "
            "capabilities stay explicit when their API shape differs."
        ),
    }


def init_provider_auth(
    provider: str,
    *,
    project: bool = False,
    from_stdin: bool = False,
    force: bool = False,
    update_gitignore: bool = True,
) -> dict[str, Any]:
    name = _provider_name(provider)
    config = PROVIDER_CONFIGS[name]
    value = sys.stdin.readline() if from_stdin else getpass.getpass(f"{config.label} API key: ")
    try:
        api_key = validate_provider_api_key(value, label=f"{config.label} API key")
    except ProviderKeyError as err:
        raise ProviderCliError(str(err)) from err
    if project:
        path = Path.cwd() / PROJECT_ENV_FILENAME
        key_source = "project_env"
    else:
        path = user_secrets_path()
        key_source = "user_config"
    try:
        write_provider_secret(name, path, api_key, force=force)
    except FileExistsError as err:
        raise ProviderCliError(str(err)) from err
    except ProviderKeyError as err:
        raise ProviderCliError(str(err)) from err
    gitignore_path: Path | None = None
    gitignore_updated = False
    if project and update_gitignore:
        gitignore_path, gitignore_updated = _ensure_gitignore_entry(Path.cwd(), PROJECT_ENV_FILENAME)
    return {
        "provider": name,
        "label": config.label,
        "path": str(path),
        "key_source": key_source,
        "gitignore_path": str(gitignore_path) if gitignore_path else None,
        "gitignore_updated": gitignore_updated,
    }


def run_provider_map_pack(
    *,
    provider: str,
    url: str,
    objective: str | None,
    query: str | None,
    instructions: str | None,
    output_dir: Path,
    include_domains: list[str],
    exclude_domains: list[str],
    select_paths: list[str],
    select_domains: list[str],
    exclude_paths: list[str],
    max_depth: int,
    max_breadth: int,
    limit: int,
    allow_external: bool,
    timeout: float,
    dry_run: bool,
    budget_limit: float | None = None,
) -> dict[str, Any]:
    name = _provider_name(provider)
    if name != "tavily":
        raise ProviderCliError("map-pack currently supports Tavily only.")
    _validate_https_url(url, field="map-pack URL")
    _validate_path_patterns(select_paths, field="select-path")
    _validate_path_patterns(exclude_paths, field="exclude-path")
    effective_include_domains = include_domains or [_url_hostname(url)]
    map_exclude_domains = list(exclude_domains)
    request_options = {
        "include_domains": effective_include_domains,
        "exclude_domains": exclude_domains,
        "select_paths": select_paths,
        "select_domains": select_domains,
        "exclude_paths": exclude_paths,
        "map_exclude_domains": map_exclude_domains,
        "max_depth": max_depth,
        "max_breadth": max_breadth,
        "limit": limit,
        "allow_external": allow_external,
        "timeout": timeout,
    }
    if dry_run:
        payload = {
            "schema_version": 1,
            "generated_at": utc_now_iso(),
            "workflow": "tavily-map-pack",
            "provider": name,
            "url": url,
            "objective": objective,
            "query": query,
            "instructions": instructions,
            "output_dir": str(output_dir.resolve()),
            "request_options": request_options,
            "dry_run": True,
        }
        if paid_action_blocked(budget_limit, estimated_cost_usd=0.0):
            payload.update(
                budget_block_payload(
                    f"{name}:map-pack",
                    budget_limit_usd=budget_limit,
                    estimated_cost_usd=0.0,
                    provider=name,
                )
            )
        return payload

    enforce_paid_budget(
        f"{name}:map-pack",
        budget_limit_usd=budget_limit,
        estimated_cost_usd=0.0,
        provider=name,
    )

    adapter = _provider_adapter(name)
    report = adapter.map_pack(
        url=url,
        output_dir=output_dir.resolve(),
        objective=objective,
        query=query,
        instructions=instructions,
        include_domains=effective_include_domains,
        exclude_domains=exclude_domains,
        select_paths=select_paths,
        select_domains=select_domains,
        exclude_paths=exclude_paths,
        map_exclude_domains=map_exclude_domains,
        max_depth=max_depth,
        max_breadth=max_breadth,
        limit=limit,
        allow_external=allow_external,
        timeout=timeout,
    )
    report["dry_run"] = False
    return report


def run_provider_context_packs(
    *,
    objective: str,
    queries: list[str],
    providers: list[str],
    output_dir: Path,
    include_domains: list[str],
    mode: str,
    max_search_results: int,
    extract_limit: int,
    max_estimated_cost: float,
    dry_run: bool,
    budget_limit: float | None = None,
) -> dict[str, Any]:
    if max_search_results < 1:
        raise ProviderCliError("max_search_results must be >= 1.")
    if extract_limit < 1:
        raise ProviderCliError("extract_limit must be >= 1.")
    if max_estimated_cost < 0:
        raise ProviderCliError("max_estimated_cost cannot be negative.")

    requested_providers = _normalize_live_providers(
        parallel=False,
        tavily=False,
        exa=False,
        live_providers=providers,
    )
    provider_status = _live_provider_statuses(requested_providers)
    selected_providers = [provider for provider in requested_providers if provider_status[provider]["ready"]]
    skipped_providers = [
        {
            "provider": provider,
            "reason": provider_status[provider]["reason"],
            "api_key_env_var": provider_status[provider]["api_key_env_var"],
        }
        for provider in requested_providers
        if provider not in selected_providers
    ]
    if not dry_run and not selected_providers:
        skipped_summary = ", ".join(f"{item['provider']}={item['reason']}" for item in skipped_providers)
        raise ProviderCliError(
            "No requested providers are ready for a live context-pack run"
            + (f" ({skipped_summary})." if skipped_summary else ".")
            + " Run `docpull providers auth --json` or initialize a key with "
            "`docpull providers init <provider>`."
        )

    planned_cases = _provider_case_plans(
        providers=selected_providers,
        output_dir=output_dir,
        max_search_results=max_search_results,
        extract_limit=extract_limit,
    )
    effective_max_estimated_cost = effective_budget_limit(max_estimated_cost, budget_limit)
    if effective_max_estimated_cost is None:
        effective_max_estimated_cost = max_estimated_cost
    blocked_actions = []
    for plan in planned_cases:
        provider = str(plan["provider"])
        estimate = float(plan.get("estimated_cost_usd") or 0.0)
        if paid_action_blocked(budget_limit, estimated_cost_usd=estimate):
            item = blocked_action(
                f"{provider}:context-pack",
                budget_limit_usd=budget_limit,
                estimated_cost_usd=estimate,
                provider=provider,
            )
            plan["blocked_by_budget"] = True
            plan["blocked_action"] = item.to_dict()
            blocked_actions.append(item)

    artifacts: dict[str, Any] = {"output_dir": str(output_dir.resolve())}
    report: dict[str, Any] = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": "provider-context-packs",
        "objective": objective,
        "queries": queries,
        "dry_run": dry_run,
        "providers": selected_providers,
        "requested_providers": requested_providers,
        "skipped_providers": skipped_providers,
        "provider_status": provider_status,
        "planned_cases": planned_cases,
        "blocked_by_budget": bool(blocked_actions),
        "budget_limit_usd": budget_limit,
        "blocked_actions": [item.to_dict() for item in blocked_actions],
        "cases": [],
        "artifacts": artifacts,
    }
    if dry_run:
        return report
    if blocked_actions:
        blocked_summary = ", ".join(item.action for item in blocked_actions)
        raise ProviderCliError(f"Paid-capable provider workflow blocked by budget: {blocked_summary}")
    _enforce_provider_cost_guard(planned_cases, max_estimated_cost=effective_max_estimated_cost)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_policy = _build_source_policy(include_domains=include_domains)
    cases: list[dict[str, Any]] = []
    for provider in selected_providers:
        case_output_dir = output_dir / provider
        if provider == "parallel":
            case = _run_parallel_context_case(
                objective=objective,
                queries=queries,
                output_dir=case_output_dir,
                include_domains=include_domains,
                source_policy=source_policy,
                mode=mode,
                max_search_results=max_search_results,
                extract_limit=extract_limit,
                estimated_cost=_parallel_context_estimate(
                    max_search_results=max_search_results,
                    extract_limit=extract_limit,
                ),
            )
        elif provider == "tavily":
            case = _run_tavily_case(
                objective=objective,
                queries=queries,
                output_dir=case_output_dir,
                include_domains=include_domains,
                mode=mode,
                max_search_results=max_search_results,
                extract_limit=extract_limit,
            )
        else:
            case = _run_exa_case(
                objective=objective,
                queries=queries,
                output_dir=case_output_dir,
                include_domains=include_domains,
                mode=mode,
                max_search_results=max_search_results,
                extract_limit=extract_limit,
            )
        cases.append(case)

    report["cases"] = cases
    report_path = output_dir / "provider-packs.report.json"
    artifacts["json"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def run_provider_extract_pack(
    *,
    provider: str,
    urls: list[str],
    url_file: Path | None,
    objective: str,
    queries: list[str],
    output_dir: Path,
    mode: str,
    dry_run: bool,
    budget_limit: float | None = None,
) -> dict[str, Any]:
    name = _provider_name(provider)
    if name == "parallel":
        raise ProviderCliError("Use `docpull parallel extract-pack` for Parallel Extract.")
    selected_urls = _load_provider_urls(urls, url_file)
    if dry_run:
        payload = {
            "schema_version": BENCHMARK_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "workflow": "provider-extract-pack",
            "provider": name,
            "objective": objective,
            "queries": queries,
            "urls": selected_urls,
            "mode": mode,
            "output_dir": str(output_dir.resolve()),
            "dry_run": True,
        }
        if paid_action_blocked(budget_limit, estimated_cost_usd=0.0):
            payload.update(
                budget_block_payload(
                    f"{name}:extract-pack",
                    budget_limit_usd=budget_limit,
                    estimated_cost_usd=0.0,
                    provider=name,
                )
            )
        return payload

    enforce_paid_budget(
        f"{name}:extract-pack",
        budget_limit_usd=budget_limit,
        estimated_cost_usd=0.0,
        provider=name,
    )

    t0 = time.perf_counter()
    adapter = _provider_adapter(name)
    result = adapter.extract_pack(
        urls=selected_urls,
        objective=objective,
        queries=queries,
        output_dir=output_dir.resolve(),
        mode=mode,
    )
    case = _provider_case_payload(
        result,
        name=f"{name}-extract",
        workflow=f"{name}-extract-pack",
        wall_seconds=time.perf_counter() - t0,
        include_domains=[],
        objective=objective,
        queries=queries,
    )
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "workflow": "provider-extract-pack",
        "provider": name,
        "objective": objective,
        "queries": queries,
        "urls": selected_urls,
        "mode": mode,
        "output_dir": str(result.output_dir),
        "dry_run": False,
        "record_count": len(result.documents),
        "case": case,
        "artifacts": {
            "pack": str(result.pack_path),
            "output_dir": str(result.output_dir),
        },
    }


def _run_tavily_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int,
    mode: str = DEFAULT_MODE,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    result = _provider_adapter("tavily").search_extract_pack(
        objective=objective,
        queries=queries,
        output_dir=output_dir,
        include_domains=include_domains,
        max_search_results=max_search_results,
        extract_limit=extract_limit,
        mode=mode,
    )
    return _provider_case_payload(
        result,
        name="tavily-search-extract",
        workflow="tavily-search-extract-pack",
        wall_seconds=time.perf_counter() - t0,
        include_domains=include_domains,
        objective=objective,
        queries=queries,
    )


def _run_exa_case(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int,
    mode: str = DEFAULT_MODE,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    result = _provider_adapter("exa").search_extract_pack(
        objective=objective,
        queries=queries,
        output_dir=output_dir,
        include_domains=include_domains,
        max_search_results=max_search_results,
        extract_limit=extract_limit,
        mode=mode,
    )
    return _provider_case_payload(
        result,
        name="exa-search-contents",
        workflow="exa-search-contents-pack",
        wall_seconds=time.perf_counter() - t0,
        include_domains=include_domains,
        objective=objective,
        queries=queries,
    )


def _load_provider_urls(urls: list[str], url_file: Path | None) -> list[str]:
    candidates = list(urls)
    if url_file is not None:
        try:
            text = url_file.read_text(encoding="utf-8")
        except OSError as err:
            raise ProviderCliError(f"Could not read URL file {url_file}: {err}") from err
        stripped = text.strip()
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError as err:
                raise ProviderCliError(f"Invalid URL JSON in {url_file}: {err}") from err
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ProviderCliError("URL JSON file must contain an array of strings.")
            candidates.extend(parsed)
        else:
            candidates.extend(line.strip() for line in text.splitlines())
    selected: list[str] = []
    for raw_url in candidates:
        url = raw_url.strip()
        if not url or url in selected:
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ProviderCliError(f"Provider extract URL must be HTTPS: {url}")
        selected.append(url)
    if not selected:
        raise ProviderCliError("extract-pack requires at least one URL or --url-file.")
    return selected


def _blocked_actions_from_report(report: dict[str, Any]) -> list:
    actions = []
    raw_actions = report.get("blocked_actions")
    if isinstance(raw_actions, list):
        for item in raw_actions:
            if isinstance(item, dict):
                actions.append(
                    blocked_action(
                        str(item.get("action") or "provider"),
                        budget_limit_usd=report.get("budget_limit_usd"),
                        estimated_cost_usd=item.get("estimated_cost_usd")
                        if isinstance(item.get("estimated_cost_usd"), int | float)
                        else None,
                        provider=str(item.get("provider")) if item.get("provider") else None,
                    )
                )
    single = report.get("blocked_action")
    if isinstance(single, dict):
        actions.append(
            blocked_action(
                str(single.get("action") or "provider"),
                budget_limit_usd=report.get("budget_limit_usd"),
                estimated_cost_usd=single.get("estimated_cost_usd")
                if isinstance(single.get("estimated_cost_usd"), int | float)
                else None,
                provider=str(single.get("provider")) if single.get("provider") else None,
            )
        )
    return actions


def _estimated_provider_report_cost(report: dict[str, Any]) -> float:
    direct = report.get("estimated_cost_usd")
    if isinstance(direct, int | float):
        return float(direct)
    total = 0.0
    for plan in report.get("planned_cases", []) if isinstance(report.get("planned_cases"), list) else []:
        if isinstance(plan, dict) and isinstance(plan.get("estimated_cost_usd"), int | float):
            total += float(plan["estimated_cost_usd"])
    return total


def _write_provider_report_accounting(
    output_dir: Path,
    *,
    budget_limit: float | None,
    estimated_cost: float,
    blocked_actions: list,
    route_steps: list,
    command: str,
    dry_run: bool,
) -> None:
    if dry_run and budget_limit is None and not blocked_actions:
        return
    maybe_write_run_accounting(
        output_dir,
        budget_limit_usd=budget_limit,
        paid_capable=True,
        accounting=RunAccounting(
            budget_limit_usd=budget_limit,
            estimated_paid_cost_usd=estimated_cost,
            paid_request_count=0 if dry_run or blocked_actions else 1,
            blocked_actions=blocked_actions,
            route_steps=route_steps,
            command=command,
        ),
    )


def _provider_auth_payload(
    statuses: dict[str, dict[str, Any]],
    *,
    redact_paths: bool = False,
) -> dict[str, Any]:
    payload_statuses = _redact_provider_status_paths(statuses) if redact_paths else statuses
    ready = [name for name, status in statuses.items() if status["ready"]]
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "ready_count": len(ready),
        "provider_count": len(statuses),
        "ready_providers": ready,
        "providers": payload_statuses,
        "key_lookup_order": ["environment", "project .env.local", "user secrets.env"],
        "user_secrets_path": "[redacted]" if redact_paths else str(user_secrets_path()),
        "project_env_path": "[redacted]" if redact_paths else str(Path.cwd() / PROJECT_ENV_FILENAME),
        "paths_redacted": redact_paths,
        "validation": (
            "local key and optional SDK presence only; run `docpull providers probe` "
            "for explicit live key validation"
        ),
        "key_handling": "docpull never prints API keys or writes them to pack artifacts",
        "next_actions": _provider_auth_next_actions(statuses),
    }


def _provider_error_payload(err: Exception, *, command: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "ok": False,
        "command": command,
        "error": {
            "type": type(err).__name__,
            "message": str(err),
        },
    }


def _redact_provider_status_paths(
    statuses: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    redacted: dict[str, dict[str, Any]] = {}
    for provider, status in statuses.items():
        item = dict(status)
        if item.get("api_key_source_path"):
            item["api_key_source_path"] = "[redacted]"
        redacted[provider] = item
    return redacted


def _print_provider_auth_status(console: Console, payload: dict[str, Any]) -> None:
    console.print("[bold]Provider local auth preflight[/bold]")
    for name in PROVIDER_NAMES:
        status = payload["providers"].get(name)
        if not status:
            continue
        state = "ready" if status["ready"] else status["reason"]
        console.print(
            f"{status['label']}: {state} "
            f"({status['api_key_env_var']}: "
            f"{'detected' if status['api_key_present'] else 'missing'}, "
            f"source: {status['api_key_source']})"
        )
    console.print("Secret handling: keys are never printed or written to pack artifacts.")
    console.print(
        "Validation: local key and optional SDK presence only; no live key validation call is made."
    )
    console.print("Use `docpull providers probe` for explicit live API-key validation.")


def _print_provider_probe_status(console: Console, payload: dict[str, Any]) -> None:
    console.print("[bold]Provider live probe[/bold]")
    console.print(f"Mode: {payload['mode']}")
    for name in PROVIDER_NAMES:
        status = payload["providers"].get(name)
        if not status:
            continue
        if status["live_checked"]:
            state = "verified" if status["live_valid"] else status["quota_state"]
            detail = f"HTTP {status['http_status']}"
        elif status["configured"]:
            state = status["quota_state"]
            detail = "no live request"
        else:
            state = status["quota_state"]
            detail = "not configured"
        console.print(f"{status['label']}: {state} ({detail}, probe: {status['probe_kind']})")
    console.print("Secret handling: keys and auth headers are never printed.")
    console.print(
        "Smoke mode may spend provider credits; safe mode avoids search calls where providers support it."
    )


def _provider_probe_payload_verified(payload: dict[str, Any]) -> bool:
    providers = payload.get("providers", {})
    return bool(providers) and all(
        result.get("live_valid") is True and result.get("workflow_ready") is True
        for result in providers.values()
    )


def _provider_auth_next_actions(statuses: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = [
        {
            "command": "docpull providers capabilities --json",
            "reason": "List shared and provider-specific workflows.",
        }
    ]
    for name, status in statuses.items():
        if status["ready"]:
            actions.append(
                {
                    "command": f"docpull providers probe --provider {name} --json --redact-paths",
                    "reason": (
                        f"Explicitly live-check {status['label']} only when a network probe is intended."
                    ),
                }
            )
            actions.append(
                {
                    "command": f'docpull {name} context-pack "Find official docs" --dry-run --json',
                    "reason": f"Plan a {status['label']} context-pack run without spending credits.",
                }
            )
            continue
        if status["reason"] == "invalid_api_key":
            actions.append(
                {
                    "command": f"docpull providers init {name} --force",
                    "reason": f"Replace the invalid {status['api_key_env_var']} value.",
                }
            )
        elif status["reason"] == "missing_optional_sdk":
            actions.append(
                {
                    "command": "pip install parallel-web",
                    "reason": "Install the optional Parallel SDK.",
                }
            )
        else:
            actions.append(
                {
                    "command": f"docpull providers init {name}",
                    "reason": f"Store {status['label']} API key for local live workflows.",
                }
            )
    return actions


def _print_provider_capabilities(console: Console, payload: dict[str, Any]) -> None:
    console.print("[bold]Provider capability matrix[/bold]")
    for provider in payload["providers"]:
        config = PROVIDER_CONFIGS[provider]
        console.print(f"{config.label}:")
        for capability in payload["capabilities"].get(provider, []):
            status = capability["status"]
            surface = capability["surface"]
            console.print(f"  - {capability['id']}: {status} ({surface})")
    console.print(payload["guidance"])


def _provider_case_plans(
    *,
    providers: list[ProviderName],
    output_dir: Path,
    max_search_results: int,
    extract_limit: int,
) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for provider in providers:
        plan: dict[str, Any] = {
            "provider": provider,
            "output_dir": str((output_dir / provider).resolve()),
            "estimated_cost_usd": None,
        }
        if provider == "parallel":
            plan["estimated_cost_usd"] = _parallel_context_estimate(
                max_search_results=max_search_results,
                extract_limit=extract_limit,
            )
        plans.append(plan)
    return plans


def _enforce_provider_cost_guard(plans: list[dict[str, Any]], *, max_estimated_cost: float) -> None:
    estimated_total = sum(float(plan.get("estimated_cost_usd") or 0.0) for plan in plans)
    if estimated_total > max_estimated_cost:
        raise ProviderCliError(
            f"Estimated live provider cost ${estimated_total:.6f} exceeds guard ${max_estimated_cost:.6f}."
        )


def _parallel_context_estimate(*, max_search_results: int, extract_limit: int) -> float:
    return estimate_context_pack_cost(
        extract_limit=extract_limit,
        max_search_results=max_search_results,
    )


def _provider_name(value: str) -> ProviderName:
    provider = value.strip().lower()
    if provider not in PROVIDER_CONFIGS:
        raise ProviderCliError(f"Unsupported provider: {value}")
    return provider  # type: ignore[return-value]


def _validate_https_url(url: str, *, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ProviderCliError(f"{field} must be an absolute HTTPS URL: {url}")


def _url_hostname(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ProviderCliError(f"Could not infer URL host: {url}")
    return host


def _validate_path_patterns(patterns: list[str], *, field: str) -> None:
    for pattern in patterns:
        if not pattern.startswith("/"):
            raise ProviderCliError(f"{field} must start with /: {pattern}")


def _ensure_gitignore_entry(project_dir: Path, entry: str) -> tuple[Path, bool]:
    gitignore = project_dir / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    normalized = {line.strip() for line in existing if line.strip() and not line.strip().startswith("#")}
    variants = {entry, f"/{entry}"}
    if normalized & variants:
        return gitignore, False
    lines = list(existing)
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(entry)
    gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return gitignore, True
