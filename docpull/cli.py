import argparse
import sys
from pathlib import Path
from typing import Optional

# Check if --doctor flag is present before checking dependencies
# This allows users to diagnose issues even when dependencies are missing
if "--doctor" in sys.argv:
    from .doctor import run_doctor

    # Parse output dir if provided
    output_dir = None
    if "--output-dir" in sys.argv or "-o" in sys.argv:
        try:
            flag_idx = sys.argv.index("--output-dir") if "--output-dir" in sys.argv else sys.argv.index("-o")
            if flag_idx + 1 < len(sys.argv):
                output_dir = Path(sys.argv[flag_idx + 1])
        except (ValueError, IndexError):
            pass
    sys.exit(run_doctor(output_dir=output_dir))

# Verify core dependencies are available
try:
    import aiohttp  # noqa: F401
    import bs4  # noqa: F401
    import defusedxml  # noqa: F401
    import html2text  # noqa: F401
    import requests  # noqa: F401
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

from . import __version__
from .config import FetcherConfig
from .fetchers import (
    BunFetcher,
    D3DevDocsFetcher,
    NextJSFetcher,
    PlaidFetcher,
    ReactFetcher,
    StripeFetcher,
    TailwindFetcher,
    TurborepoFetcher,
)
from .fetchers.generic_async import GenericAsyncFetcher
from .orchestrator import create_orchestrator
from .utils.logging_config import setup_logging


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for CLI."""
    parser = argparse.ArgumentParser(
        prog="docpull",
        description="Fetch and convert documentation from any URL or known sources to markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch from any documentation URL
  docpull https://aptos.dev
  docpull https://docs.anthropic.com

  # Fetch using profile names (shortcuts)
  docpull stripe
  docpull nextjs plaid

  # Mix URLs and profiles
  docpull stripe https://newsite.com/docs

  # Control scraping depth and pages
  docpull https://example.com/docs --max-pages 100 --max-depth 3

  # Legacy syntax still works
  docpull --source stripe --source nextjs

  # Use a config file
  docpull --config config.yaml

  # Generate a sample config file
  docpull --generate-config config.yaml
        """,
    )

    # Positional arguments for URLs or profile names
    parser.add_argument(
        "targets",
        nargs="*",
        help="URLs or profile names to fetch (e.g., 'https://docs.site.com', 'stripe', 'nextjs')",
    )

    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        help="Path to config file (YAML or JSON)",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=None,
        help="Directory to save documentation (default: ./docs)",
    )

    parser.add_argument(
        "--source",
        "-s",
        nargs="+",
        choices=["all", "bun", "d3", "nextjs", "plaid", "react", "stripe", "tailwind", "turborepo"],
        default=None,
        dest="sources",
        help="Documentation source(s) to fetch. Use 'all' for everything. (default: all)",
    )

    parser.add_argument(
        "--rate-limit",
        "-r",
        type=float,
        default=None,
        help="Seconds to wait between requests (default: 0.5)",
    )

    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="Re-fetch files that already exist",
    )

    parser.add_argument(
        "--log-level",
        "-l",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default=None,
        help="Logging level (default: INFO)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_const",
        const="DEBUG",
        dest="log_level_override",
        help="Enable verbose output (equivalent to --log-level DEBUG)",
    )

    parser.add_argument(
        "--quiet",
        "-q",
        action="store_const",
        const="ERROR",
        dest="log_level_override",
        help="Suppress informational output (equivalent to --log-level ERROR)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be fetched without actually downloading",
    )

    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of pages to fetch (default: unlimited)",
    )

    parser.add_argument(
        "--max-depth",
        type=int,
        default=5,
        help="Maximum crawl depth when following links (default: 5)",
    )

    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=10,
        help="Maximum concurrent requests for async fetching (default: 10)",
    )

    parser.add_argument(
        "--js",
        "--javascript",
        action="store_true",
        dest="use_js",
        help="Enable JavaScript rendering with Playwright (slower but handles JS-heavy sites)",
    )

    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bars",
    )

    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to log file (default: console only)",
    )

    parser.add_argument(
        "--generate-config",
        type=Path,
        metavar="PATH",
        help="Generate a sample config file and exit",
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Run diagnostic checks to verify installation",
    )

    # ===== New Features (v1.2.0) =====

    # Multi-Source Configuration
    multisource_group = parser.add_argument_group(
        "multi-source configuration", "Fetch from multiple sources with one command"
    )
    multisource_group.add_argument(
        "--sources-file",
        type=Path,
        metavar="PATH",
        help="YAML config file with multiple sources and their individual settings",
    )

    # Language Filtering
    language_group = parser.add_argument_group("language filtering")
    language_group.add_argument(
        "--language", type=str, metavar="CODE", help='Include only this language (e.g., "en")'
    )
    language_group.add_argument(
        "--exclude-languages", nargs="+", metavar="CODE", help="Exclude these languages"
    )

    # Deduplication
    dedup_group = parser.add_argument_group("deduplication")
    dedup_group.add_argument(
        "--deduplicate", action="store_true", help="Remove duplicate files based on content"
    )
    dedup_group.add_argument(
        "--keep-variant",
        type=str,
        metavar="PATTERN",
        help="When deduplicating, keep files matching this pattern",
    )

    # Size Limits
    size_group = parser.add_argument_group("size limits")
    size_group.add_argument(
        "--max-file-size", type=str, metavar="SIZE", help='Maximum size per file (e.g., "200kb", "1mb")'
    )
    size_group.add_argument("--max-total-size", type=str, metavar="SIZE", help="Maximum total download size")

    # Content Filtering
    content_group = parser.add_argument_group("content filtering")
    content_group.add_argument(
        "--exclude-sections", nargs="+", metavar="NAME", help="Remove sections with these header names"
    )
    content_group.add_argument(
        "--include-paths", nargs="+", metavar="PATTERN", help="Only crawl URLs matching these patterns"
    )
    content_group.add_argument(
        "--exclude-paths", nargs="+", metavar="PATTERN", help="Skip URLs matching these patterns"
    )

    # Output Format
    format_group = parser.add_argument_group("output format")
    format_group.add_argument(
        "--format",
        choices=["markdown", "toon", "json", "sqlite"],
        default="markdown",
        help="Output format (default: markdown)",
    )
    format_group.add_argument(
        "--naming-strategy",
        choices=["full", "short", "flat", "hierarchical"],
        default="full",
        help="File naming strategy",
    )

    # Index Generation
    index_group = parser.add_argument_group("index generation")
    index_group.add_argument(
        "--create-index", action="store_true", help="Create INDEX.md with file tree and navigation"
    )
    index_group.add_argument(
        "--extract-metadata", action="store_true", help="Extract metadata to metadata.json"
    )

    # Update Detection
    cache_group = parser.add_argument_group("update detection & caching")
    cache_group.add_argument(
        "--update-only-changed", action="store_true", help="Only download files that have changed"
    )
    cache_group.add_argument(
        "--incremental", action="store_true", help="Enable incremental mode (resume interrupted downloads)"
    )
    cache_group.add_argument(
        "--cache-dir", type=Path, metavar="PATH", help="Cache directory (default: .docpull-cache)"
    )

    # Git Integration
    git_group = parser.add_argument_group("git integration")
    git_group.add_argument(
        "--git-commit", action="store_true", help="Automatically commit changes after fetch"
    )
    git_group.add_argument(
        "--git-message",
        type=str,
        default="Update docs - {date}",
        metavar="MSG",
        help="Commit message template",
    )

    # Archive Mode
    archive_group = parser.add_argument_group("archive mode")
    archive_group.add_argument(
        "--archive", action="store_true", help="Create compressed archive after fetching"
    )
    archive_group.add_argument(
        "--archive-format",
        choices=["tar.gz", "tar.bz2", "tar.xz", "zip"],
        default="tar.gz",
        help="Archive format",
    )

    # Hooks
    hooks_group = parser.add_argument_group("hooks & plugins")
    hooks_group.add_argument(
        "--post-process-hook", type=Path, metavar="PATH", help="Python file with post-processing hooks"
    )

    return parser


