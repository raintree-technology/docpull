"""CLI adapters for provider-neutral local parity workflows."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from .parity import (
    DEFAULT_CRAWL_OUTPUT_DIR,
    DEFAULT_EXTRACT_OUTPUT_DIR,
    DEFAULT_MAP_OUTPUT_DIR,
    ParityWorkflowError,
    crawl_pack,
    entities_pack,
    extract_pack,
    map_sources,
    research_pack,
)
from .policy import PolicyConfig, PolicyError


def run_extract_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull extract-pack",
        description="Extract known URLs into a local provider-neutral context pack",
    )
    parser.add_argument("url_file", type=Path)
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_EXTRACT_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--objective")
    parser.add_argument("--max-results", type=_positive_int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: extract_pack(
            args.url_file,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            query=args.query,
            objective=args.objective,
            max_results=args.max_results,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        success_label="Extract pack",
    )


def run_map_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull map",
        description="Build a URL-only local map/discovery pack",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    urls = subparsers.add_parser("urls", help="Map a newline/JSON URL file")
    urls.add_argument("url_file", type=Path)
    _add_map_options(urls)

    sitemap = subparsers.add_parser("sitemap", help="Map a local sitemap XML file")
    sitemap.add_argument("sitemap_file", type=Path)
    sitemap.add_argument("--base-url")
    _add_map_options(sitemap)

    args = parser.parse_args(argv)
    input_path = args.url_file if args.command == "urls" else args.sitemap_file
    return _run_and_print(
        lambda: map_sources(
            input_path,
            source_type=args.command,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            query=args.query,
            objective=args.objective,
            base_url=getattr(args, "base_url", None),
            max_results=args.max_results,
        ),
        json_output=args.json_output,
        success_label="Map",
    )


def run_crawl_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull crawl-pack",
        description="Select mapped candidates and fetch them into a local context pack",
    )
    parser.add_argument("input_path", type=Path, help="Discovery pack directory/candidate NDJSON or URL file")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_CRAWL_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument(
        "--select",
        action="append",
        dest="selectors",
        default=[],
        help="Selection policy: top:N, domain:N, domain:example.com:N, score>=X, or manual-file",
    )
    parser.add_argument("--manual-file", type=Path)
    parser.add_argument("--max-results", type=_positive_int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: crawl_pack(
            args.input_path,
            output_dir=args.output_dir,
            policy=_policy(args.policy),
            selectors=args.selectors or None,
            manual_file=args.manual_file,
            max_results=args.max_results,
            dry_run=args.dry_run,
        ),
        json_output=args.json_output,
        success_label="Crawl pack",
    )


def run_research_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull research-pack",
        description="Produce a cited local research result from an existing pack",
    )
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--output-dir", "-o", type=Path)
    parser.add_argument("--schema", type=Path, help="JSON Schema for local structured output validation")
    parser.add_argument("--require-domain", action="append", dest="required_domains", default=[])
    parser.add_argument("--max-excerpts", type=_positive_int, default=8)
    parser.add_argument("--entity-limit", type=int, default=20)
    _add_lifecycle_compat_options(parser)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: research_pack(
            args.pack_dir,
            objective=args.objective,
            output_dir=args.output_dir,
            schema_path=args.schema,
            required_domains=args.required_domains,
            max_excerpts=args.max_excerpts,
            entity_limit=args.entity_limit,
        ),
        json_output=args.json_output,
        success_label="Research pack",
        stream_events=args.stream_events,
    )


def run_entities_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull entities-pack",
        description="Build a local entity/list pack from existing pack evidence",
    )
    parser.add_argument("pack_dir", type=Path)
    parser.add_argument("--output-dir", "-o", type=Path)
    parser.add_argument("--limit", type=_positive_int, default=100)
    parser.add_argument("--require-domain", action="append", dest="required_domains", default=[])
    _add_lifecycle_compat_options(parser)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: entities_pack(
            args.pack_dir,
            output_dir=args.output_dir,
            required_domains=args.required_domains,
            limit=args.limit,
        ),
        json_output=args.json_output,
        success_label="Entities pack",
        stream_events=args.stream_events,
    )


def _add_map_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_MAP_OUTPUT_DIR)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--objective")
    parser.add_argument("--max-results", type=_positive_int)
    parser.add_argument("--json", action="store_true", dest="json_output")


def _add_lifecycle_compat_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--wait",
        action="store_true",
        help="Accepted for hosted API parity; local runs are sync",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Accepted for hosted API parity; recorded by caller only",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        help="Accepted for hosted API parity; local runs write a single poll.report.json",
    )
    parser.add_argument("--stream-events", action="store_true", help="Print local lifecycle events after run")


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
    stream_events: bool = False,
) -> int:
    console = Console()
    try:
        payload = action()
        if json_output:
            console.print_json(data=payload)
        else:
            summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
            output_dir = payload.get("output_dir") if isinstance(payload, dict) else None
            console.print(f"[green]{success_label}:[/green] {output_dir} {summary}")
        if stream_events and isinstance(payload, dict):
            _print_events(console, payload)
        return 0
    except (ParityWorkflowError, PolicyError, ValueError) as err:
        console.print(f"[red]{success_label} error:[/red] " + escape(str(err)))
        return 1


def _print_events(console: Console, payload: dict[str, object]) -> None:
    artifacts = payload.get("artifacts")
    output_dir = payload.get("output_dir")
    if not isinstance(artifacts, dict) or not isinstance(output_dir, str):
        return
    events_ref = artifacts.get("events")
    if not isinstance(events_ref, str):
        return
    events_path = Path(output_dir) / events_ref
    if not events_path.exists():
        return
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            console.print(line)
            continue
        console.print_json(data=event)


__all__ = [
    "run_extract_pack_cli",
    "run_map_cli",
    "run_crawl_pack_cli",
    "run_research_pack_cli",
    "run_entities_pack_cli",
]
