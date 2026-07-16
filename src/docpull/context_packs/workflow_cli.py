"""Public CLI adapters for evidence-backed workflow-protocol packs."""

from __future__ import annotations

import argparse
import json
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
from .relationship import DEFAULT_RELATIONSHIP_OUTPUT_DIR, build_relationship_pack


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


def run_relationship_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull relationship-pack",
        description="Extract cited ownership/operator/acquisition/franchise/investment review candidates",
    )
    parser.add_argument("sources", nargs="*", help="HTTPS sources, domains, or local pack paths")
    parser.add_argument("--inputs", type=Path, help="JSON array of entity/source input objects")
    parser.add_argument("--subject", help="Subject name for a single positional source")
    parser.add_argument("--location-scope", help="Optional location scope for a single positional source")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_RELATIONSHIP_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--max-pages-per-source", type=_positive_int, default=4)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    sources: list[str | dict[str, object]] = list(args.sources)
    if args.inputs:
        payload = json.loads(args.inputs.read_text(encoding="utf-8"))
        if not isinstance(payload, list) or not all(isinstance(item, (str, dict)) for item in payload):
            parser.error("--inputs must contain a JSON array of strings or objects")
        sources.extend(payload)
    if args.subject or args.location_scope:
        if len(sources) != 1 or not isinstance(sources[0], str):
            parser.error("--subject/--location-scope require exactly one positional source")
        sources = [
            {
                "url": sources[0],
                "name": args.subject or sources[0],
                "location_scope": args.location_scope,
            }
        ]
    if not sources:
        parser.error("provide at least one source or --inputs JSON file")
    return _run_and_print(
        lambda: build_relationship_pack(
            sources,
            output_dir=args.output_dir,
            policy=PolicyConfig.from_file(args.policy) if args.policy else None,
            max_pages_per_source=args.max_pages_per_source,
        ),
        json_output=args.json_output,
        success_label="Relationship pack",
    )


__all__ = [
    "run_brand_pack_cli",
    "run_image_pack_cli",
    "run_policy_pack_cli",
    "run_relationship_pack_cli",
    "run_product_pack_cli",
    "run_screenshot_pack_cli",
    "run_styleguide_pack_cli",
]
