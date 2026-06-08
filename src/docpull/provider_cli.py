"""Provider-neutral CLI for optional live context-pack providers."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from .benchmark import (
    BENCHMARK_SCHEMA_VERSION,
    DEFAULT_INCLUDE_DOMAIN,
    DEFAULT_MAX_ESTIMATED_COST_USD,
    DEFAULT_MODE,
    DEFAULT_OBJECTIVE,
    DEFAULT_QUERY,
    BenchmarkError,
    _live_provider_statuses,
    _normalize_live_providers,
    _run_exa_case,
    _run_parallel_context_case,
    _run_tavily_case,
)
from .parallel_workflows import _build_source_policy, estimate_context_pack_cost
from .provider_keys import (
    PROJECT_ENV_FILENAME,
    PROVIDER_CONFIGS,
    PROVIDER_NAMES,
    ProviderName,
    clean_api_key,
    user_secrets_path,
    write_provider_secret,
)
from .time_utils import utc_now_iso

DEFAULT_PROVIDER_OUTPUT_DIR = Path("packs/provider-context-packs")


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

    return parser


def run_provider_cli(argv: list[str] | None = None) -> int:
    parser = create_provider_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.command == "auth":
            statuses = provider_auth_statuses(args.provider or list(PROVIDER_NAMES))
            payload = _provider_auth_payload(statuses)
            if args.json_output:
                console.print_json(data=payload)
            else:
                _print_provider_auth_status(console, payload)
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
                f"[green]Stored {result['label']} API key:[/green] "
                f"{result['key_source']} -> {result['path']}"
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
        parser.error(f"Unknown command: {args.command}")
    except (BenchmarkError, ProviderCliError) as err:
        console.print("[red]Provider error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Provider command failed:[/red] " + escape(str(err)))
        return 1
    return 1


def provider_auth_statuses(providers: list[str]) -> dict[str, dict[str, Any]]:
    normalized = _normalize_live_providers(
        parallel=False,
        tavily=False,
        exa=False,
        live_providers=providers,
    )
    return _live_provider_statuses(normalized)


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
    api_key = clean_api_key(value)
    if not api_key:
        raise ProviderCliError(f"{config.label} API key cannot be empty.")
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
    selected_providers = [
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
        if provider not in selected_providers
    ]

    planned_cases = _provider_case_plans(
        providers=selected_providers,
        output_dir=output_dir,
        max_search_results=max_search_results,
        extract_limit=extract_limit,
    )
    _enforce_provider_cost_guard(planned_cases, max_estimated_cost=max_estimated_cost)

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
        "cases": [],
        "artifacts": artifacts,
    }
    if dry_run:
        return report

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
                max_search_results=max_search_results,
                extract_limit=extract_limit,
            )
        else:
            case = _run_exa_case(
                objective=objective,
                queries=queries,
                output_dir=case_output_dir,
                include_domains=include_domains,
                max_search_results=max_search_results,
            )
        cases.append(case)

    report["cases"] = cases
    report_path = output_dir / "provider-packs.report.json"
    artifacts["json"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def _provider_auth_payload(statuses: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ready = [name for name, status in statuses.items() if status["ready"]]
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "ready_count": len(ready),
        "provider_count": len(statuses),
        "ready_providers": ready,
        "providers": statuses,
        "key_lookup_order": ["environment", "project .env.local", "user secrets.env"],
        "user_secrets_path": str(user_secrets_path()),
        "project_env_path": str(Path.cwd() / PROJECT_ENV_FILENAME),
        "validation": "local key and optional SDK presence only; no live key validation call is made",
        "key_handling": "docpull never prints API keys or writes them to pack artifacts",
    }


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
        "Validation: local key and optional SDK presence only; "
        "no live key validation call is made."
    )


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
