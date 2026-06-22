"""CLI commands for provider-neutral discovery packs."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from .core.fetcher import Fetcher
from .discovery.contracts import (
    SITE_SCAN_SOURCES,
    DiscoveryError,
    normalize_provider_response,
    read_candidate_records,
    records_from_site_scan,
    records_from_sitemap_file,
    records_from_url_file,
    select_candidate_records,
    write_discovery_pack,
    write_selected_sources,
)
from .http.client import AsyncHttpClient
from .http.rate_limiter import PerHostRateLimiter
from .models.config import CrawlConfig, DocpullConfig, OutputConfig, ProfileName
from .policy import PolicyConfig, PolicyError
from .security.url_validator import UrlValidator

DEFAULT_DISCOVERY_OUTPUT_DIR = Path("packs/discovery")
DEFAULT_SELECTED_OUTPUT_DIR = Path("packs/discovery-selected")


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be an integer") from err
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be a number") from err
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def create_discovery_parser() -> argparse.ArgumentParser:
    """Create the ``docpull discover`` parser."""
    parser = argparse.ArgumentParser(
        prog="docpull discover",
        description="Build and select local provider-neutral discovery packs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_cmd = subparsers.add_parser(
        "import",
        help="Normalize a local provider response JSON file into a discovery pack",
    )
    import_cmd.add_argument("response", type=Path)
    import_cmd.add_argument(
        "--provider",
        required=True,
        choices=["parallel", "tavily", "exa", "brave", "local"],
    )
    _add_pack_options(import_cmd)

    urls_cmd = subparsers.add_parser(
        "urls",
        help="Normalize a local URL file into a discovery pack",
    )
    urls_cmd.add_argument("url_file", type=Path)
    urls_cmd.add_argument("--source-name", default="local-url-file")
    _add_pack_options(urls_cmd)

    sitemap_cmd = subparsers.add_parser(
        "sitemap",
        help="Normalize a local sitemap XML file into a discovery pack",
    )
    sitemap_cmd.add_argument("sitemap_file", type=Path)
    sitemap_cmd.add_argument("--base-url", help="Optional crawl-origin URL used to keep sitemap URLs on host")
    _add_pack_options(sitemap_cmd)

    scan_cmd = subparsers.add_parser(
        "scan",
        help="Scan free local/open site hints into a discovery pack",
    )
    scan_cmd.add_argument("url", help="Site or GitHub repository URL to scan")
    scan_cmd.add_argument(
        "--source",
        action="append",
        dest="sources",
        choices=[*SITE_SCAN_SOURCES, "all"],
        help="Discovery source to enable; repeat for multiple sources (default: all)",
    )
    scan_cmd.add_argument(
        "--max-per-source",
        type=_positive_int,
        default=50,
        help="Maximum candidate records retained from each scan source",
    )
    scan_cmd.add_argument(
        "--timeout-seconds",
        type=_positive_float,
        default=20.0,
        help="Per-request timeout for local/open discovery fetches",
    )
    _add_pack_options(scan_cmd)

    select_cmd = subparsers.add_parser(
        "select",
        help="Apply selection policies and write selected_sources.ndjson without fetching",
    )
    select_cmd.add_argument("discovery_pack", type=Path)
    _add_selection_options(select_cmd)
    select_cmd.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_SELECTED_OUTPUT_DIR)
    select_cmd.add_argument("--policy", type=Path)
    select_cmd.add_argument("--json", action="store_true", dest="json_output")

    fetch_cmd = subparsers.add_parser(
        "fetch",
        help="Fetch selected discovery candidates into a normal DocPull output directory",
    )
    fetch_cmd.add_argument("discovery_pack", type=Path)
    _add_selection_options(fetch_cmd)
    fetch_cmd.add_argument("--output-dir", "-o", type=Path, default=Path("packs/discovery-fetch"))
    fetch_cmd.add_argument("--policy", type=Path)
    fetch_cmd.add_argument("--profile", choices=["rag", "mirror", "quick", "llm", "okf"], default="rag")
    fetch_cmd.add_argument(
        "--format",
        choices=["markdown", "json", "ndjson", "sqlite", "okf"],
        default="markdown",
    )
    fetch_cmd.add_argument("--naming-strategy", choices=["full", "hierarchical"], default="full")
    fetch_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Write selected source files but do not fetch",
    )
    fetch_cmd.add_argument("--json", action="store_true", dest="json_output")
    fetch_cmd.add_argument("--quiet", "-q", action="store_true")

    return parser


def _add_pack_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_DISCOVERY_OUTPUT_DIR)
    parser.add_argument("--query")
    parser.add_argument("--objective")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    parser.add_argument("--exclude-domain", action="append", dest="exclude_domains", default=[])
    parser.add_argument("--max-results", type=_positive_int)
    parser.add_argument("--json", action="store_true", dest="json_output")


def _add_selection_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--select",
        action="append",
        dest="selectors",
        default=[],
        help="Selection policy: top:N, domain:N, domain:example.com:N, score>=X, or manual-file",
    )
    parser.add_argument("--manual-file", type=Path, help="Newline/JSON URL file for manual selection")


def run_discovery_cli(argv: list[str] | None = None) -> int:
    """Run ``docpull discover``."""
    parser = create_discovery_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.command == "import":
            policy = _load_effective_policy(args)
            records = normalize_provider_response(
                args.response,
                provider=args.provider,
                query=args.query,
                expected_domains=policy.allowed_domains,
            )
            report = write_discovery_pack(
                args.output_dir,
                records,
                policy=policy,
                objective=args.objective,
                query=args.query,
                source=f"provider-import:{args.provider}",
                source_path=args.response,
                max_results=args.max_results,
            )
            _print_pack_report(console, report, args.json_output)
            return 0

        if args.command == "urls":
            policy = _load_effective_policy(args)
            records = records_from_url_file(
                args.url_file,
                query=args.query,
                expected_domains=policy.allowed_domains,
                source=args.source_name,
            )
            report = write_discovery_pack(
                args.output_dir,
                records,
                policy=policy,
                objective=args.objective,
                query=args.query,
                source=args.source_name,
                source_path=args.url_file,
                max_results=args.max_results,
            )
            _print_pack_report(console, report, args.json_output)
            return 0

        if args.command == "sitemap":
            policy = _load_effective_policy(args)
            records = records_from_sitemap_file(
                args.sitemap_file,
                base_url=args.base_url,
                query=args.query,
                expected_domains=policy.allowed_domains,
            )
            report = write_discovery_pack(
                args.output_dir,
                records,
                policy=policy,
                objective=args.objective,
                query=args.query,
                source="local-sitemap",
                source_path=args.sitemap_file,
                max_results=args.max_results,
            )
            _print_pack_report(console, report, args.json_output)
            return 0

        if args.command == "scan":
            return asyncio.run(_run_scan_command(args, console))

        if args.command == "select":
            records = read_candidate_records(args.discovery_pack)
            policy = _selection_policy(args)
            selected = select_candidate_records(
                records,
                args.selectors or ["top:10"],
                manual_file=args.manual_file,
            )
            report = write_selected_sources(
                args.output_dir,
                selected,
                source_pack=args.discovery_pack,
                policy=policy,
            )
            _print_selection_report(console, report, args.json_output)
            return 0

        if args.command == "fetch":
            return asyncio.run(_run_fetch_command(args, console))

        parser.error(f"Unknown discover command: {args.command}")
    except (DiscoveryError, PolicyError, ValueError) as err:
        console.print("[red]Discovery error:[/red] " + escape(str(err)))
        return 1
    return 1


def _load_effective_policy(args: argparse.Namespace) -> PolicyConfig:
    policy = PolicyConfig.from_file(args.policy) if args.policy else PolicyConfig()
    data = policy.model_dump(mode="json")
    if args.include_domains:
        data["allowed_domains"] = args.include_domains
    if args.exclude_domains:
        data["denied_domains"] = args.exclude_domains
    if args.max_results and data.get("max_pages") is None:
        data["max_pages"] = args.max_results
    try:
        return PolicyConfig.model_validate(data)
    except Exception as err:  # noqa: BLE001
        raise PolicyError(str(err)) from err


def _selection_policy(args: argparse.Namespace) -> PolicyConfig:
    if args.policy:
        return PolicyConfig.from_file(args.policy)
    source_policy = args.discovery_pack / "source_policy.json" if args.discovery_pack.is_dir() else None
    if source_policy and source_policy.exists():
        import json

        payload = json.loads(source_policy.read_text(encoding="utf-8"))
        constraints = payload.get("constraints", {})
        if isinstance(constraints, dict):
            return PolicyConfig.model_validate(constraints)
    return PolicyConfig()


async def _run_scan_command(args: argparse.Namespace, console: Console) -> int:
    try:
        policy = _load_effective_policy(args)
        validator = UrlValidator()
        validation = validator.validate(args.url)
        if not validation.is_valid:
            raise DiscoveryError(f"Scan URL rejected: {validation.rejection_reason}")
        expected_domains = policy.allowed_domains or _default_scan_expected_domains(args.url)
        async with AsyncHttpClient(
            rate_limiter=PerHostRateLimiter(default_delay=0.2, default_concurrent=2),
            url_validator=validator,
            default_timeout=args.timeout_seconds,
            max_retries=1,
        ) as client:
            records = await records_from_site_scan(
                args.url,
                client=client,
                sources=args.sources,
                query=args.query,
                expected_domains=expected_domains,
                max_results_per_source=args.max_per_source,
                timeout_seconds=args.timeout_seconds,
            )
        report = write_discovery_pack(
            args.output_dir,
            records,
            policy=policy,
            objective=args.objective,
            query=args.query,
            source="local-site-scan",
            max_results=args.max_results,
        )
        _print_pack_report(console, report, args.json_output)
        return 0
    except (DiscoveryError, PolicyError, ValueError) as err:
        console.print("[red]Discovery error:[/red] " + escape(str(err)))
        return 1


def _default_scan_expected_domains(url: str) -> list[str]:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host in {"github.com", "www.github.com"}:
        return ["github.com", "raw.githubusercontent.com"]
    return [host] if host else []


async def _run_fetch_command(args: argparse.Namespace, console: Console) -> int:
    try:
        records = read_candidate_records(args.discovery_pack)
        policy = _selection_policy(args)
        selected = select_candidate_records(
            records,
            args.selectors or ["top:10"],
            manual_file=args.manual_file,
        )
        selection_report = write_selected_sources(
            args.output_dir,
            selected,
            source_pack=args.discovery_pack,
            policy=policy,
        )
        if args.dry_run:
            report = {**selection_report, "dry_run": True, "fetched": 0, "failed": 0}
            _print_selection_report(console, report, args.json_output or True)
            return 0
        if not selected:
            console.print("[yellow]No selected sources to fetch.[/yellow]")
            return 0

        profile_map = {
            "rag": ProfileName.RAG,
            "mirror": ProfileName.MIRROR,
            "quick": ProfileName.QUICK,
            "llm": ProfileName.LLM,
            "okf": ProfileName.OKF,
        }
        config = DocpullConfig(
            profile=profile_map[args.profile],
            url=selected[0].url,
            output=OutputConfig(
                directory=args.output_dir,
                format=args.format,
                naming_strategy=args.naming_strategy,
            ),
            crawl=CrawlConfig(max_pages=len(selected), streaming_discovery=False),
        )
        failed = 0
        async with Fetcher(config) as fetcher:
            for record in selected:
                ctx = await fetcher.fetch_one(record.url)
                if ctx.error or ctx.should_skip:
                    failed += 1
                    if not args.quiet:
                        reason = ctx.error or ctx.skip_reason or "skipped"
                        console.print(f"[yellow]Skipped:[/yellow] {record.url} - {reason}")
                elif not args.quiet:
                    console.print(f"[green]Saved:[/green] {record.url}")
            stats = fetcher.stats
        report = {
            **selection_report,
            "dry_run": False,
            "fetched": stats.pages_fetched,
            "failed": failed,
            "skipped": stats.pages_skipped,
        }
        if args.json_output:
            console.print_json(data=report)
        elif not args.quiet:
            console.print(
                f"[green]Fetched {stats.pages_fetched} selected source(s):[/green] {args.output_dir}"
            )
        return 0 if failed == 0 else 1
    except (DiscoveryError, PolicyError, ValueError) as err:
        console.print("[red]Discovery error:[/red] " + escape(str(err)))
        return 1


def _print_pack_report(console: Console, report: dict[str, object], json_output: bool) -> None:
    if json_output:
        console.print_json(data=report)
        return
    console.print(
        "[green]Discovery pack written:[/green] "
        f"{report['output_dir']} ({report['candidate_count']} candidate(s), "
        f"{report['skipped_count']} skipped)"
    )
    artifacts = report["artifacts"]
    if isinstance(artifacts, dict):
        console.print(f"Candidates: {artifacts['candidate_sources']}")
        console.print(f"Policy: {artifacts['source_policy']}")
        console.print(f"Guide: {artifacts['discovery']}")


def _print_selection_report(console: Console, report: dict[str, object], json_output: bool) -> None:
    if json_output:
        console.print_json(data=report)
        return
    console.print(
        "[green]Selected sources written:[/green] "
        f"{report['selected_count']} source(s) from {report['source_pack']}"
    )
    artifacts = report["artifacts"]
    if isinstance(artifacts, dict):
        console.print(f"URLs: {artifacts['selected_urls']}")
