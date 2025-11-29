"""Command-line interface for docpull."""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

# Check if --doctor flag is present before checking dependencies
if "--doctor" in sys.argv:
    from .doctor import run_doctor

    output_dir = None
    if "--output-dir" in sys.argv or "-o" in sys.argv:
        try:
            flag_idx = sys.argv.index("--output-dir") if "--output-dir" in sys.argv else sys.argv.index("-o")
            if flag_idx + 1 < len(sys.argv):
                output_dir = Path(sys.argv[flag_idx + 1])
        except (ValueError, IndexError):
            pass
    sys.exit(run_doctor(output_dir=output_dir))

# Verify core dependencies
try:
    import aiohttp  # noqa: F401
    import bs4  # noqa: F401
    import defusedxml  # noqa: F401
    import html2text  # noqa: F401
    import rich  # noqa: F401
except ImportError as e:
    print(f"\nERROR: Missing required dependency: {e.name}", file=sys.stderr)
    print("\nDocpull requires all core dependencies to be installed.", file=sys.stderr)
    print("\nRecommended fixes:", file=sys.stderr)
    print("  1. For pipx users: pipx reinstall docpull --force", file=sys.stderr)
    print("  2. For pip users: pip install --upgrade --force-reinstall docpull", file=sys.stderr)
    print("  3. For development: pip install -e .[dev]", file=sys.stderr)
    print("\nTo diagnose issues, run: docpull --doctor", file=sys.stderr)
    sys.exit(1)

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from . import __version__
from .core.fetcher import Fetcher
from .models.config import DocpullConfig, ProfileName
from .models.events import EventType


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for CLI."""
    parser = argparse.ArgumentParser(
        prog="docpull",
        description="Fetch and convert documentation from any URL to markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch with default settings (RAG profile)
  docpull https://docs.example.com

  # Use a specific profile
  docpull https://docs.example.com --profile mirror

  # Control crawl behavior
  docpull https://example.com --max-pages 100 --max-depth 3

  # Filter paths
  docpull https://example.com --include-paths "/api/*" --exclude-paths "/changelog/*"

  # Enable JavaScript rendering
  docpull https://spa-site.com --js
        """,
    )

    parser.add_argument(
        "url",
        nargs="?",
        help="URL to fetch documentation from",
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

    # Profile
    parser.add_argument(
        "--profile",
        "-p",
        choices=["rag", "mirror", "quick"],
        default="rag",
        help="Preset profile (default: rag)",
    )

    # Output
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Output directory (default: ./docs)",
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
        "--js",
        "--javascript",
        action="store_true",
        dest="javascript",
        help="Enable JavaScript rendering (requires Playwright)",
    )

    # Content filtering
    filter_group = parser.add_argument_group("content filtering")
    filter_group.add_argument(
        "--streaming-dedup",
        action="store_true",
        help="Enable real-time deduplication",
    )
    filter_group.add_argument(
        "--language",
        type=str,
        metavar="CODE",
        help="Include only pages in this language",
    )

    # Network settings
    network_group = parser.add_argument_group("network settings")
    network_group.add_argument(
        "--proxy",
        type=str,
        metavar="URL",
        help="Proxy URL",
    )
    network_group.add_argument(
        "--user-agent",
        type=str,
        help="Custom User-Agent string",
    )
    network_group.add_argument(
        "--max-retries",
        type=int,
        default=None,
        help="Maximum retry attempts",
    )

    # Cache settings
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

    # Output control
    output_group = parser.add_argument_group("output control")
    output_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without downloading",
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

    return parser