def generate_sample_config(output_path: Path) -> None:
    """
    Generate a sample configuration file.

    Args:
        output_path: Path to save the config file
    """
    config = FetcherConfig()

    # Determine format from extension
    suffix = output_path.suffix.lower()

    try:
        if suffix in [".yaml", ".yml"]:
            config.save_yaml(output_path)
            print(f"Sample YAML config generated: {output_path}")
        elif suffix == ".json":
            config.save_json(output_path)
            print(f"Sample JSON config generated: {output_path}")
        else:
            # Try YAML first, fall back to JSON if PyYAML not available
            try:
                print(f"Warning: Unknown extension {suffix}, generating YAML")
                output_path = output_path.with_suffix(".yaml")
                config.save_yaml(output_path)
                print(f"Sample YAML config generated: {output_path}")
            except ImportError:
                print("PyYAML not installed, generating JSON instead")
                output_path = output_path.with_suffix(".json")
                config.save_json(output_path)
                print(f"Sample JSON config generated: {output_path}")
    except ImportError:
        print("\nERROR: PyYAML is required for YAML config files")
        print("Install it with: pip install docpull[yaml]")
        print("\nAlternatively, use JSON format:")
        print(f"  docpull --generate-config {output_path.with_suffix('.json')}")
        raise


def get_config(args: argparse.Namespace) -> FetcherConfig:
    """
    Get configuration from args and config file.

    Args:
        args: Parsed command-line arguments

    Returns:
        FetcherConfig instance
    """
    # Load from config file if provided
    if args.config:
        try:
            config = FetcherConfig.from_file(args.config)
        except ImportError as e:
            print(f"\nERROR: Error loading config file: {e}")
            if "yaml" in str(e).lower() or "pyyaml" in str(e).lower():
                print("Install PyYAML with: pip install docpull[yaml]")
                print("\nAlternatively, convert your config to JSON format")
            raise
    else:
        config = FetcherConfig()

    # Override with command-line arguments
    if args.output_dir is not None:
        config.output_dir = args.output_dir

    if args.sources is not None:
        # Handle "all" keyword
        if "all" in args.sources:
            config.sources = ["bun", "d3", "nextjs", "plaid", "react", "stripe", "tailwind", "turborepo"]
        else:
            config.sources = args.sources

    if args.rate_limit is not None:
        config.rate_limit = args.rate_limit

    if args.no_skip_existing:
        config.skip_existing = False

    # Handle log level (verbose/quiet shortcuts override --log-level)
    if args.log_level_override is not None:
        config.log_level = args.log_level_override
    elif args.log_level is not None:
        config.log_level = args.log_level

    if args.log_file is not None:
        config.log_file = str(args.log_file)

    # Store dry-run flag in config
    config.dry_run = args.dry_run

    # v1.2.0 features - map CLI arguments to config
    if hasattr(args, "language") and args.language:
        config.language = args.language
    if hasattr(args, "exclude_languages") and args.exclude_languages:
        config.exclude_languages = args.exclude_languages
    if hasattr(args, "deduplicate") and args.deduplicate:
        config.deduplicate = args.deduplicate
    if hasattr(args, "keep_variant") and args.keep_variant:
        config.keep_variant = args.keep_variant
    if hasattr(args, "max_file_size") and args.max_file_size:
        config.max_file_size = args.max_file_size
    if hasattr(args, "max_total_size") and args.max_total_size:
        config.max_total_size = args.max_total_size
    if hasattr(args, "exclude_sections") and args.exclude_sections:
        config.exclude_sections = args.exclude_sections
    if hasattr(args, "include_paths") and args.include_paths:
        config.include_paths = args.include_paths
    if hasattr(args, "exclude_paths") and args.exclude_paths:
        config.exclude_paths = args.exclude_paths
    if hasattr(args, "format") and args.format:
        config.output_format = args.format
    if hasattr(args, "naming_strategy") and args.naming_strategy:
        config.naming_strategy = args.naming_strategy
    if hasattr(args, "create_index") and args.create_index:
        config.create_index = args.create_index
    if hasattr(args, "extract_metadata") and args.extract_metadata:
        config.extract_metadata = args.extract_metadata
    if hasattr(args, "update_only_changed") and args.update_only_changed:
        config.update_only_changed = args.update_only_changed
    if hasattr(args, "incremental") and args.incremental:
        config.incremental = args.incremental
    if hasattr(args, "cache_dir") and args.cache_dir:
        config.cache_dir = args.cache_dir
    if hasattr(args, "git_commit") and args.git_commit:
        config.git_commit = args.git_commit
    if hasattr(args, "git_message") and args.git_message:
        config.git_message = args.git_message
    if hasattr(args, "archive") and args.archive:
        config.archive = args.archive
    if hasattr(args, "archive_format") and args.archive_format:
        config.archive_format = args.archive_format
    if hasattr(args, "post_process_hook") and args.post_process_hook:
        config.post_process_hook = str(args.post_process_hook)

    return config


