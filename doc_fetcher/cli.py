"""Command-line interface for doc_fetcher."""

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from .config import FetcherConfig
from .fetchers import (
    StripeFetcher,
    PlaidFetcher,
    NextJSFetcher,
    D3DevDocsFetcher,
    BunFetcher,
    TailwindFetcher,
    ReactFetcher,
)
from .utils.logging_config import setup_logging


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for CLI."""
    parser = argparse.ArgumentParser(
        prog="docpull",
        description="Fetch and convert documentation from various sources to markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch all sources with default settings
  docpull

  # Fetch only Stripe docs
  docpull --sources stripe

  # Fetch with custom output directory and rate limit
  docpull --output-dir ./my-docs --rate-limit 1.0

  # Use a config file
  docpull --config config.yaml

  # Generate a sample config file
  docpull --generate-config config.yaml
        """,
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
        "--sources",
        "-s",
        nargs="+",
        choices=["stripe", "plaid", "nextjs", "d3", "bun", "tailwind", "react", "all"],
        default=None,
        help="Sources to fetch (default: all)",
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
        version="%(prog)s 1.0.0",
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

    if suffix in [".yaml", ".yml"]:
        config.save_yaml(output_path)
        print(f"Sample YAML config generated: {output_path}")
    elif suffix == ".json":
        config.save_json(output_path)
        print(f"Sample JSON config generated: {output_path}")
    else:
        print(f"Warning: Unknown extension {suffix}, generating YAML")
        output_path = output_path.with_suffix(".yaml")
        config.save_yaml(output_path)
        print(f"Sample YAML config generated: {output_path}")


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
        config = FetcherConfig.from_file(args.config)
    else:
        config = FetcherConfig()

    # Override with command-line arguments
    if args.output_dir is not None:
        config.output_dir = args.output_dir

    if args.sources is not None:
        # Handle "all" keyword
        if "all" in args.sources:
            config.sources = ["stripe", "plaid", "nextjs", "d3", "bun", "tailwind", "react"]
        else:
            config.sources = args.sources

    if args.rate_limit is not None:
        config.rate_limit = args.rate_limit

    if args.no_skip_existing:
        config.skip_existing = False

    if args.log_level is not None:
        config.log_level = args.log_level

    if args.log_file is not None:
        config.log_file = str(args.log_file)

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

    logger.info("=" * 80)
    logger.info("Documentation Fetcher")
    logger.info("=" * 80)
    logger.info(f"Output directory: {config.output_dir}")
    logger.info(f"Rate limit: {config.rate_limit}s between requests")
    logger.info(f"Skip existing: {config.skip_existing}")
    logger.info(f"Sources: {', '.join(config.sources)}")
    logger.info("")

    # Map source names to fetcher classes
    fetcher_map = {
        "stripe": StripeFetcher,
        "plaid": PlaidFetcher,
        "nextjs": NextJSFetcher,
        "d3": D3DevDocsFetcher,
        "bun": BunFetcher,
        "tailwind": TailwindFetcher,
        "react": ReactFetcher,
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
        except Exception as e:
            logger.error(f"Error fetching {source}: {e}", exc_info=True)
            errors += 1

    logger.info("")
    logger.info("=" * 80)
    if errors > 0:
        logger.error(f"Completed with {errors} error(s)")
        return 1
    else:
        logger.info("All documentation fetched successfully!")
        return 0


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point for CLI.

    Args:
        argv: Command-line arguments (defaults to sys.argv)

    Returns:
        Exit code
    """
    parser = create_parser()
    args = parser.parse_args(argv)

    # Handle --generate-config
    if args.generate_config:
        try:
            generate_sample_config(args.generate_config)
            return 0
        except Exception as e:
            print(f"Error generating config: {e}", file=sys.stderr)
            return 1

    # Get configuration
    try:
        config = get_config(args)
    except Exception as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        return 1

    # Run fetchers
    return run_fetchers(config)


if __name__ == "__main__":
    sys.exit(main())
