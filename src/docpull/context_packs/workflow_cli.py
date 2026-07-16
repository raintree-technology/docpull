"""Public CLI adapters for evidence-backed workflow-protocol packs."""

from __future__ import annotations

import argparse
from pathlib import Path

from ..policy import PolicyConfig
from ._legacy_cli import (
    run_brand_pack_cli,
    run_image_pack_cli,
    run_product_pack_cli,
    run_screenshot_pack_cli,
    run_styleguide_pack_cli,
)
from .cli import _positive_int, _run_and_print
from .policy_pack import DEFAULT_POLICY_OUTPUT_DIR, build_policy_pack


def run_policy_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull policy-pack",
        description="Discover policy documents and emit clause-level evidence without legal conclusions",
    )
    parser.add_argument("domain_or_url")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_POLICY_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--max-pages", type=_positive_int, default=16)
    parser.add_argument("--baseline-pack", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_policy_pack(
            args.domain_or_url,
            output_dir=args.output_dir,
            policy=PolicyConfig.from_file(args.policy) if args.policy else None,
            max_pages=args.max_pages,
            baseline_pack=args.baseline_pack,
        ),
        json_output=args.json_output,
        success_label="Policy pack",
    )


__all__ = [
    "run_brand_pack_cli",
    "run_image_pack_cli",
    "run_policy_pack_cli",
    "run_product_pack_cli",
    "run_screenshot_pack_cli",
    "run_styleguide_pack_cli",
]