def run_fetchers(config: FetcherConfig) -> int:
    """
    Run the fetchers based on configuration.

    Args:
        config: FetcherConfig instance

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Setup logging
    logger = setup_logging(
        level=config.log_level,
        log_file=config.log_file,
    )

    logger.info("docpull - Documentation Fetcher")
    logger.info(f"Mode: {'DRY RUN' if config.dry_run else 'FETCH'}")
    logger.info(f"Output directory: {config.output_dir}")
    logger.info(f"Rate limit: {config.rate_limit}s between requests")
    logger.info(f"Skip existing: {config.skip_existing}")
    logger.info(f"Sources: {', '.join(config.sources)}")
    logger.info("")

    if config.dry_run:
        logger.info("DRY RUN MODE: No files will be downloaded")
        logger.info("")

    # Map source names to fetcher classes
    fetcher_map = {
        "bun": BunFetcher,
        "d3": D3DevDocsFetcher,
        "nextjs": NextJSFetcher,
        "plaid": PlaidFetcher,
        "react": ReactFetcher,
        "stripe": StripeFetcher,
        "tailwind": TailwindFetcher,
        "turborepo": TurborepoFetcher,
    }

    # Run fetchers
    errors = 0
    for source in config.sources:
        if source not in fetcher_map:
            logger.error(f"Unknown source: {source}")
            errors += 1
            continue

        try:
            fetcher_class = fetcher_map[source]
            fetcher = fetcher_class(
                output_dir=config.output_dir,
                rate_limit=config.rate_limit,
                skip_existing=config.skip_existing,
                logger=logger,
            )
            fetcher.fetch()

            # v1.2.0: Run post-fetch pipeline
            try:
                orchestrator = create_orchestrator(config)
                files = list(config.output_dir.rglob("*.md"))
                if files:
                    processed_files = orchestrator.run_post_fetch_pipeline(files)
                    logger.info(f"{source}: {len(processed_files)} files after processing")
            except Exception as pipeline_error:
                logger.error(f"Post-fetch pipeline error for {source}: {pipeline_error}", exc_info=True)
                # Don't increment errors - fetching succeeded, post-processing failed

        except Exception as e:
            logger.error(f"Error fetching {source}: {e}", exc_info=True)
            errors += 1

    logger.info("")
    if errors > 0:
        logger.error(f"Completed with {errors} error(s)")
        return 1
    else:
        logger.info("All documentation fetched successfully")
        return 0


def run_generic_fetchers(args: argparse.Namespace) -> int:
    """
    Run generic fetchers for URLs or profile names.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Create config from args (reuse get_config logic)
    try:
        config = get_config(args)
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        return 1

    # Setup logging
    logger = setup_logging(
        level=config.log_level,
        log_file=config.log_file,
    )

    # Extract generic-specific args
    output_dir = config.output_dir
    rate_limit = config.rate_limit
    skip_existing = config.skip_existing
    max_pages = args.max_pages if hasattr(args, "max_pages") else None
    max_depth = args.max_depth if hasattr(args, "max_depth") else 5
    max_concurrent = args.max_concurrent if hasattr(args, "max_concurrent") else 10
    use_js = args.use_js if hasattr(args, "use_js") else False
    show_progress = not (args.no_progress if hasattr(args, "no_progress") else False)

    logger.info("docpull - Universal Documentation Fetcher")
    logger.info(f"Targets: {', '.join(args.targets)}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Rate limit: {rate_limit}s between requests")
    logger.info(f"Skip existing: {skip_existing}")
    logger.info(f"Max concurrent: {max_concurrent}")
    if max_pages:
        logger.info(f"Max pages: {max_pages}")
    logger.info(f"Max depth: {max_depth}")
    if use_js:
        logger.info("JavaScript rendering: ENABLED (slower but handles JS sites)")
    logger.info("")

    # Run async generic fetcher for each target
    errors = 0
    for target in args.targets:
        try:
            logger.info(f"Fetching: {target}")

            # Create per-target output dir
            target_output = output_dir

            fetcher = GenericAsyncFetcher(
                url_or_profile=target,
                output_dir=target_output,
                rate_limit=rate_limit,
                skip_existing=skip_existing,
                logger=logger,
                max_pages=max_pages,
                max_depth=max_depth,
                max_concurrent=max_concurrent,
                use_js=use_js,
                show_progress=show_progress,
            )
            fetcher.fetch()  # This calls asyncio.run() internally

            # v1.2.0: Run post-fetch pipeline
            try:
                orchestrator = create_orchestrator(config)
                files = list(target_output.rglob("*.md"))
                if files:
                    processed_files = orchestrator.run_post_fetch_pipeline(files)
                    logger.info(f"{target}: {len(processed_files)} files after processing")
            except Exception as pipeline_error:
                logger.error(f"Post-fetch pipeline error for {target}: {pipeline_error}", exc_info=True)

        except Exception as e:
            logger.error(f"Error fetching {target}: {e}", exc_info=True)
            errors += 1

    logger.info("")
    if errors > 0:
        logger.error(f"Completed with {errors} error(s)")
        return 1
    else:
        logger.info("All documentation fetched successfully")
        return 0


