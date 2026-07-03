"""CLI adapters for typed local context packs."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from ..policy import PolicyError
from .common import ContextPackError
from .dataset import DEFAULT_DATASET_OUTPUT_DIR, build_dataset_pack
from .feed import DEFAULT_FEED_OUTPUT_DIR, build_feed_pack
from .openapi import DEFAULT_OPENAPI_OUTPUT_DIR, build_openapi_pack
from .package import DEFAULT_PACKAGE_OUTPUT_DIR, build_package_pack
from .paper import DEFAULT_PAPER_OUTPUT_DIR, build_paper_pack
from .repo import DEFAULT_REPO_OUTPUT_DIR, build_repo_pack
from .standards import DEFAULT_STANDARDS_OUTPUT_DIR, build_standards_pack
from .transcript import DEFAULT_TRANSCRIPT_OUTPUT_DIR, build_transcript_pack
from .typed import PrepareLevel
from .wiki import DEFAULT_WIKI_OUTPUT_DIR, build_wiki_pack


def run_openapi_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull openapi-pack",
        description="Build a local v3 pack from an OpenAPI JSON/YAML spec",
    )
    parser.add_argument("source", help="Local path or HTTPS URL for an OpenAPI spec")
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_OPENAPI_OUTPUT_DIR)
    parser.add_argument("--chunk-tokens", type=_positive_int, default=4000)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_openapi_pack(
            args.source,
            output_dir=args.output_dir,
            chunk_tokens=args.chunk_tokens,
        ),
        json_output=args.json_output,
        success_label="OpenAPI pack",
    )


def run_feed_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull feed-pack",
        description="Build a local v3 pack from an RSS, Atom, JSON Feed, or feed-advertising page",
    )
    parser.add_argument(
        "source",
        help="Local feed path, HTTPS feed URL, or HTTPS page that advertises a feed",
    )
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FEED_OUTPUT_DIR)
    parser.add_argument("--max-items", type=_positive_int, default=50)
    parser.add_argument("--chunk-tokens", type=_positive_int, default=4000)
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_feed_pack(
            args.source,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
        ),
        json_output=args.json_output,
        success_label="Feed pack",
    )


def run_paper_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull paper-pack",
        description="Build a local v3 pack from local papers, arXiv IDs, DOIs, or PubMed IDs",
    )
    parser.add_argument(
        "sources", nargs="+", help="Local file, arxiv:<id>, doi:<doi>, pmid:<id>, or HTTPS metadata URL"
    )
    _add_typed_common_args(parser, DEFAULT_PAPER_OUTPUT_DIR, max_items_default=50)
    parser.add_argument(
        "--include-full-text",
        action="store_true",
        help="Include local full text and arXiv PDF text when parser backends are available",
    )
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_paper_pack(
            args.sources,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            include_full_text=args.include_full_text,
            prepare_level=_prepare_level(args),
            cache_dir=_cache_dir(args),
            cache_ttl_days=args.cache_ttl,
        ),
        json_output=args.json_output,
        success_label="Paper pack",
    )


def run_repo_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull repo-pack",
        description="Build a local v3 pack from a public GitHub repository",
    )
    parser.add_argument("source", help="GitHub URL or owner/repo[@ref]")
    _add_typed_common_args(parser, DEFAULT_REPO_OUTPUT_DIR, max_items_default=30)
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_repo_pack(
            args.source,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            prepare_level=_prepare_level(args),
            cache_dir=_cache_dir(args),
            cache_ttl_days=args.cache_ttl,
        ),
        json_output=args.json_output,
        success_label="Repo pack",
    )


def run_package_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull package-pack",
        description="Build a local v3 pack from npm or PyPI package metadata",
    )
    parser.add_argument("source", help="npm:<name> or pypi:<name>")
    _add_typed_common_args(parser, DEFAULT_PACKAGE_OUTPUT_DIR, max_items_default=25)
    parser.add_argument(
        "--include-repo",
        action="store_true",
        help="Also include bounded GitHub repo context when metadata links to GitHub",
    )
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_package_pack(
            args.source,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            include_repo=args.include_repo,
            prepare_level=_prepare_level(args),
            cache_dir=_cache_dir(args),
            cache_ttl_days=args.cache_ttl,
        ),
        json_output=args.json_output,
        success_label="Package pack",
    )


def run_standards_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull standards-pack",
        description="Build a local v3 pack from RFC, IETF, W3C, or WHATWG standards",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="rfc:<n>, ietf:<draft>, w3c:<shortname>, whatwg:<url>, or HTTPS standard URL",
    )
    _add_typed_common_args(parser, DEFAULT_STANDARDS_OUTPUT_DIR, max_items_default=20)
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_standards_pack(
            args.sources,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            prepare_level=_prepare_level(args),
            cache_dir=_cache_dir(args),
            cache_ttl_days=args.cache_ttl,
        ),
        json_output=args.json_output,
        success_label="Standards pack",
    )


def run_dataset_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull dataset-pack",
        description="Build a local v3 pack from local CSV, TSV, JSON, NDJSON, SQLite, or Parquet files",
    )
    parser.add_argument("sources", nargs="+", help="Local dataset files")
    _add_typed_common_args(parser, DEFAULT_DATASET_OUTPUT_DIR, max_items_default=50)
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_dataset_pack(
            args.sources,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            prepare_level=_prepare_level(args),
        ),
        json_output=args.json_output,
        success_label="Dataset pack",
    )


def run_transcript_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull transcript-pack",
        description="Build a local v3 pack from VTT, SRT, text, JSON, or direct transcript URLs",
    )
    parser.add_argument("sources", nargs="+", help="Local transcript files or HTTPS transcript URLs")
    _add_typed_common_args(parser, DEFAULT_TRANSCRIPT_OUTPUT_DIR, max_items_default=200)
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_transcript_pack(
            args.sources,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            prepare_level=_prepare_level(args),
        ),
        json_output=args.json_output,
        success_label="Transcript pack",
    )


def run_wiki_pack_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull wiki-pack",
        description="Build a local v3 pack from Wikimedia/MediaWiki REST page content",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        help="wiki:<title>, wikipedia:<title>, or Wikimedia/MediaWiki page URL",
    )
    _add_typed_common_args(parser, DEFAULT_WIKI_OUTPUT_DIR, max_items_default=30)
    args = parser.parse_args(argv)
    return _run_and_print(
        lambda: build_wiki_pack(
            args.sources,
            output_dir=args.output_dir,
            max_items=args.max_items,
            chunk_tokens=args.chunk_tokens,
            prepare_level=_prepare_level(args),
            cache_dir=_cache_dir(args),
            cache_ttl_days=args.cache_ttl,
        ),
        json_output=args.json_output,
        success_label="Wiki pack",
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be an integer") from err
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _add_typed_common_args(
    parser: argparse.ArgumentParser,
    default_output_dir: Path,
    *,
    max_items_default: int,
) -> None:
    parser.add_argument("--output-dir", "-o", type=Path, default=default_output_dir)
    parser.add_argument("--max-items", type=_positive_int, default=max_items_default)
    parser.add_argument("--chunk-tokens", type=_positive_int, default=4000)
    parser.add_argument("--prepare", action="store_true", help="Also write agent-level sidecars")
    parser.add_argument("--eval-grade", action="store_true", help="Also write eval-grade sidecars")
    parser.add_argument("--cache", action="store_true", help="Cache typed remote metadata/API responses")
    parser.add_argument("--cache-dir", type=Path, default=Path(".docpull-cache/typed-packs"))
    parser.add_argument("--cache-ttl", type=_positive_int, default=7, help="Typed remote cache TTL in days")
    parser.add_argument("--json", action="store_true", dest="json_output")


def _prepare_level(args: argparse.Namespace) -> PrepareLevel:
    if getattr(args, "eval_grade", False):
        return "eval"
    if getattr(args, "prepare", False):
        return "agent"
    return "raw"


def _cache_dir(args: argparse.Namespace) -> Path | None:
    return args.cache_dir if getattr(args, "cache", False) else None


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
