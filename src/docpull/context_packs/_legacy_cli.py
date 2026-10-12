"""Compatibility import path for the original context-pack CLI adapters.

The commands are public again through :mod:`docpull.context_packs.workflow_cli`;
this module remains importable so pre-6.2 integrations do not break.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..policy import PolicyConfig
from .brand import DEFAULT_BRAND_OUTPUT_DIR, build_brand_pack
from .cli import _positive_int, _run_and_print
from .product import DEFAULT_PRODUCT_OUTPUT_DIR, build_product_pack
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


def _policy(path: Path | None) -> PolicyConfig:
    return PolicyConfig.from_file(path) if path else PolicyConfig()
