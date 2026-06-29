"""CLI adapters for typed local context packs."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from ..accounting import parse_budget_value
from ..policy import PolicyConfig, PolicyError
from .brand import DEFAULT_BRAND_OUTPUT_DIR, build_brand_pack
from .common import ContextPackError
from .product import DEFAULT_PRODUCT_OUTPUT_DIR, build_product_pack
from .schema_extract import DEFAULT_SCHEMA_OUTPUT_DIR, extract_schema
from .search import DEFAULT_SEARCH_OUTPUT_DIR, build_search_pack
from .styleguide import DEFAULT_STYLEGUIDE_OUTPUT_DIR, build_styleguide_pack
from .visuals import (
    DEFAULT_IMAGE_OUTPUT_DIR,
    DEFAULT_SCREENSHOT_OUTPUT_DIR,
    build_image_pack,
    capture_screenshot_pack,
)


def run_brand_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull brand-pack",
        description="Build a local evidence-backed brand profile pack",
    )
    parser.add_argument("domain_or_url")
    parser.add_argument("--email")
    parser.add_argument("--name")
    parser.add_argument("--ticker")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_BRAND_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--allow-free-email", action="store_true")
    parser.add_argument("--no-download-assets", action="store_true")
    parser.add_argument("--max-pages", type=_positive_int, default=6)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_brand_pack(
            args.domain_or_url,
            email=args.email,
            name=args.name,
            ticker=args.ticker,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            allow_free_email=args.allow_free_email,
            download_assets=not args.no_download_assets,
            max_pages=args.max_pages,
        ),
        json_output=args.json_output,
        success_label="Brand pack",
    )


def run_styleguide_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull styleguide-pack",
        description="Build a local styleguide/design-token pack",
    )
    parser.add_argument("domain_or_url")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_STYLEGUIDE_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--render", action="store_true", help="Use explicit trusted rendering gate")
    parser.add_argument("--max-stylesheets", type=_positive_int, default=12)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_styleguide_pack(
            args.domain_or_url,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            render=args.render,
            max_stylesheets=args.max_stylesheets,
        ),
        json_output=args.json_output,
        success_label="Styleguide pack",
    )


def run_product_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull product-pack",
        description="Build cited product and pricing records",
    )
    parser.add_argument("url_or_domain")
    parser.add_argument("--mode", choices=["page", "site"], default="page")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_PRODUCT_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--max-pages", type=_positive_int, default=8)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_product_pack(
            args.url_or_domain,
            mode=args.mode,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            max_pages=args.max_pages,
        ),
        json_output=args.json_output,
        success_label="Product pack",
    )


def run_extract_schema_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull extract-schema",
        description="Extract a JSON shape from local evidence and validate it",
    )
    parser.add_argument("url_or_pack")
    parser.add_argument("--schema", type=Path, required=True)
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_SCHEMA_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--fact-check", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: extract_schema(
            args.url_or_pack,
            schema_path=args.schema,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            fact_check=args.fact_check,
        ),
        json_output=args.json_output,
        success_label="Extract schema",
    )


def run_image_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull image-pack",
        description="Build a local image manifest and bounded asset pack",
    )
    parser.add_argument("url_or_pack")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_IMAGE_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--no-download-assets", action="store_true")
    parser.add_argument("--max-assets", type=_positive_int, default=40)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_image_pack(
            args.url_or_pack,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            download_assets=not args.no_download_assets,
            max_assets=args.max_assets,
        ),
        json_output=args.json_output,
        success_label="Image pack",
    )


def run_screenshot_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull screenshot-pack",
        description="Capture a local screenshot pack through explicit rendering",
    )
    parser.add_argument("url")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_SCREENSHOT_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--viewport", default="1280x720")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--wait-for", choices=["load", "domcontentloaded", "networkidle"], default="load")
    parser.add_argument("--agent-browser-bin")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: capture_screenshot_pack(
            args.url,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            viewport=args.viewport,
            full_page=args.full_page,
            wait_for=args.wait_for,
            agent_browser_binary=args.agent_browser_bin,
        ),
        json_output=args.json_output,
        success_label="Screenshot pack",
    )


def run_search_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull search-pack",
        description="Build a local-first search result pack",
    )
    parser.add_argument("query")
    parser.add_argument(
        "--provider",
        choices=["local", "parallel", "tavily", "exa", "context"],
        default="local",
    )
    parser.add_argument("--pack-dir", type=Path)
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_SEARCH_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    parser.add_argument("--exclude-domain", action="append", dest="exclude_domains", default=[])
    parser.add_argument("--max-results", type=_positive_int, default=10)
    parser.add_argument("--scrape", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--budget", type=parse_budget_value)
    parser.add_argument("--max-estimated-cost", type=parse_budget_value)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_search_pack(
            args.query,
            provider=args.provider,
            pack_dir=args.pack_dir,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            include_domains=args.include_domains,
            exclude_domains=args.exclude_domains,
            max_results=args.max_results,
            scrape=args.scrape,
            dry_run=args.dry_run,
            budget=args.budget,
            max_estimated_cost=args.max_estimated_cost,
        ),
        json_output=args.json_output,
        success_label="Search pack",
    )


def _policy(path: Path | None) -> PolicyConfig:
    return PolicyConfig.from_file(path) if path else PolicyConfig()


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be an integer") from err
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _run_and_print(
    action: Callable[[], dict[str, Any]],
    *,
    json_output: bool,
    success_label: str,
) -> int:
    console = Console()
    try:
        payload = action()
        if json_output:
            console.print_json(data=payload)
        else:
            summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
            output_dir = payload.get("output_dir") or payload.get("artifacts", {}).get("result")
            console.print(f"[green]{success_label}:[/green] {output_dir} {summary}")
        return 0
    except (ContextPackError, PolicyError, ValueError) as err:
        console.print(f"[red]{success_label} error:[/red] " + escape(str(err)))
        return 1