def run_fetcher(args: argparse.Namespace) -> int:
    """Run the fetcher with given arguments."""
    console = Console()

    if not args.url:
        console.print("[red]Error:[/red] Please provide a URL to fetch")
        return 1

    # Map profile
    profile_map = {
        "rag": ProfileName.RAG,
        "mirror": ProfileName.MIRROR,
        "quick": ProfileName.QUICK,
    }
    profile = profile_map.get(args.profile, ProfileName.RAG)

    # Build config
    config_kwargs: dict = {
        "profile": profile,
        "url": args.url,
        "dry_run": args.dry_run,
    }

    # Output settings
    if args.output_dir:
        config_kwargs["output"] = {"directory": args.output_dir}

    # Crawl settings
    crawl_kwargs: dict = {}
    if args.max_pages is not None:
        crawl_kwargs["max_pages"] = args.max_pages
    if args.max_depth is not None:
        crawl_kwargs["max_depth"] = args.max_depth
    if args.max_concurrent is not None:
        crawl_kwargs["max_concurrent"] = args.max_concurrent
    if args.rate_limit is not None:
        crawl_kwargs["rate_limit"] = args.rate_limit
    if args.javascript:
        crawl_kwargs["javascript"] = True
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
    if args.language:
        filter_kwargs["language"] = args.language
    if filter_kwargs:
        config_kwargs["content_filter"] = filter_kwargs

    # Network settings
    network_kwargs: dict = {}
    if args.proxy:
        network_kwargs["proxy"] = args.proxy
    if args.user_agent:
        network_kwargs["user_agent"] = args.user_agent
    if args.max_retries is not None:
        network_kwargs["max_retries"] = args.max_retries
    if network_kwargs:
        config_kwargs["network"] = network_kwargs

    # Cache settings
    cache_kwargs: dict = {}
    if args.cache:
        cache_kwargs["enabled"] = True
    if args.cache_dir:
        cache_kwargs["directory"] = args.cache_dir
    if args.cache_ttl is not None:
        cache_kwargs["ttl_days"] = args.cache_ttl
    if args.no_skip_unchanged:
        cache_kwargs["skip_unchanged"] = False
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
        console.print(f"[red]Configuration error:[/red] {e}")
        return 1

    async def run() -> int:
        if not args.quiet:
            console.print(f"[bold blue]docpull[/bold blue] v{__version__}")
            console.print(f"Profile: {profile.value}")
            console.print(f"Target: {config.url}")
            console.print()

        try:
            async with Fetcher(config) as fetcher:
                if args.quiet:
                    async for _ in fetcher.run():
                        pass
                else:
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[progress.description]{task.description}"),
                        console=console,
                        transient=True,
                    ) as progress:
                        task = progress.add_task("Starting...", total=None)

                        async for event in fetcher.run():
                            if event.type == EventType.STARTED:
                                progress.update(task, description=f"[cyan]{event.message}")
                            elif event.type == EventType.DISCOVERY_STARTED:
                                progress.update(task, description="[cyan]Discovering URLs...")
                            elif event.type == EventType.DISCOVERY_COMPLETE:
                                progress.update(task, description=f"[green]Found {event.total} URLs")
                            elif event.type == EventType.FETCH_PROGRESS:
                                progress.update(
                                    task,
                                    description=f"[cyan]Fetching {event.current}/{event.total}: {event.url}",
                                )
                            elif event.type == EventType.FETCH_FAILED:
                                console.print(f"[red]Failed:[/red] {event.url} - {event.error}")
                            elif event.type == EventType.COMPLETED:
                                progress.update(task, description=f"[green]{event.message}")

                # Print stats
                stats = fetcher.stats
                if not args.quiet:
                    console.print()
                    console.print("[bold]Results:[/bold]")
                    console.print(f"  URLs discovered: {stats.urls_discovered}")
                    console.print(f"  Pages fetched: {stats.pages_fetched}")
                    console.print(f"  Pages skipped: {stats.pages_skipped}")
                    console.print(f"  Pages failed: {stats.pages_failed}")
                    console.print(f"  Duration: {stats.duration_seconds:.1f}s")

                return 0 if stats.pages_failed == 0 else 1

        except Exception as e:
            console.print(f"[red]Error:[/red] {e}")
            if args.verbose:
                import traceback

                traceback.print_exc()
            return 1

    return asyncio.run(run())


def main(argv: Optional[list[str]] = None) -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if args.doctor:
        from .doctor import run_doctor

        return run_doctor(output_dir=args.output_dir)

    return run_fetcher(args)


if __name__ == "__main__":
    sys.exit(main())