def run_multi_source_fetch(args: argparse.Namespace) -> int:
    """
    Run multi-source fetch from sources file.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code (0 for success, 1 for error)
    """
    from .sources_config import SourcesConfiguration

    # Setup logging
    log_level = (
        args.log_level_override
        if hasattr(args, "log_level_override") and args.log_level_override
        else (args.log_level if hasattr(args, "log_level") and args.log_level else "INFO")
    )
    logger = setup_logging(
        level=log_level,
        log_file=args.log_file if hasattr(args, "log_file") else None,
    )

    # Load sources configuration
    try:
        sources_config = SourcesConfiguration.load(args.sources_file)
    except Exception as e:
        logger.error(f"Failed to load sources file: {e}", exc_info=True)
        return 1

    logger.info("Multi-Source Fetch")
    logger.info(f"Sources file: {args.sources_file}")
    logger.info(f"Number of sources: {len(sources_config.sources)}")
    logger.info("")

    # Fetch each source
    all_files = []
    errors = 0

    for source_name, source_config in sources_config.sources.items():
        logger.info("=" * 70)
        logger.info(f"Fetching source: {source_name}")
        logger.info("=" * 70)

        try:
            # Create fetcher config for this source
            config = FetcherConfig(
                output_dir=source_config.output,
                rate_limit=source_config.rate_limit or 0.5,
                skip_existing=True,
                log_level=log_level,
                log_file=args.log_file if hasattr(args, "log_file") else None,
                language=source_config.language,
                exclude_languages=source_config.exclude_languages,
                deduplicate=source_config.deduplicate,
                keep_variant=source_config.keep_variant,
                max_file_size=source_config.max_file_size,
                max_total_size=source_config.max_total_size,
                exclude_sections=source_config.exclude_sections,
                include_paths=source_config.include_paths,
                exclude_paths=source_config.exclude_paths,
                output_format=source_config.output_format,
                naming_strategy=source_config.naming_strategy,
                create_index=source_config.create_index,
                update_only_changed=source_config.update_only_changed,
                cache_dir=str(Path(".docpull-cache") / source_name),
            )

            # Create generic fetcher
            fetcher = GenericAsyncFetcher(
                url_or_profile=source_config.url,
                output_dir=Path(source_config.output),
                rate_limit=source_config.rate_limit or 0.5,
                skip_existing=True,
                logger=logger,
                max_pages=source_config.max_pages,
                max_depth=source_config.max_depth or 5,
                max_concurrent=source_config.max_concurrent or 10,
                use_js=source_config.javascript,
                show_progress=True,
            )

            # Fetch
            fetcher.fetch()

            # Run post-fetch pipeline
            orchestrator = create_orchestrator(config)
            files = list(Path(source_config.output).rglob("*.md"))
            if files:
                processed_files = orchestrator.run_post_fetch_pipeline(files)
                all_files.extend(processed_files)
                logger.info(f"{source_name}: {len(processed_files)} files")
            else:
                logger.warning(f"{source_name}: No files found")

        except Exception as e:
            logger.error(f"Error fetching {source_name}: {e}", exc_info=True)
            errors += 1

    # Global post-processing (git, archive)
    if all_files and errors == 0:
        logger.info("")
        logger.info("=" * 70)
        logger.info("Running global post-processing")
        logger.info("=" * 70)

        # Create global config for git/archive
        global_config = FetcherConfig(
            output_dir=Path("."),
            git_commit=sources_config.git_commit,
            git_message=sources_config.git_message,
            archive=sources_config.archive,
            archive_format=sources_config.archive_format,
        )

        orchestrator = create_orchestrator(global_config)
        if sources_config.git_commit:
            orchestrator.commit_to_git()
        if sources_config.archive:
            orchestrator.create_archive()

    logger.info("")
    if errors > 0:
        logger.error(f"Completed with {errors} error(s)")
        return 1
    else:
        logger.info(f"All sources fetched successfully ({len(all_files)} total files)")
        return 0


def main(argv: Optional[list[str]] = None) -> int:
    """
    Main entry point for CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv)

    Returns:
        Exit code
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    # Handle --doctor
    if args.doctor:
        from .doctor import run_doctor

        output_dir = Path(args.output_dir) if args.output_dir else None
        return run_doctor(output_dir=output_dir)

    # Handle --generate-config
    if args.generate_config:
        try:
            generate_sample_config(args.generate_config)
            return 0
        except Exception as e:
            print(f"Error generating config: {e}", file=sys.stderr)
            return 1

    # NEW: Handle --sources-file (multi-source mode)
    if hasattr(args, "sources_file") and args.sources_file:
        return run_multi_source_fetch(args)

    # Determine if using new URL-based interface or legacy source-based
    use_generic = bool(args.targets)

    if use_generic:
        # New URL-based interface
        return run_generic_fetchers(args)
    else:
        # Legacy source-based interface
        try:
            config = get_config(args)
        except Exception as e:
            print(f"Error loading configuration: {e}", file=sys.stderr)
            return 1
        return run_fetchers(config)


if __name__ == "__main__":
    sys.exit(main())
