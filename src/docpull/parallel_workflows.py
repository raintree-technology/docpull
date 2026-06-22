"""Parallel-backed context-pack workflows for docpull."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import getpass
import importlib.resources
import importlib.util
import json
import re
import shlex
import sys
import tempfile
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .accounting import (
    RunAccounting,
    blocked_action,
    budget_block_payload,
    default_route_steps,
    effective_budget_limit,
    extract_budget_flags,
    maybe_write_run_accounting,
    paid_action_blocked,
)
from .conversion.chunking import TokenCounter, chunk_markdown
from .http import AsyncHttpClient, PerHostRateLimiter
from .models.document import DocumentRecord
from .pipeline.manifest import CorpusManifest
from .provider_keys import (
    PROJECT_ENV_FILENAME,
    SECRETS_FILENAME,
    ProviderApiKeyLookup,
    ProviderKeyError,
    clean_api_key,
    find_project_env_path,
    key_assignment,
    lookup_provider_api_key,
    parse_env_assignment,
    quote_env_value,
    read_key_file,
    user_secrets_path,
    validate_provider_api_key,
    write_provider_secret,
)
from .security.robots import RobotsChecker
from .security.url_validator import UrlValidator
from .source_scoring import score_source_entries
from .time_utils import utc_now_iso

PACK_SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("packs/parallel-context-pack")
PARALLEL_API_KEY_ENV = "PARALLEL_API_KEY"
PARALLEL_INSTALL_COMMAND = "pip install 'docpull[parallel]'"
PARALLEL_API_KEY_COMMAND = f'export {PARALLEL_API_KEY_ENV}="<your-parallel-api-key>"'
PARALLEL_SECRETS_FILENAME = SECRETS_FILENAME
PARALLEL_PROJECT_ENV_FILENAME = PROJECT_ENV_FILENAME
PARALLEL_ACCOUNT_URL = "https://platform.parallel.ai"
PARALLEL_DOCS_URL = "https://docs.parallel.ai"
PARALLEL_LLMS_TXT_URL = "https://docs.parallel.ai/llms.txt"
PARALLEL_OPENAPI_URL = "https://docs.parallel.ai/public-openapi.json"
PARALLEL_DRY_RUN_EXAMPLE = (
    'docpull parallel context-pack "Build an agent context pack" '
    '--query "API docs" --dry-run --max-estimated-cost 0.05'
)
DEFAULT_MODE = "advanced"
SEARCH_MODES = ("turbo", "basic", "advanced")
DEFAULT_EXTRACT_LIMIT = 8
MAX_EXTRACT_URLS_PER_REQUEST = 20
DEFAULT_MAX_TOKENS = 4000
DEFAULT_TASK_PROCESSOR = "base"
DEFAULT_MAX_FULL_CONTENT_CHARS = 50000
DEFAULT_MAX_ESTIMATED_COST_USD = 0.05
DEFAULT_SEARCH_PACK_OUTPUT_DIR = Path("packs/parallel-search-pack")
DEFAULT_DISCOVERY_PACK_OUTPUT_DIR = Path("packs/parallel-discovery-pack")
DEFAULT_EXTRACT_PACK_OUTPUT_DIR = Path("packs/parallel-extract-pack")
DEFAULT_FALLBACK_PACK_OUTPUT_DIR = Path("packs/parallel-fallback-pack")
DEFAULT_TASK_PACK_OUTPUT_DIR = Path("packs/parallel-task-pack")
DEFAULT_DIFF_BRIEF_OUTPUT_DIR = Path("packs/parallel-diff-brief")
DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR = Path("packs/parallel-findall")
PACKAGE_EXAMPLE_FIXTURE = "parallel-search-extract.json"
SEARCH_BASE_COST_USD = 0.005
SEARCH_ADDITIONAL_RESULT_COST_USD = 0.001
EXTRACT_COST_PER_URL_USD = 0.001
TASK_PROCESSOR_COST_USD = {
    "lite": 0.005,
    "base": 0.010,
    "core": 0.025,
    "core2x": 0.050,
    "pro": 0.100,
    "ultra": 0.300,
    "ultra2x": 0.600,
    "ultra4x": 1.200,
    "ultra8x": 2.400,
}
TASK_PROCESSORS = tuple(TASK_PROCESSOR_COST_USD)
FAST_TASK_PROCESSORS = tuple(f"{processor}-fast" for processor in TASK_PROCESSORS)
VALID_TASK_PROCESSORS = TASK_PROCESSORS + FAST_TASK_PROCESSORS
FINDALL_GENERATOR_COST_USD = {
    "preview": (0.10, 0.00),
    "base": (0.25, 0.03),
    "core": (2.00, 0.15),
    "pro": (10.00, 1.00),
}
ENTITY_SEARCH_BASE_COST_USD = 0.005
ENTITY_SEARCH_ADDITIONAL_RESULT_COST_USD = 0.00005
MONITOR_EXECUTION_COST_USD = {
    "lite": 0.003,
    "base": 0.010,
}
MAX_RECIPE_BYTES = 1_000_000


class ParallelWorkflowError(RuntimeError):
    """User-facing workflow error."""


ParallelApiKeyLookup = ProviderApiKeyLookup


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be an integer") from err
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _extract_limit(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > MAX_EXTRACT_URLS_PER_REQUEST:
        raise argparse.ArgumentTypeError(f"must be at most {MAX_EXTRACT_URLS_PER_REQUEST}")
    return parsed


def _token_budget(value: str) -> int:
    parsed = _positive_int(value)
    if parsed < 100:
        raise argparse.ArgumentTypeError("must be at least 100")
    return parsed


def _at_least_600_int(value: str) -> int:
    parsed = _positive_int(value)
    if parsed < 600:
        raise argparse.ArgumentTypeError("must be at least 600")
    return parsed


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must be a number") from err
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be at least 0")
    return parsed


def _date_string(value: str) -> str:
    try:
        dt.date.fromisoformat(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from err
    return value


@dataclass
class ParallelContextPack:
    """Normalized Parallel workflow data, independent of SDK response classes."""

    objective: str
    queries: list[str]
    workflow: str = "context-pack"
    mode: str = DEFAULT_MODE
    session_id: str | None = None
    search_id: str | None = None
    extract_id: str | None = None
    task_run_id: str | None = None
    search_results: list[dict[str, Any]] = field(default_factory=list)
    extract_results: list[dict[str, Any]] = field(default_factory=list)
    extract_errors: list[dict[str, Any]] = field(default_factory=list)
    task_brief: str | None = None
    task_basis: list[Any] = field(default_factory=list)
    request_options: dict[str, Any] = field(default_factory=dict)
    warnings: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    estimated_cost_usd: float | None = None


def create_parallel_parser() -> argparse.ArgumentParser:
    """Create the ``docpull parallel`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="docpull parallel",
        description="Build agent-ready context packs from Parallel web intelligence APIs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser(
        "auth",
        help="Check local Parallel SDK and API-key configuration without storing secrets",
        description="Check local Parallel SDK and API-key configuration without storing secrets",
    )
    auth.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit machine-readable local configuration status.",
    )
    auth.add_argument(
        "--redact-paths",
        action="store_true",
        help="Redact local filesystem paths from JSON output for CI/agent logs.",
    )

    probe = subparsers.add_parser(
        "probe",
        help="Explicitly validate the Parallel API key with provider-aware live probes",
    )
    probe.add_argument(
        "--mode",
        choices=["safe", "validation", "smoke"],
        default="safe",
        help=(
            "Probe depth. safe reports local readiness only for Parallel; validation runs an "
            "opt-in auth-gate request; smoke runs a minimal live Search call."
        ),
    )
    probe.add_argument("--json", action="store_true", dest="json_output", help="Print probe JSON")
    probe.add_argument(
        "--require-verified",
        action="store_true",
        help="Exit non-zero unless the Parallel key is live verified and workflow-ready.",
    )
    probe.add_argument(
        "--redact-paths",
        action="store_true",
        help="Redact local filesystem paths from JSON output for CI/agent logs.",
    )
    probe.add_argument(
        "--include-account-metadata",
        action="store_true",
        help="Include provider account metadata when returned by a probe.",
    )
    probe.add_argument("--timeout", type=float, default=15.0, help="Per-request probe timeout in seconds.")
    probe.add_argument(
        "--max-estimated-cost",
        type=float,
        default=0.01,
        help="Local spend guard for smoke probes.",
    )

    init = subparsers.add_parser(
        "init",
        help="Store a Parallel API key in a durable local docpull secrets file",
        description=(
            "Store a Parallel API key without printing it. By default this writes "
            "~/.config/docpull/secrets.env with 0600 permissions. Use --project "
            "to write .env.local in the current directory and update .gitignore."
        ),
    )
    init.add_argument(
        "--project",
        action="store_true",
        help="Write .env.local in the current directory instead of user-level config.",
    )
    init.add_argument(
        "--from-stdin",
        action="store_true",
        help="Read the API key from stdin instead of prompting securely.",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing Parallel key entry in the target file.",
    )
    init.add_argument(
        "--no-gitignore-update",
        action="store_true",
        help="With --project, do not add .env.local to .gitignore.",
    )

    context = subparsers.add_parser(
        "context-pack",
        help="Run Parallel Search + Extract and persist a docpull context pack",
    )
    context.add_argument("objective", help="Natural-language research objective")
    context.add_argument(
        "--query",
        action="append",
        dest="queries",
        default=[],
        help="Keyword search query. Repeat 2-3 times for best results.",
    )
    context.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    context.add_argument(
        "--mode",
        choices=SEARCH_MODES,
        default=DEFAULT_MODE,
        help="Parallel Search mode (default: advanced)",
    )
    context.add_argument(
        "--extract-limit",
        type=_extract_limit,
        default=DEFAULT_EXTRACT_LIMIT,
        help=(
            "Maximum selected Search URLs to extract "
            f"(default: {DEFAULT_EXTRACT_LIMIT}, max: {MAX_EXTRACT_URLS_PER_REQUEST})"
        ),
    )
    context.add_argument(
        "--max-tokens-per-file",
        type=_token_budget,
        default=DEFAULT_MAX_TOKENS,
        help="Chunk size for NDJSON context records (default: 4000)",
    )
    context.add_argument(
        "--include-domain",
        action="append",
        dest="include_domains",
        default=[],
        help="Restrict Search results to this domain or suffix. Repeat as needed.",
    )
    context.add_argument(
        "--exclude-domain",
        action="append",
        dest="exclude_domains",
        default=[],
        help="Exclude this domain or suffix from Search results. Repeat as needed.",
    )
    context.add_argument(
        "--after-date",
        type=_date_string,
        help="Restrict Search results to content published on or after YYYY-MM-DD.",
    )
    context.add_argument(
        "--max-search-results",
        type=_positive_int,
        help="Maximum Search results to request before URL selection.",
    )
    context.add_argument(
        "--max-search-chars-total",
        type=_positive_int,
        help="Upper bound on Search excerpt characters across all results.",
    )
    context.add_argument(
        "--max-extract-chars-total",
        type=_positive_int,
        help="Upper bound on Extract excerpt characters across all selected URLs.",
    )
    context.add_argument(
        "--max-full-content-chars",
        type=_positive_int,
        default=DEFAULT_MAX_FULL_CONTENT_CHARS,
        help="Maximum full-content characters per extracted URL (default: 50000).",
    )
    context.add_argument(
        "--no-full-content",
        action="store_true",
        help="Do not request full_content from Extract; use excerpts only.",
    )
    context.add_argument(
        "--fetch-max-age-seconds",
        type=_at_least_600_int,
        help="Parallel fetch policy freshness threshold for Search and Extract (minimum: 600).",
    )
    context.add_argument(
        "--fetch-timeout-seconds",
        type=_positive_int,
        help="Parallel fetch policy timeout for live source fetches.",
    )
    context.add_argument(
        "--disable-cache-fallback",
        action="store_true",
        help="Require live Parallel fetches when fetch policy refresh fails.",
    )
    context.add_argument(
        "--excerpt-chars-per-result",
        type=_positive_int,
        help="Parallel excerpt character budget per result for Search and Extract.",
    )
    context.add_argument(
        "--location",
        help="ISO 3166-1 alpha-2 country code for geo-targeted Search results.",
    )
    context.add_argument(
        "--client-model",
        help="Model consuming results, passed to Parallel for response optimization.",
    )
    context.add_argument(
        "--task-brief",
        action="store_true",
        help="Also run Parallel Task and write a cited brief.md",
    )
    context.add_argument(
        "--task-processor",
        default=DEFAULT_TASK_PROCESSOR,
        help=(
            "Parallel Task processor for --task-brief "
            f"(default: base; choices: {', '.join(VALID_TASK_PROCESSORS)})"
        ),
    )
    context.add_argument(
        "--max-estimated-cost",
        type=_nonnegative_float,
        default=DEFAULT_MAX_ESTIMATED_COST_USD,
        help="Abort before live calls if estimated cost exceeds this many USD (default: 0.05).",
    )
    context.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request plan and estimated cost without calling Parallel.",
    )

    search_pack = subparsers.add_parser(
        "search-pack",
        help="Run Parallel Search only and persist ranked web results as a pack",
    )
    search_pack.add_argument("objective", help="Natural-language research objective")
    search_pack.add_argument(
        "--query",
        action="append",
        dest="queries",
        default=[],
        help="Keyword search query. Repeat 2-3 times for best results.",
    )
    search_pack.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_SEARCH_PACK_OUTPUT_DIR)
    search_pack.add_argument("--mode", choices=SEARCH_MODES, default=DEFAULT_MODE)
    search_pack.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    search_pack.add_argument("--exclude-domain", action="append", dest="exclude_domains", default=[])
    search_pack.add_argument("--after-date", type=_date_string)
    search_pack.add_argument("--max-search-results", type=_positive_int)
    search_pack.add_argument("--max-search-chars-total", type=_positive_int)
    search_pack.add_argument("--fetch-max-age-seconds", type=_at_least_600_int)
    search_pack.add_argument("--fetch-timeout-seconds", type=_positive_int)
    search_pack.add_argument("--disable-cache-fallback", action="store_true")
    search_pack.add_argument("--excerpt-chars-per-result", type=_positive_int)
    search_pack.add_argument("--location")
    search_pack.add_argument("--client-model")
    search_pack.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.01)
    search_pack.add_argument("--dry-run", action="store_true")

    discover_docs = subparsers.add_parser(
        "discover-docs",
        help="Use Parallel Search to discover canonical source URLs for core docpull crawls",
    )
    discover_docs.add_argument("objective", help="Natural-language source discovery objective")
    discover_docs.add_argument("--query", action="append", dest="queries", default=[])
    discover_docs.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_DISCOVERY_PACK_OUTPUT_DIR)
    discover_docs.add_argument("--mode", choices=SEARCH_MODES, default=DEFAULT_MODE)
    discover_docs.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    discover_docs.add_argument("--exclude-domain", action="append", dest="exclude_domains", default=[])
    discover_docs.add_argument("--after-date", type=_date_string)
    discover_docs.add_argument("--max-search-results", type=_positive_int)
    discover_docs.add_argument("--max-search-chars-total", type=_positive_int)
    discover_docs.add_argument("--fetch-max-age-seconds", type=_at_least_600_int)
    discover_docs.add_argument("--fetch-timeout-seconds", type=_positive_int)
    discover_docs.add_argument("--disable-cache-fallback", action="store_true")
    discover_docs.add_argument("--excerpt-chars-per-result", type=_positive_int)
    discover_docs.add_argument("--location")
    discover_docs.add_argument("--client-model")
    discover_docs.add_argument("--crawl-profile", choices=["rag", "mirror", "quick", "llm"], default="mirror")
    discover_docs.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.01)
    discover_docs.add_argument("--dry-run", action="store_true")

    extract_pack = subparsers.add_parser(
        "extract-pack",
        help="Run Parallel Extract for known URLs and persist a docpull context pack",
    )
    extract_pack.add_argument("urls", nargs="*", help="HTTPS URLs to extract, up to 20")
    extract_pack.add_argument("--url-file", type=Path, help="JSON array or newline-delimited URL file")
    extract_pack.add_argument("--objective", default="Extract known URLs into an agent context pack")
    extract_pack.add_argument("--query", action="append", dest="queries", default=[])
    extract_pack.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_EXTRACT_PACK_OUTPUT_DIR)
    extract_pack.add_argument("--max-tokens-per-file", type=_token_budget, default=DEFAULT_MAX_TOKENS)
    extract_pack.add_argument("--max-extract-chars-total", type=_positive_int)
    extract_pack.add_argument(
        "--max-full-content-chars",
        type=_positive_int,
        default=DEFAULT_MAX_FULL_CONTENT_CHARS,
    )
    extract_pack.add_argument("--no-full-content", action="store_true")
    extract_pack.add_argument("--fetch-max-age-seconds", type=_at_least_600_int)
    extract_pack.add_argument("--fetch-timeout-seconds", type=_positive_int)
    extract_pack.add_argument("--disable-cache-fallback", action="store_true")
    extract_pack.add_argument("--excerpt-chars-per-result", type=_positive_int)
    extract_pack.add_argument("--client-model")
    extract_pack.add_argument("--session-id")
    extract_pack.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.05)
    extract_pack.add_argument("--dry-run", action="store_true")

    fallback_pack = subparsers.add_parser(
        "fallback-pack",
        help="Try core docpull extraction first, then fall back to Parallel Extract for failed URLs",
    )
    fallback_pack.add_argument("urls", nargs="*", help="HTTPS URLs to extract, up to 20")
    fallback_pack.add_argument("--url-file", type=Path, help="JSON array or newline-delimited URL file")
    fallback_pack.add_argument("--objective", default="Extract URLs with docpull and Parallel fallback")
    fallback_pack.add_argument("--query", action="append", dest="queries", default=[])
    fallback_pack.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FALLBACK_PACK_OUTPUT_DIR)
    fallback_pack.add_argument("--profile", choices=["rag", "mirror", "quick", "llm"], default="rag")
    fallback_pack.add_argument("--max-tokens-per-file", type=_token_budget, default=DEFAULT_MAX_TOKENS)
    fallback_pack.add_argument("--max-core-chars", type=_positive_int, default=DEFAULT_MAX_FULL_CONTENT_CHARS)
    fallback_pack.add_argument("--max-extract-chars-total", type=_positive_int)
    fallback_pack.add_argument(
        "--max-full-content-chars",
        type=_positive_int,
        default=DEFAULT_MAX_FULL_CONTENT_CHARS,
    )
    fallback_pack.add_argument("--no-full-content", action="store_true")
    fallback_pack.add_argument("--fetch-max-age-seconds", type=_at_least_600_int)
    fallback_pack.add_argument("--fetch-timeout-seconds", type=_positive_int)
    fallback_pack.add_argument("--disable-cache-fallback", action="store_true")
    fallback_pack.add_argument("--excerpt-chars-per-result", type=_positive_int)
    fallback_pack.add_argument("--client-model")
    fallback_pack.add_argument("--session-id")
    fallback_pack.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.05)
    fallback_pack.add_argument("--dry-run", action="store_true")

    task_pack = subparsers.add_parser(
        "task-pack",
        help="Create one Parallel Task run and persist the run/result as a pack",
    )
    task_pack.add_argument("input", nargs="?", help="Task input text or JSON string")
    task_pack.add_argument("--input-file", type=Path, help="Read task input from a text or JSON file")
    task_pack.add_argument(
        "--processor",
        default=DEFAULT_TASK_PROCESSOR,
        help=f"Parallel Task processor (default: base; choices: {', '.join(VALID_TASK_PROCESSORS)})",
    )
    task_pack.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_TASK_PACK_OUTPUT_DIR)
    task_pack.add_argument("--output-schema", type=Path, help="JSON file containing a Task output schema")
    task_pack.add_argument("--output-schema-json", help="Inline JSON Task output schema")
    task_pack.add_argument("--input-schema", type=Path, help="Optional JSON file containing an input schema")
    task_pack.add_argument("--source-include-domain", action="append", dest="include_domains", default=[])
    task_pack.add_argument("--source-exclude-domain", action="append", dest="exclude_domains", default=[])
    task_pack.add_argument("--source-after-date", type=_date_string, dest="after_date")
    task_pack.add_argument("--location")
    task_pack.add_argument("--previous-interaction-id")
    task_pack.add_argument("--enable-events", action="store_true")
    task_pack.add_argument("--mcp-server", action="append", dest="mcp_servers", default=[])
    task_pack.add_argument("--webhook-url")
    task_pack.add_argument("--webhook-event-type", action="append", dest="webhook_event_types", default=[])
    task_pack.add_argument("--metadata", action="append", dest="metadata_pairs", default=[])
    task_pack.add_argument("--api-timeout", type=_positive_int, default=3600)
    task_pack.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.05)
    task_pack.add_argument("--dry-run", action="store_true")

    task_result = subparsers.add_parser(
        "task-result",
        help="Persist a Parallel Task run result by run ID",
    )
    task_result.add_argument("run_id")
    task_result.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-task-result"))
    task_result.add_argument("--api-timeout", type=_positive_int, default=3600)

    task_events = subparsers.add_parser(
        "task-events",
        help="Persist Parallel Task run progress events by run ID",
    )
    task_events.add_argument("run_id")
    task_events.add_argument("--last-event-id")
    task_events.add_argument("--timeout", type=_positive_int, default=60)
    task_events.add_argument("--limit", type=_positive_int, default=100)
    task_events.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-task-events"))

    diff_brief = subparsers.add_parser(
        "diff-brief",
        help="Diff two context packs and ask Parallel Task for a change brief",
    )
    diff_brief.add_argument("old_pack_dir", type=Path, help="Older context pack directory")
    diff_brief.add_argument("new_pack_dir", type=Path, help="Newer context pack directory")
    diff_brief.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_DIFF_BRIEF_OUTPUT_DIR)
    diff_brief.add_argument(
        "--processor",
        default=DEFAULT_TASK_PROCESSOR,
        help=f"Parallel Task processor (default: base; choices: {', '.join(VALID_TASK_PROCESSORS)})",
    )
    diff_brief.add_argument("--api-timeout", type=_positive_int, default=3600)
    diff_brief.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.05)
    diff_brief.add_argument("--dry-run", action="store_true")

    entity = subparsers.add_parser(
        "entity-pack",
        help="Run Parallel Entity Search and persist candidate dossiers",
    )
    entity.add_argument("objective", help="Natural-language people/company search objective")
    entity.add_argument("--entity-type", choices=["people", "companies"], default="companies")
    entity.add_argument("--match-limit", type=_positive_int, default=25)
    entity.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-entity-pack"))
    entity.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.01)
    entity.add_argument("--dry-run", action="store_true")

    findall = subparsers.add_parser(
        "findall-pack",
        help="Run Parallel FindAll and persist discovered entity candidates",
    )
    findall.add_argument("objective", help="Natural-language entity discovery objective")
    findall.add_argument("--entity-type", default="companies")
    findall.add_argument(
        "--condition",
        action="append",
        dest="conditions",
        default=[],
        help="Match condition as name=description, or just description. Repeat as needed.",
    )
    findall.add_argument("--generator", choices=["preview", "base", "core", "pro"], default="preview")
    findall.add_argument("--match-limit", type=_positive_int, default=5)
    findall.add_argument(
        "--exclude-candidate",
        action="append",
        dest="exclude_candidates",
        default=[],
        help="Candidate to exclude as name or id=name. Repeat as needed.",
    )
    findall.add_argument("--metadata", action="append", dest="metadata_pairs", default=[])
    findall.add_argument("--webhook-url")
    findall.add_argument("--webhook-event-type", action="append", dest="webhook_event_types", default=[])
    findall.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-findall-pack"))
    findall.add_argument(
        "--wait",
        action="store_true",
        help="Poll for result candidates before writing the pack.",
    )
    findall.add_argument("--poll-interval", type=_positive_int, default=5)
    findall.add_argument("--timeout", type=_positive_int, default=600)
    findall.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.10)
    findall.add_argument("--dry-run", action="store_true")

    findall_ingest = subparsers.add_parser(
        "findall-ingest-pack",
        help="Run Parallel FindAll ingest and persist the inferred schema",
    )
    findall_ingest.add_argument("objective")
    findall_ingest.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    findall_result = subparsers.add_parser(
        "findall-result-pack",
        help="Persist a Parallel FindAll result snapshot",
    )
    findall_result.add_argument("findall_id")
    findall_result.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    findall_schema = subparsers.add_parser(
        "findall-schema-pack",
        help="Persist a Parallel FindAll run schema",
    )
    findall_schema.add_argument("findall_id")
    findall_schema.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    findall_enrich = subparsers.add_parser(
        "findall-enrich-pack",
        help="Add FindAll enrichment and persist the updated schema",
    )
    findall_enrich.add_argument("findall_id")
    findall_enrich.add_argument("--output-schema", type=Path, required=True)
    findall_enrich.add_argument("--processor", default="core")
    findall_enrich.add_argument("--mcp-server", action="append", dest="mcp_servers", default=[])
    findall_enrich.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    findall_extend = subparsers.add_parser(
        "findall-extend-pack",
        help="Extend a Parallel FindAll run and persist the response",
    )
    findall_extend.add_argument("findall_id")
    findall_extend.add_argument("--additional-match-limit", type=_positive_int, required=True)
    findall_extend.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    findall_cancel = subparsers.add_parser(
        "findall-cancel-pack",
        help="Cancel a Parallel FindAll run and persist the response",
    )
    findall_cancel.add_argument("findall_id")
    findall_cancel.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    findall_events = subparsers.add_parser(
        "findall-events-pack",
        help="Persist streamed Parallel FindAll events",
    )
    findall_events.add_argument("findall_id")
    findall_events.add_argument("--limit", type=_positive_int, default=100)
    findall_events.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR)

    taskgroup = subparsers.add_parser(
        "taskgroup-pack",
        help="Run Parallel TaskGroup over JSON/NDJSON inputs and persist outputs",
    )
    taskgroup.add_argument("inputs", type=Path, help="JSON array or NDJSON input rows")
    taskgroup.add_argument("--prompt-template", help="Format string used to build each task input")
    taskgroup.add_argument(
        "--processor",
        default="lite",
        help=f"Parallel Task processor (default: lite; choices: {', '.join(VALID_TASK_PROCESSORS)})",
    )
    taskgroup.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-taskgroup-pack"))
    taskgroup.add_argument(
        "--wait",
        action="store_true",
        help="Fetch run outputs after adding the group runs.",
    )
    taskgroup.add_argument("--poll-interval", type=_positive_int, default=5)
    taskgroup.add_argument("--timeout", type=_positive_int, default=600)
    taskgroup.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.05)
    taskgroup.add_argument("--dry-run", action="store_true")

    monitor = subparsers.add_parser(
        "monitor-pack",
        help="Create monitors or persist monitor events as context packs",
    )
    monitor_subparsers = monitor.add_subparsers(dest="monitor_command", required=True)
    monitor_create = monitor_subparsers.add_parser(
        "create",
        help="Create a Parallel Monitor and save metadata",
    )
    monitor_create.add_argument("query", nargs="?", help="Natural-language monitor query")
    monitor_create.add_argument("--type", choices=["event_stream", "snapshot"], default="event_stream")
    monitor_create.add_argument("--task-run-id", help="Baseline Task run ID for snapshot monitors")
    monitor_create.add_argument("--frequency", default="1d")
    monitor_create.add_argument("--processor", choices=["lite", "base"], default="lite")
    monitor_create.add_argument(
        "--output-schema",
        type=Path,
        help="JSON event output schema for event-stream monitors",
    )
    monitor_create.add_argument("--include-backfill", action="store_true")
    monitor_create.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    monitor_create.add_argument("--exclude-domain", action="append", dest="exclude_domains", default=[])
    monitor_create.add_argument("--after-date", type=_date_string)
    monitor_create.add_argument("--location")
    monitor_create.add_argument("--webhook-url")
    monitor_create.add_argument(
        "--webhook-event-type",
        action="append",
        dest="webhook_event_types",
        default=[],
    )
    monitor_create.add_argument("--metadata", action="append", dest="metadata_pairs", default=[])
    monitor_create.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-monitor-pack"))
    monitor_create.add_argument("--max-estimated-cost", type=_nonnegative_float, default=0.01)
    monitor_create.add_argument("--dry-run", action="store_true")

    monitor_events = monitor_subparsers.add_parser(
        "events",
        help="Persist Monitor events as a context pack",
    )
    monitor_events.add_argument("monitor_id", help="Parallel monitor ID")
    monitor_events.add_argument("--limit", type=_positive_int, default=20)
    monitor_events.add_argument("--cursor")
    monitor_events.add_argument("--event-group-id")
    monitor_events.add_argument("--include-completions", action="store_true")
    monitor_events.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("packs/parallel-monitor-events"),
    )
    monitor_list = monitor_subparsers.add_parser(
        "list",
        help="Persist a page of monitors as a context pack",
    )
    monitor_list.add_argument("--limit", type=_positive_int, default=20)
    monitor_list.add_argument("--cursor")
    monitor_list.add_argument("--status", action="append", choices=["active", "cancelled"], default=[])
    monitor_list.add_argument("--type", action="append", choices=["event_stream", "snapshot"], default=[])
    monitor_list.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-monitors"))

    monitor_retrieve = monitor_subparsers.add_parser(
        "retrieve",
        help="Persist one monitor's metadata as a context pack",
    )
    monitor_retrieve.add_argument("monitor_id")
    monitor_retrieve.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-monitor"))

    monitor_update = monitor_subparsers.add_parser(
        "update",
        help="Update an event-stream monitor and persist the updated metadata",
    )
    monitor_update.add_argument("monitor_id")
    monitor_update.add_argument("--query")
    monitor_update.add_argument("--frequency")
    monitor_update.add_argument("--include-domain", action="append", dest="include_domains", default=[])
    monitor_update.add_argument("--exclude-domain", action="append", dest="exclude_domains", default=[])
    monitor_update.add_argument("--after-date", type=_date_string)
    monitor_update.add_argument("--location")
    monitor_update.add_argument("--webhook-url")
    monitor_update.add_argument("--clear-webhook", action="store_true")
    monitor_update.add_argument(
        "--webhook-event-type",
        action="append",
        dest="webhook_event_types",
        default=[],
    )
    monitor_update.add_argument("--metadata", action="append", dest="metadata_pairs", default=[])
    monitor_update.add_argument("--clear-metadata", action="store_true")
    monitor_update.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-monitor"))

    monitor_cancel = monitor_subparsers.add_parser(
        "cancel",
        help="Cancel a monitor and persist the returned metadata",
    )
    monitor_cancel.add_argument("monitor_id")
    monitor_cancel.add_argument("--output-dir", "-o", type=Path, default=Path("packs/parallel-monitor"))

    monitor_trigger = monitor_subparsers.add_parser(
        "trigger",
        help="Trigger an immediate monitor run and persist the action metadata",
    )
    monitor_trigger.add_argument("monitor_id")
    monitor_trigger.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=Path("packs/parallel-monitor-trigger"),
    )

    api_pack = subparsers.add_parser(
        "api-pack",
        help="Turn an llms.txt index or OpenAPI spec into a docpull context pack",
    )
    api_pack.add_argument("source", help="URL or local path to llms.txt or OpenAPI JSON")
    api_pack.add_argument("--kind", choices=["auto", "llms", "openapi"], default="auto")
    api_pack.add_argument("--output-dir", "-o", type=Path, default=Path("packs/api-pack"))

    import_parser = subparsers.add_parser(
        "import",
        help="Build a context pack from a saved Parallel Search/Extract fixture JSON",
    )
    import_parser.add_argument("fixture", type=Path, help="Fixture JSON to import")
    import_parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    import_parser.add_argument(
        "--max-tokens-per-file",
        type=_token_budget,
        default=DEFAULT_MAX_TOKENS,
        help="Chunk size for NDJSON context records (default: 4000)",
    )

    demo_parser = subparsers.add_parser(
        "demo",
        help="Build a context pack from the packaged Parallel example fixture",
    )
    demo_parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    demo_parser.add_argument(
        "--max-tokens-per-file",
        type=_token_budget,
        default=DEFAULT_MAX_TOKENS,
        help="Chunk size for NDJSON context records (default: 4000)",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run a YAML/JSON Parallel workflow recipe",
        description=(
            "Run a YAML/JSON Parallel workflow recipe. Supported workflows include "
            "context-pack, search-pack, discover-docs, extract-pack, fallback-pack, "
            "diff-brief, task-pack, entity-pack, findall-pack, taskgroup-pack, "
            "monitor-pack, api-pack, task-result, task-events, and FindAll lifecycle packs."
        ),
    )
    run_parser.add_argument(
        "recipe",
        type=Path,
        help="Parallel workflow recipe YAML or JSON path (for example: context-pack, api-pack)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request plan and estimated cost without calling Parallel.",
    )
    run_parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        help="Override the recipe output directory.",
    )
    run_parser.add_argument(
        "--max-estimated-cost",
        type=_nonnegative_float,
        help="Override the recipe cost guard.",
    )

    return parser


def run_parallel_cli(argv: list[str] | None = None) -> int:
    """Entrypoint for ``docpull parallel``."""
    try:
        parsed_argv, budget_limit, explain_route = extract_budget_flags(argv)
    except ValueError as err:
        Console().print("[red]Parallel workflow error:[/red] " + escape(str(err)))
        return 1
    parser = create_parallel_parser()
    args = parser.parse_args(parsed_argv)
    console = Console()
    route_steps = default_route_steps(include_provider=True, budget_limit_usd=budget_limit)
    if explain_route:
        console.print("[bold]Parallel route[/bold]")
        console.print(f"Budget: {'not set' if budget_limit is None else f'${budget_limit:.6f}'}")
        for step in route_steps:
            payload = step.to_dict()
            detail = f" - {payload['detail']}" if payload.get("detail") else ""
            console.print(f"- {payload['name']}: {payload['status']} ({payload['cost_class']}){detail}")
        return 0
    if budget_limit is not None and hasattr(args, "max_estimated_cost"):
        current_guard = getattr(args, "max_estimated_cost", None)
        if current_guard is not None:
            args.max_estimated_cost = effective_budget_limit(current_guard, budget_limit)

    try:
        paid_command = _parallel_command_paid_capable(args)
        if paid_command and paid_action_blocked(budget_limit, estimated_cost_usd=0.0):
            action = f"parallel:{args.command}"
            if getattr(args, "command", None) == "monitor-pack":
                action = f"parallel:monitor-pack:{getattr(args, 'monitor_command', 'unknown')}"
            payload = {
                "schema_version": PACK_SCHEMA_VERSION,
                "generated_at": utc_now_iso(),
                "workflow": args.command,
                **budget_block_payload(
                    action,
                    budget_limit_usd=budget_limit,
                    estimated_cost_usd=0.0,
                    provider="parallel",
                ),
            }
            output_dir = _parallel_output_dir_from_args(args)
            maybe_write_run_accounting(
                output_dir,
                budget_limit_usd=budget_limit,
                paid_capable=True,
                accounting=RunAccounting(
                    budget_limit_usd=budget_limit,
                    estimated_paid_cost_usd=0.0,
                    blocked_actions=[
                        blocked_action(
                            action,
                            budget_limit_usd=budget_limit,
                            estimated_cost_usd=0.0,
                            provider="parallel",
                        )
                    ],
                    route_steps=route_steps,
                    command=f"parallel {args.command}",
                ),
            )
            if getattr(args, "dry_run", False):
                console.print_json(data=payload)
                return 0
            raise ParallelWorkflowError(payload["blocked_action"]["reason"])
        if args.command == "init":
            init_result = init_parallel_auth(
                project=args.project,
                from_stdin=args.from_stdin,
                force=args.force,
                update_gitignore=not args.no_gitignore_update,
            )
            console.print(
                "[green]Stored Parallel API key:[/green] "
                f"{init_result['key_source']} -> {init_result['path']}"
            )
            if init_result.get("gitignore_updated"):
                console.print(f"[green]Updated .gitignore:[/green] {init_result['gitignore_path']}")
            console.print("Secret handling: key value was not printed and is not written to pack artifacts.")
            return 0
        if args.command == "auth":
            status = get_parallel_auth_status(redact_paths=args.redact_paths)
            if args.json_output:
                console.print_json(data=status)
            else:
                _print_parallel_auth_status(console, status)
            return 0 if status["ready"] else 1
        if args.command == "probe":
            from .provider_cli import run_provider_cli

            forwarded = [
                "probe",
                "--provider",
                "parallel",
                "--mode",
                args.mode,
                "--timeout",
                str(args.timeout),
                "--max-estimated-cost",
                str(args.max_estimated_cost),
            ]
            if args.json_output:
                forwarded.append("--json")
            if args.require_verified:
                forwarded.append("--require-verified")
            if args.redact_paths:
                forwarded.append("--redact-paths")
            if args.include_account_metadata:
                forwarded.append("--include-account-metadata")
            return run_provider_cli(forwarded)
        if args.command == "search-pack":
            queries = list(args.queries or []) or [args.objective]
            source_policy = _build_source_policy(
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                after_date=args.after_date,
            )
            fetch_policy = _build_fetch_policy(
                max_age_seconds=args.fetch_max_age_seconds,
                timeout_seconds=args.fetch_timeout_seconds,
                disable_cache_fallback=args.disable_cache_fallback,
            )
            request_options = _build_request_options(
                source_policy=source_policy,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=args.location,
                max_search_results=args.max_search_results,
                max_search_chars_total=args.max_search_chars_total,
                max_extract_chars_total=None,
                max_full_content_chars=None,
                client_model=args.client_model,
                full_content=False,
            )
            estimated_cost = estimate_search_pack_cost(max_search_results=args.max_search_results)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "search-pack",
                        "objective": args.objective,
                        "queries": queries,
                        "mode": args.mode,
                        "request_options": request_options,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated Search cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_search_pack(
                objective=args.objective,
                queries=queries,
                mode=args.mode,
                output_dir=args.output_dir,
                source_policy=source_policy,
                fetch_policy=fetch_policy,
                max_search_results=args.max_search_results,
                max_search_chars_total=args.max_search_chars_total,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=args.location,
                client_model=args.client_model,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "discover-docs":
            queries = list(args.queries or []) or [args.objective]
            source_policy = _build_source_policy(
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                after_date=args.after_date,
            )
            fetch_policy = _build_fetch_policy(
                max_age_seconds=args.fetch_max_age_seconds,
                timeout_seconds=args.fetch_timeout_seconds,
                disable_cache_fallback=args.disable_cache_fallback,
            )
            request_options = _build_request_options(
                source_policy=source_policy,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=args.location,
                max_search_results=args.max_search_results,
                max_search_chars_total=args.max_search_chars_total,
                max_extract_chars_total=None,
                max_full_content_chars=None,
                client_model=args.client_model,
                full_content=False,
            )
            estimated_cost = estimate_search_pack_cost(max_search_results=args.max_search_results)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "discover-docs",
                        "objective": args.objective,
                        "queries": queries,
                        "mode": args.mode,
                        "crawl_profile": args.crawl_profile,
                        "request_options": request_options,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated discovery cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_discover_docs_pack(
                objective=args.objective,
                queries=queries,
                mode=args.mode,
                output_dir=args.output_dir,
                source_policy=source_policy,
                fetch_policy=fetch_policy,
                max_search_results=args.max_search_results,
                max_search_chars_total=args.max_search_chars_total,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=args.location,
                client_model=args.client_model,
                crawl_profile=args.crawl_profile,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "extract-pack":
            urls = _load_extract_urls(args.urls, args.url_file)
            fetch_policy = _build_fetch_policy(
                max_age_seconds=args.fetch_max_age_seconds,
                timeout_seconds=args.fetch_timeout_seconds,
                disable_cache_fallback=args.disable_cache_fallback,
            )
            request_options = _build_request_options(
                source_policy=None,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=None,
                max_search_results=None,
                max_search_chars_total=None,
                max_extract_chars_total=args.max_extract_chars_total,
                max_full_content_chars=None if args.no_full_content else args.max_full_content_chars,
                client_model=args.client_model,
                full_content=not args.no_full_content,
            )
            estimated_cost = estimate_extract_pack_cost(url_count=len(urls))
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "extract-pack",
                        "objective": args.objective,
                        "urls": urls,
                        "queries": args.queries,
                        "request_options": request_options,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated Extract cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_extract_pack(
                urls=urls,
                objective=args.objective,
                queries=list(args.queries or []),
                output_dir=args.output_dir,
                max_tokens_per_file=args.max_tokens_per_file,
                max_extract_chars_total=args.max_extract_chars_total,
                max_full_content_chars=None if args.no_full_content else args.max_full_content_chars,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                client_model=args.client_model,
                full_content=not args.no_full_content,
                session_id=args.session_id,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "fallback-pack":
            urls = _load_extract_urls(args.urls, args.url_file)
            fetch_policy = _build_fetch_policy(
                max_age_seconds=args.fetch_max_age_seconds,
                timeout_seconds=args.fetch_timeout_seconds,
                disable_cache_fallback=args.disable_cache_fallback,
            )
            request_options = _build_request_options(
                source_policy=None,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=None,
                max_search_results=None,
                max_search_chars_total=None,
                max_extract_chars_total=args.max_extract_chars_total,
                max_full_content_chars=None if args.no_full_content else args.max_full_content_chars,
                client_model=args.client_model,
                full_content=not args.no_full_content,
            )
            request_options["core_profile"] = args.profile
            request_options["max_core_chars"] = args.max_core_chars
            estimated_cost = estimate_extract_pack_cost(url_count=len(urls))
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "fallback-pack",
                        "objective": args.objective,
                        "urls": urls,
                        "queries": args.queries,
                        "request_options": request_options,
                        "estimated_worst_case_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated worst-case fallback cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_fallback_pack(
                urls=urls,
                objective=args.objective,
                queries=list(args.queries or []),
                output_dir=args.output_dir,
                profile=args.profile,
                max_tokens_per_file=args.max_tokens_per_file,
                max_core_chars=args.max_core_chars,
                max_extract_chars_total=args.max_extract_chars_total,
                max_full_content_chars=None if args.no_full_content else args.max_full_content_chars,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                client_model=args.client_model,
                full_content=not args.no_full_content,
                session_id=args.session_id,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "task-pack":
            task_input = _load_task_input(args.input, args.input_file)
            processor = _validate_task_processor(args.processor)
            output_schema = _load_optional_json_schema(args.output_schema, args.output_schema_json)
            input_schema = _load_optional_json_schema(args.input_schema, None)
            source_policy = _build_source_policy(
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                after_date=args.after_date,
            )
            metadata = _parse_metadata_pairs(args.metadata_pairs)
            webhook = _build_webhook(args.webhook_url, args.webhook_event_types)
            mcp_servers = _load_mcp_servers(args.mcp_servers)
            estimated_cost = estimate_task_pack_cost(processor=processor)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "task-pack",
                        "processor": processor,
                        "source_policy": source_policy,
                        "location": args.location,
                        "previous_interaction_id": args.previous_interaction_id,
                        "enable_events": args.enable_events,
                        "mcp_server_count": len(mcp_servers),
                        "webhook": _jsonable(webhook),
                        "metadata": metadata,
                        "has_output_schema": output_schema is not None,
                        "has_input_schema": input_schema is not None,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated Task cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_task_pack(
                task_input=task_input,
                processor=processor,
                output_dir=args.output_dir,
                output_schema=output_schema,
                input_schema=input_schema,
                source_policy=source_policy,
                location=args.location,
                previous_interaction_id=args.previous_interaction_id,
                enable_events=args.enable_events,
                mcp_servers=mcp_servers,
                webhook=webhook,
                metadata=metadata,
                api_timeout=args.api_timeout,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "task-result":
            pack = run_task_result_pack(
                run_id=args.run_id,
                output_dir=args.output_dir,
                api_timeout=args.api_timeout,
            )
        elif args.command == "task-events":
            pack = run_task_events_pack(
                run_id=args.run_id,
                last_event_id=args.last_event_id,
                timeout=args.timeout,
                limit=args.limit,
                output_dir=args.output_dir,
            )
        elif args.command == "diff-brief":
            processor = _validate_task_processor(args.processor)
            estimated_cost = estimate_task_pack_cost(processor=processor)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "diff-brief",
                        "old_pack_dir": str(args.old_pack_dir),
                        "new_pack_dir": str(args.new_pack_dir),
                        "processor": processor,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated diff brief cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_diff_brief_pack(
                old_pack_dir=args.old_pack_dir,
                new_pack_dir=args.new_pack_dir,
                output_dir=args.output_dir,
                processor=processor,
                api_timeout=args.api_timeout,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "context-pack":
            queries = list(args.queries or [])
            if not queries:
                queries = [args.objective]
                console.print(
                    "[yellow]Warning:[/yellow] no --query values supplied; "
                    "using the objective as the only search query."
                )
            source_policy = _build_source_policy(
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                after_date=args.after_date,
            )
            fetch_policy = _build_fetch_policy(
                max_age_seconds=args.fetch_max_age_seconds,
                timeout_seconds=args.fetch_timeout_seconds,
                disable_cache_fallback=args.disable_cache_fallback,
            )
            request_options = _build_request_options(
                source_policy=source_policy,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=args.location,
                max_search_results=args.max_search_results,
                max_search_chars_total=args.max_search_chars_total,
                max_extract_chars_total=args.max_extract_chars_total,
                max_full_content_chars=None if args.no_full_content else args.max_full_content_chars,
                client_model=args.client_model,
                full_content=not args.no_full_content,
            )
            estimated_cost = estimate_context_pack_cost(
                extract_limit=args.extract_limit,
                max_search_results=args.max_search_results,
                task_brief=args.task_brief,
                task_processor=args.task_processor,
            )
            if args.dry_run:
                console.print_json(
                    data={
                        "objective": args.objective,
                        "queries": queries,
                        "mode": args.mode,
                        "extract_limit": args.extract_limit,
                        "request_options": request_options,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    "Estimated Parallel cost "
                    f"${estimated_cost:.3f} exceeds --max-estimated-cost ${args.max_estimated_cost:.3f}. "
                    "Lower --extract-limit/--max-search-results, disable --task-brief, or raise the limit."
                )
            pack = run_live_context_pack(
                objective=args.objective,
                queries=queries,
                output_dir=args.output_dir,
                mode=args.mode,
                extract_limit=args.extract_limit,
                max_tokens_per_file=args.max_tokens_per_file,
                source_policy=source_policy,
                max_search_results=args.max_search_results,
                max_search_chars_total=args.max_search_chars_total,
                max_extract_chars_total=args.max_extract_chars_total,
                max_full_content_chars=None if args.no_full_content else args.max_full_content_chars,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=args.excerpt_chars_per_result,
                location=args.location,
                client_model=args.client_model,
                full_content=not args.no_full_content,
                task_brief=args.task_brief,
                task_processor=args.task_processor,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "entity-pack":
            estimated_cost = estimate_entity_search_cost(match_limit=args.match_limit)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "entity-pack",
                        "objective": args.objective,
                        "entity_type": args.entity_type,
                        "match_limit": args.match_limit,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated Entity Search cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_entity_pack(
                objective=args.objective,
                entity_type=args.entity_type,
                match_limit=args.match_limit,
                output_dir=args.output_dir,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "findall-pack":
            conditions = _parse_match_conditions(args.conditions, args.objective)
            exclude_list = _parse_exclude_candidates(args.exclude_candidates)
            metadata = _parse_metadata_pairs(args.metadata_pairs)
            webhook = _build_webhook(args.webhook_url, args.webhook_event_types)
            estimated_cost = estimate_findall_cost(generator=args.generator, match_limit=args.match_limit)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "findall-pack",
                        "objective": args.objective,
                        "entity_type": args.entity_type,
                        "generator": args.generator,
                        "match_limit": args.match_limit,
                        "match_conditions": conditions,
                        "exclude_list": exclude_list,
                        "metadata": metadata,
                        "webhook": _jsonable(webhook),
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated FindAll cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_findall_pack(
                objective=args.objective,
                entity_type=args.entity_type,
                match_conditions=conditions,
                generator=args.generator,
                match_limit=args.match_limit,
                exclude_list=exclude_list,
                metadata=metadata,
                webhook=webhook,
                output_dir=args.output_dir,
                wait=args.wait,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "findall-ingest-pack":
            pack = run_findall_ingest_pack(
                objective=args.objective,
                output_dir=args.output_dir,
            )
        elif args.command == "findall-result-pack":
            pack = run_findall_result_pack(findall_id=args.findall_id, output_dir=args.output_dir)
        elif args.command == "findall-schema-pack":
            pack = run_findall_schema_pack(findall_id=args.findall_id, output_dir=args.output_dir)
        elif args.command == "findall-enrich-pack":
            pack = run_findall_enrich_pack(
                findall_id=args.findall_id,
                output_schema=_load_json_file(args.output_schema, "output_schema"),
                processor=args.processor,
                mcp_servers=_load_mcp_servers(args.mcp_servers),
                output_dir=args.output_dir,
            )
        elif args.command == "findall-extend-pack":
            pack = run_findall_extend_pack(
                findall_id=args.findall_id,
                additional_match_limit=args.additional_match_limit,
                output_dir=args.output_dir,
            )
        elif args.command == "findall-cancel-pack":
            pack = run_findall_cancel_pack(findall_id=args.findall_id, output_dir=args.output_dir)
        elif args.command == "findall-events-pack":
            pack = run_findall_events_pack(
                findall_id=args.findall_id,
                limit=args.limit,
                output_dir=args.output_dir,
            )
        elif args.command == "taskgroup-pack":
            task_inputs = _load_taskgroup_inputs(args.inputs, prompt_template=args.prompt_template)
            estimated_cost = estimate_taskgroup_cost(len(task_inputs), processor=args.processor)
            if args.dry_run:
                console.print_json(
                    data={
                        "workflow": "taskgroup-pack",
                        "input_count": len(task_inputs),
                        "processor": args.processor,
                        "estimated_cost_usd": estimated_cost,
                        "max_estimated_cost_usd": args.max_estimated_cost,
                    }
                )
                return 0
            if estimated_cost > args.max_estimated_cost:
                raise ParallelWorkflowError(
                    f"Estimated TaskGroup cost ${estimated_cost:.3f} exceeds "
                    f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                )
            pack = run_taskgroup_pack(
                task_inputs=task_inputs,
                processor=args.processor,
                output_dir=args.output_dir,
                wait=args.wait,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
                estimated_cost_usd=estimated_cost,
            )
        elif args.command == "monitor-pack":
            if args.monitor_command == "create":
                estimated_cost = estimate_monitor_execution_cost(processor=args.processor)
                source_policy = _build_source_policy(
                    include_domains=args.include_domains,
                    exclude_domains=args.exclude_domains,
                    after_date=args.after_date,
                )
                webhook = _build_webhook(args.webhook_url, args.webhook_event_types)
                metadata = _parse_metadata_pairs(args.metadata_pairs)
                output_schema = (
                    _load_json_file(args.output_schema, "output_schema") if args.output_schema else None
                )
                if args.dry_run:
                    console.print_json(
                        data={
                            "workflow": "monitor-pack",
                            "action": "create",
                            "type": args.type,
                            "query": args.query,
                            "task_run_id": args.task_run_id,
                            "frequency": args.frequency,
                            "processor": args.processor,
                            "source_policy": source_policy,
                            "location": args.location,
                            "include_backfill": args.include_backfill,
                            "has_output_schema": output_schema is not None,
                            "webhook": _jsonable(webhook),
                            "metadata": metadata,
                            "estimated_cost_per_execution_usd": estimated_cost,
                            "max_estimated_cost_usd": args.max_estimated_cost,
                        }
                    )
                    return 0
                if estimated_cost > args.max_estimated_cost:
                    raise ParallelWorkflowError(
                        f"Estimated Monitor execution cost ${estimated_cost:.3f} exceeds "
                        f"--max-estimated-cost ${args.max_estimated_cost:.3f}."
                    )
                pack = run_monitor_create_pack(
                    query=args.query,
                    monitor_type=args.type,
                    task_run_id=args.task_run_id,
                    frequency=args.frequency,
                    processor=args.processor,
                    output_schema=output_schema,
                    include_backfill=args.include_backfill,
                    source_policy=source_policy,
                    location=args.location,
                    webhook=webhook,
                    metadata=metadata,
                    output_dir=args.output_dir,
                    estimated_cost_usd=estimated_cost,
                )
            elif args.monitor_command == "events":
                pack = run_monitor_events_pack(
                    monitor_id=args.monitor_id,
                    limit=args.limit,
                    cursor=args.cursor,
                    event_group_id=args.event_group_id,
                    include_completions=args.include_completions,
                    output_dir=args.output_dir,
                )
            elif args.monitor_command == "list":
                pack = run_monitor_list_pack(
                    limit=args.limit,
                    cursor=args.cursor,
                    statuses=args.status,
                    monitor_types=args.type,
                    output_dir=args.output_dir,
                )
            elif args.monitor_command == "retrieve":
                pack = run_monitor_retrieve_pack(monitor_id=args.monitor_id, output_dir=args.output_dir)
            elif args.monitor_command == "update":
                source_policy = _build_source_policy(
                    include_domains=args.include_domains,
                    exclude_domains=args.exclude_domains,
                    after_date=args.after_date,
                )
                pack = run_monitor_update_pack(
                    monitor_id=args.monitor_id,
                    query=args.query,
                    frequency=args.frequency,
                    source_policy=source_policy,
                    location=args.location,
                    webhook=_build_webhook(args.webhook_url, args.webhook_event_types)
                    if args.webhook_url
                    else None,
                    clear_webhook=args.clear_webhook,
                    metadata=_parse_metadata_pairs(args.metadata_pairs),
                    clear_metadata=args.clear_metadata,
                    output_dir=args.output_dir,
                )
            elif args.monitor_command == "cancel":
                pack = run_monitor_cancel_pack(monitor_id=args.monitor_id, output_dir=args.output_dir)
            elif args.monitor_command == "trigger":
                pack = run_monitor_trigger_pack(monitor_id=args.monitor_id, output_dir=args.output_dir)
            else:  # pragma: no cover - argparse prevents this.
                parser.error(f"Unknown monitor command: {args.monitor_command}")
        elif args.command == "api-pack":
            pack = run_api_pack(source=args.source, kind=args.kind, output_dir=args.output_dir)
        elif args.command == "import":
            pack = import_context_pack(
                args.fixture,
                output_dir=args.output_dir,
                max_tokens_per_file=args.max_tokens_per_file,
            )
        elif args.command == "demo":
            pack = demo_context_pack(
                output_dir=args.output_dir,
                max_tokens_per_file=args.max_tokens_per_file,
            )
        elif args.command == "run":
            recipe_result = run_recipe(
                args.recipe,
                output_dir_override=args.output_dir,
                dry_run_override=args.dry_run if args.dry_run else None,
                max_estimated_cost_override=args.max_estimated_cost,
                console=console,
            )
            if recipe_result is None:
                return 0
            pack = recipe_result
        else:  # pragma: no cover - argparse prevents this.
            parser.error(f"Unknown command: {args.command}")

        if paid_command:
            maybe_write_run_accounting(
                Path(pack),
                budget_limit_usd=budget_limit,
                paid_capable=True,
                accounting=RunAccounting(
                    budget_limit_usd=budget_limit,
                    estimated_paid_cost_usd=float(locals().get("estimated_cost", 0.0) or 0.0),
                    paid_request_count=1,
                    route_steps=route_steps,
                    command=f"parallel {args.command}",
                ),
            )
        console.print(f"[green]Wrote Parallel context pack:[/green] {pack}")
        return 0
    except ParallelWorkflowError as err:
        console.print("[red]Parallel workflow error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Parallel workflow failed:[/red] " + escape(str(err)))
        return 1


def _parallel_command_paid_capable(args: argparse.Namespace) -> bool:
    """Return whether a parsed Parallel command may call live Parallel APIs."""
    command = getattr(args, "command", None)
    if command in {"api-pack", "import", "demo", "auth", "init"}:
        return False
    if command == "probe":
        return getattr(args, "mode", "safe") in {"validation", "smoke"}
    if command == "run":
        # Recipes may be local api-pack/import shapes, but V1 treats recipe
        # execution as paid-capable unless the user dry-runs without a budget.
        return True
    return True


def _parallel_output_dir_from_args(args: argparse.Namespace) -> Path:
    output_dir = getattr(args, "output_dir", None)
    if isinstance(output_dir, Path):
        return output_dir
    command = getattr(args, "command", "")
    if command == "api-pack":
        return Path("packs/parallel-api-pack")
    return DEFAULT_OUTPUT_DIR


def run_search_pack(
    *,
    objective: str,
    queries: list[str],
    mode: str,
    output_dir: Path,
    source_policy: dict[str, Any] | None,
    fetch_policy: dict[str, Any] | None,
    max_search_results: int | None,
    max_search_chars_total: int | None,
    excerpt_chars_per_result: int | None,
    location: str | None,
    client_model: str | None,
    estimated_cost_usd: float,
) -> Path:
    """Run live Parallel Search and write ranked result excerpts as a pack."""
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    session_id = f"docpull_{uuid.uuid4().hex}"
    search_kwargs = _build_search_kwargs(
        objective=objective,
        queries=queries,
        mode=mode,
        session_id=session_id,
        source_policy=source_policy,
        fetch_policy=fetch_policy,
        max_search_results=max_search_results,
        max_search_chars_total=max_search_chars_total,
        excerpt_chars_per_result=excerpt_chars_per_result,
        location=location,
        client_model=client_model,
    )
    search = client.search(**search_kwargs)
    results = [_jsonable_dict(item) for item in _list(_get(search, "results"))]
    metadata = {
        "provider": "parallel",
        "workflow": "search-pack",
        "objective": objective,
        "queries": queries,
        "mode": mode,
        "search_id": _get(search, "search_id"),
        "session_id": _get(search, "session_id", session_id),
        "request_options": _build_request_options(
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=excerpt_chars_per_result,
            location=location,
            max_search_results=max_search_results,
            max_search_chars_total=max_search_chars_total,
            max_extract_chars_total=None,
            max_full_content_chars=None,
            client_model=client_model,
            full_content=False,
        ),
        "warnings": _jsonable(_get(search, "warnings")),
        "usage": _jsonable(_get(search, "usage")),
        "estimated_cost_usd": estimated_cost_usd,
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="search-pack",
        pack_filename="search.pack.json",
        objective=objective,
        items=results,
        metadata=metadata,
        source_type="parallel_search_result",
    )


def run_discover_docs_pack(
    *,
    objective: str,
    queries: list[str],
    mode: str,
    output_dir: Path,
    source_policy: dict[str, Any] | None,
    fetch_policy: dict[str, Any] | None,
    max_search_results: int | None,
    max_search_chars_total: int | None,
    excerpt_chars_per_result: int | None,
    location: str | None,
    client_model: str | None,
    crawl_profile: str,
    estimated_cost_usd: float,
) -> Path:
    """Use Parallel Search to seed core docpull crawl targets."""
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    session_id = f"docpull_{uuid.uuid4().hex}"
    search_kwargs = _build_search_kwargs(
        objective=objective,
        queries=queries,
        mode=mode,
        session_id=session_id,
        source_policy=source_policy,
        fetch_policy=fetch_policy,
        max_search_results=max_search_results,
        max_search_chars_total=max_search_chars_total,
        excerpt_chars_per_result=excerpt_chars_per_result,
        location=location,
        client_model=client_model,
    )
    search = client.search(**search_kwargs)
    results = [_jsonable_dict(item) for item in _list(_get(search, "results"))]
    expected_domains = _expected_domains_from_request_options(
        _build_request_options(
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=excerpt_chars_per_result,
            location=location,
            max_search_results=max_search_results,
            max_search_chars_total=max_search_chars_total,
            max_extract_chars_total=None,
            max_full_content_chars=None,
            client_model=client_model,
            full_content=False,
        )
    )
    discovery_items = _discovery_items(
        results,
        expected_domains=expected_domains,
        crawl_profile=crawl_profile,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    discovered_urls_path = output_dir / "discovered_urls.json"
    next_steps_path = output_dir / "NEXT_STEPS.md"
    discovered_urls_path.write_text(
        json.dumps(
            {
                "objective": objective,
                "queries": queries,
                "crawl_profile": crawl_profile,
                "sources": discovery_items,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    next_steps_path.write_text(
        _discovery_next_steps_md(objective, discovery_items, crawl_profile=crawl_profile),
        encoding="utf-8",
    )
    metadata = {
        "provider": "parallel",
        "workflow": "discover-docs",
        "objective": objective,
        "queries": queries,
        "mode": mode,
        "search_id": _get(search, "search_id"),
        "session_id": _get(search, "session_id", session_id),
        "request_options": _build_request_options(
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=excerpt_chars_per_result,
            location=location,
            max_search_results=max_search_results,
            max_search_chars_total=max_search_chars_total,
            max_extract_chars_total=None,
            max_full_content_chars=None,
            client_model=client_model,
            full_content=False,
        ),
        "warnings": _jsonable(_get(search, "warnings")),
        "usage": _jsonable(_get(search, "usage")),
        "estimated_cost_usd": estimated_cost_usd,
        "crawl_profile": crawl_profile,
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="discover-docs",
        pack_filename="discovery.pack.json",
        objective=objective,
        items=discovery_items,
        metadata=metadata,
        source_type="parallel_discovered_source",
        extra_artifacts={
            "discovered_urls": _relative_path(discovered_urls_path, output_dir),
            "next_steps": _relative_path(next_steps_path, output_dir),
        },
    )


def run_extract_pack(
    *,
    urls: list[str],
    objective: str,
    queries: list[str],
    output_dir: Path,
    max_tokens_per_file: int,
    max_extract_chars_total: int | None,
    max_full_content_chars: int | None,
    fetch_policy: dict[str, Any] | None,
    excerpt_chars_per_result: int | None,
    client_model: str | None,
    full_content: bool,
    session_id: str | None,
    estimated_cost_usd: float,
) -> Path:
    """Run live Parallel Extract for known URLs and write a context pack."""
    urls = _validate_extract_urls(urls)
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    request_session_id = session_id or f"docpull_{uuid.uuid4().hex}"
    extract_kwargs: dict[str, Any] = {
        "urls": urls,
        "objective": objective,
        "session_id": request_session_id,
        "advanced_settings": _build_extract_advanced_settings(
            full_content=full_content,
            max_full_content_chars=max_full_content_chars,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=excerpt_chars_per_result,
        ),
    }
    if queries:
        extract_kwargs["search_queries"] = queries
    if client_model:
        extract_kwargs["client_model"] = client_model
    if max_extract_chars_total:
        extract_kwargs["max_chars_total"] = max_extract_chars_total
    extract = client.extract(**extract_kwargs)
    pack = ParallelContextPack(
        objective=objective,
        queries=queries,
        workflow="extract-pack",
        session_id=_get(extract, "session_id", request_session_id),
        extract_id=_get(extract, "extract_id"),
        extract_results=[_jsonable(item) for item in _list(_get(extract, "results", []))],
        extract_errors=[_jsonable(item) for item in _list(_get(extract, "errors", []))],
        request_options=_build_request_options(
            source_policy=None,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=excerpt_chars_per_result,
            location=None,
            max_search_results=None,
            max_search_chars_total=None,
            max_extract_chars_total=max_extract_chars_total,
            max_full_content_chars=max_full_content_chars,
            client_model=client_model,
            full_content=full_content,
        ),
        warnings={"extract": _jsonable(_list(_get(extract, "warnings")))},
        usage={"extract": _jsonable(_get(extract, "usage"))},
        estimated_cost_usd=estimated_cost_usd,
    )
    return write_context_pack(pack, output_dir=output_dir, max_tokens_per_file=max_tokens_per_file)


def run_fallback_pack(
    *,
    urls: list[str],
    objective: str,
    queries: list[str],
    output_dir: Path,
    profile: str,
    max_tokens_per_file: int,
    max_core_chars: int,
    max_extract_chars_total: int | None,
    max_full_content_chars: int | None,
    fetch_policy: dict[str, Any] | None,
    excerpt_chars_per_result: int | None,
    client_model: str | None,
    full_content: bool,
    session_id: str | None,
    estimated_cost_usd: float,
) -> Path:
    """Run core docpull first and use Parallel Extract only for misses."""
    urls = _validate_extract_urls(urls)
    request_session_id = session_id or f"docpull_{uuid.uuid4().hex}"
    core_results: list[dict[str, Any]] = []
    core_failures: list[dict[str, Any]] = []

    for url in urls:
        try:
            core_results.append(
                _core_docpull_extract_result(
                    url,
                    profile=profile,
                    max_core_chars=max_core_chars,
                )
            )
        except Exception as err:  # noqa: BLE001 - each URL can fall back independently.
            core_failures.append(
                {
                    "url": url,
                    "error_type": "core_fetch_error",
                    "content": str(err),
                }
            )

    fallback_urls = [failure["url"] for failure in core_failures]
    parallel_results: list[dict[str, Any]] = []
    parallel_errors: list[dict[str, Any]] = []
    parallel_warnings: Any = None
    parallel_usage: Any = None
    extract_id: Any = None
    if fallback_urls:
        api_key = _require_api_key()
        parallel_client_class = _require_parallel_sdk()
        client = parallel_client_class(api_key=api_key)
        extract_kwargs: dict[str, Any] = {
            "urls": fallback_urls,
            "objective": objective,
            "session_id": request_session_id,
            "advanced_settings": _build_extract_advanced_settings(
                full_content=full_content,
                max_full_content_chars=max_full_content_chars,
                fetch_policy=fetch_policy,
                excerpt_chars_per_result=excerpt_chars_per_result,
            ),
        }
        if queries:
            extract_kwargs["search_queries"] = queries
        if client_model:
            extract_kwargs["client_model"] = client_model
        if max_extract_chars_total:
            extract_kwargs["max_chars_total"] = max_extract_chars_total
        extract = client.extract(**extract_kwargs)
        extract_id = _get(extract, "extract_id")
        parallel_results = [
            _mark_parallel_fallback_result(_jsonable_dict(item))
            for item in _list(_get(extract, "results", []))
        ]
        parallel_errors = [_jsonable_dict(item) for item in _list(_get(extract, "errors", []))]
        parallel_warnings = _jsonable(_list(_get(extract, "warnings")))
        parallel_usage = _jsonable(_get(extract, "usage"))
        request_session_id = _coerce_str(_get(extract, "session_id")) or request_session_id

    request_options = _build_request_options(
        source_policy=None,
        fetch_policy=fetch_policy,
        excerpt_chars_per_result=excerpt_chars_per_result,
        location=None,
        max_search_results=None,
        max_search_chars_total=None,
        max_extract_chars_total=max_extract_chars_total,
        max_full_content_chars=max_full_content_chars,
        client_model=client_model,
        full_content=full_content,
    )
    request_options.update(
        {
            "core_profile": profile,
            "max_core_chars": max_core_chars,
            "core_success_count": len(core_results),
            "fallback_url_count": len(fallback_urls),
        }
    )
    pack = ParallelContextPack(
        objective=objective,
        queries=queries,
        workflow="fallback-pack",
        session_id=request_session_id,
        extract_id=extract_id,
        extract_results=core_results + parallel_results,
        extract_errors=parallel_errors,
        request_options=request_options,
        warnings={
            "core_fetch_failures": core_failures,
            "fallback_extract": parallel_warnings,
        },
        usage={"extract": parallel_usage},
        estimated_cost_usd=estimated_cost_usd if fallback_urls else 0.0,
    )
    return write_context_pack(pack, output_dir=output_dir, max_tokens_per_file=max_tokens_per_file)


def run_task_pack(
    *,
    task_input: Any,
    processor: str,
    output_dir: Path,
    output_schema: dict[str, Any] | None,
    input_schema: dict[str, Any] | None,
    source_policy: dict[str, Any] | None,
    location: str | None,
    previous_interaction_id: str | None,
    enable_events: bool,
    mcp_servers: list[dict[str, Any]],
    webhook: dict[str, Any] | None,
    metadata: dict[str, Any],
    api_timeout: int,
    estimated_cost_usd: float,
) -> Path:
    """Create one Parallel Task run, fetch its result, and write a pack."""
    processor = _validate_task_processor(processor)
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    task_kwargs = _clean_dict(
        {
            "input": task_input,
            "processor": processor,
            "metadata": metadata or None,
            "source_policy": source_policy,
            "advanced_settings": _task_advanced_settings(location),
            "task_spec": _task_spec(output_schema=output_schema, input_schema=input_schema),
            "previous_interaction_id": previous_interaction_id,
            "mcp_servers": mcp_servers or None,
            "enable_events": True if enable_events else None,
            "webhook": webhook,
        }
    )
    task_run = client.task_run.create(**task_kwargs)
    run_id = _coerce_str(_get(task_run, "run_id")) or _coerce_str(_get(task_run, "id"))
    result = client.task_run.result(run_id, api_timeout=api_timeout) if run_id else None
    items = [
        _clean_dict(
            {
                "name": run_id or "task_run",
                "run_id": run_id,
                "task_run": _jsonable(task_run),
                "result": _jsonable(result),
                "output": _jsonable(_get(result, "output")),
            }
        )
    ]
    return write_structured_pack(
        output_dir=output_dir,
        workflow="task-pack",
        pack_filename="task.pack.json",
        objective=_task_objective(task_input),
        items=items,
        metadata={
            "provider": "parallel",
            "workflow": "task-pack",
            "run_id": run_id,
            "processor": processor,
            "request": _redact_sensitive_headers(task_kwargs),
            "task_run": _jsonable(task_run),
            "usage": _jsonable(_get(result, "usage")),
            "basis": _jsonable(_list(_get(_get(result, "output"), "basis"))) if result else [],
            "estimated_cost_usd": estimated_cost_usd,
        },
        source_type="parallel_task_run",
    )


def run_task_result_pack(*, run_id: str, output_dir: Path, api_timeout: int) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    result = client.task_run.result(run_id, api_timeout=api_timeout)
    item = {
        "name": run_id,
        "run_id": run_id,
        "result": _jsonable(result),
        "output": _jsonable(_get(result, "output")),
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="task-result-pack",
        pack_filename="task.result.pack.json",
        objective=f"Parallel Task result {run_id}",
        items=[item],
        metadata={"provider": "parallel", "workflow": "task-result-pack", "run_id": run_id},
        source_type="parallel_task_result",
    )


def run_task_events_pack(
    *,
    run_id: str,
    last_event_id: str | None,
    timeout: int | None,
    limit: int,
    output_dir: Path,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    events_iter = client.task_run.events(
        run_id,
        **_clean_dict({"last_event_id": last_event_id, "timeout": timeout}),
    )
    events = _collect_iterable(events_iter, limit=limit)
    return write_structured_pack(
        output_dir=output_dir,
        workflow="task-events-pack",
        pack_filename="task.events.pack.json",
        objective=f"Parallel Task events {run_id}",
        items=events,
        metadata={
            "provider": "parallel",
            "workflow": "task-events-pack",
            "run_id": run_id,
            "last_event_id": last_event_id,
            "timeout": timeout,
            "limit": limit,
            "event_count": len(events),
        },
        source_type="parallel_task_event",
    )


def run_diff_brief_pack(
    *,
    old_pack_dir: Path,
    new_pack_dir: Path,
    output_dir: Path,
    processor: str,
    api_timeout: int,
    estimated_cost_usd: float,
) -> Path:
    """Diff two local packs and summarize the changes with Parallel Task."""
    from .pack_tools import diff_packs

    processor = _validate_task_processor(processor)
    diff = diff_packs(old_pack_dir, new_pack_dir)
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    task_input = _diff_brief_prompt(diff)
    task_kwargs = {
        "input": task_input,
        "processor": processor,
        "metadata": {
            "workflow": "diff-brief",
            "old_pack_dir": diff["old_pack_dir"],
            "new_pack_dir": diff["new_pack_dir"],
        },
        "task_spec": {"output_schema": {"type": "text"}},
    }
    task_run = client.task_run.create(**task_kwargs)
    run_id = _coerce_str(_get(task_run, "run_id")) or _coerce_str(_get(task_run, "id"))
    result = client.task_run.result(run_id, api_timeout=api_timeout) if run_id else None
    brief = _extract_task_content(result) or _compact_json(_jsonable(result))

    output_dir.mkdir(parents=True, exist_ok=True)
    diff_path = output_dir / "pack.diff.json"
    brief_path = output_dir / "CHANGE_SUMMARY.md"
    diff_path.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    brief_path.write_text(brief.strip() + "\n", encoding="utf-8")

    items = [
        {
            "name": "Context pack diff brief",
            "title": "Context pack diff brief",
            "url": f"parallel://diff-brief/{run_id or 'run'}",
            "content": brief,
            "output": {"content": brief},
            "diff": diff,
            "task_run": _jsonable(task_run),
            "result": _jsonable(result),
        }
    ]
    return write_structured_pack(
        output_dir=output_dir,
        workflow="diff-brief",
        pack_filename="diff.brief.pack.json",
        objective="Summarize context pack changes for agent review",
        items=items,
        metadata={
            "provider": "parallel",
            "workflow": "diff-brief",
            "run_id": run_id,
            "processor": processor,
            "old_pack_dir": diff["old_pack_dir"],
            "new_pack_dir": diff["new_pack_dir"],
            "diff_counts": {
                "added": len(diff["added_urls"]),
                "removed": len(diff["removed_urls"]),
                "changed": len(diff["changed_urls"]),
                "unchanged": len(diff["unchanged_urls"]),
            },
            "request": _redact_sensitive_headers(task_kwargs),
            "task_run": _jsonable(task_run),
            "usage": _jsonable(_get(result, "usage")),
            "basis": _jsonable(_list(_get(_get(result, "output"), "basis"))) if result else [],
            "estimated_cost_usd": estimated_cost_usd,
        },
        source_type="parallel_diff_brief",
        extra_artifacts={
            "pack_diff": _relative_path(diff_path, output_dir),
            "change_summary": _relative_path(brief_path, output_dir),
        },
    )


def run_live_context_pack(
    *,
    objective: str,
    queries: list[str],
    output_dir: Path,
    mode: str = DEFAULT_MODE,
    extract_limit: int = DEFAULT_EXTRACT_LIMIT,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS,
    source_policy: dict[str, Any] | None = None,
    max_search_results: int | None = None,
    max_search_chars_total: int | None = None,
    max_extract_chars_total: int | None = None,
    max_full_content_chars: int | None = DEFAULT_MAX_FULL_CONTENT_CHARS,
    fetch_policy: dict[str, Any] | None = None,
    excerpt_chars_per_result: int | None = None,
    location: str | None = None,
    client_model: str | None = None,
    full_content: bool = True,
    task_brief: bool = False,
    task_processor: str = DEFAULT_TASK_PROCESSOR,
    estimated_cost_usd: float | None = None,
) -> Path:
    """Run live Parallel Search/Extract/Task calls and write a context pack."""
    if task_brief:
        task_processor = _validate_task_processor(task_processor)
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    session_id = f"docpull_{uuid.uuid4().hex}"

    request_options = _build_request_options(
        source_policy=source_policy,
        fetch_policy=fetch_policy,
        excerpt_chars_per_result=excerpt_chars_per_result,
        location=location,
        max_search_results=max_search_results,
        max_search_chars_total=max_search_chars_total,
        max_extract_chars_total=max_extract_chars_total,
        max_full_content_chars=max_full_content_chars,
        client_model=client_model,
        full_content=full_content,
    )

    search_kwargs: dict[str, Any] = {
        "objective": objective,
        "search_queries": queries,
        "mode": mode,
        "session_id": session_id,
    }
    if client_model:
        search_kwargs["client_model"] = client_model
    if max_search_chars_total:
        search_kwargs["max_chars_total"] = max_search_chars_total
    search_advanced_settings = _clean_dict(
        {
            "source_policy": source_policy,
            "fetch_policy": fetch_policy,
            "excerpt_settings": _excerpt_settings(excerpt_chars_per_result),
            "location": location,
            "max_results": max_search_results,
        }
    )
    if search_advanced_settings:
        search_kwargs["advanced_settings"] = search_advanced_settings

    search = client.search(**search_kwargs)
    search_results = _list(_get(search, "results", []))
    selected_urls = _select_urls(search_results, extract_limit)
    if not selected_urls:
        raise ParallelWorkflowError("Parallel Search returned no URLs to extract.")
    selected_urls = _validated_https_urls(selected_urls)
    if not selected_urls:
        raise ParallelWorkflowError("Parallel Search returned no valid HTTPS URLs to extract.")

    extract_kwargs: dict[str, Any] = {
        "urls": selected_urls,
        "objective": objective,
        "search_queries": queries,
        "session_id": _get(search, "session_id", session_id),
        "advanced_settings": _build_extract_advanced_settings(
            full_content=full_content,
            max_full_content_chars=max_full_content_chars,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=excerpt_chars_per_result,
        ),
    }
    if client_model:
        extract_kwargs["client_model"] = client_model
    if max_extract_chars_total:
        extract_kwargs["max_chars_total"] = max_extract_chars_total

    extract = client.extract(**extract_kwargs)

    task_result = None
    task_run_id = None
    task_content = None
    if task_brief:
        task_input = (
            "Create a concise Markdown research brief for this objective. "
            "Include inline citations where available.\n\n"
            f"Objective: {objective}\n\nPriority sources:\n" + "\n".join(f"- {url}" for url in selected_urls)
        )
        task_run = client.task_run.create(
            input=task_input,
            processor=task_processor,
            task_spec=_task_text_spec(),
        )
        task_run_id = _get(task_run, "run_id")
        task_result = client.task_run.result(task_run_id, api_timeout=3600)
        task_content = _extract_task_content(task_result)

    pack = ParallelContextPack(
        objective=objective,
        queries=queries,
        mode=mode,
        session_id=_get(extract, "session_id", _get(search, "session_id", session_id)),
        search_id=_get(search, "search_id"),
        extract_id=_get(extract, "extract_id"),
        task_run_id=task_run_id,
        search_results=[_jsonable(item) for item in search_results],
        extract_results=[_jsonable(item) for item in _list(_get(extract, "results", []))],
        extract_errors=[_jsonable(item) for item in _list(_get(extract, "errors", []))],
        task_brief=task_content,
        task_basis=_jsonable(_list(_get(_get(task_result, "output"), "basis"))) if task_result else [],
        request_options=request_options,
        warnings={
            "search": _jsonable(_list(_get(search, "warnings"))),
            "extract": _jsonable(_list(_get(extract, "warnings"))),
        },
        usage={
            "search": _jsonable(_get(search, "usage")),
            "extract": _jsonable(_get(extract, "usage")),
            "task": _jsonable(_get(task_result, "usage")) if task_result is not None else None,
        },
        estimated_cost_usd=estimated_cost_usd,
    )
    return write_context_pack(pack, output_dir=output_dir, max_tokens_per_file=max_tokens_per_file)


def run_recipe(
    recipe_path: Path,
    *,
    output_dir_override: Path | None = None,
    dry_run_override: bool | None = None,
    max_estimated_cost_override: float | None = None,
    console: Console | None = None,
) -> Path | None:
    """Run a YAML/JSON Parallel workflow recipe."""
    recipe = _load_recipe(recipe_path)
    workflow = _coerce_str(recipe.get("workflow")) or "context-pack"
    if workflow != "context-pack":
        return _run_non_context_recipe(
            workflow,
            recipe,
            recipe_path=recipe_path,
            output_dir_override=output_dir_override,
            dry_run_override=dry_run_override,
            max_estimated_cost_override=max_estimated_cost_override,
            console=console,
        )

    objective = _coerce_str(recipe.get("objective"))
    if not objective:
        raise ParallelWorkflowError("Recipe is missing required string field: objective.")
    queries = recipe.get("queries") or recipe.get("search_queries") or []
    if not isinstance(queries, list) or not all(isinstance(item, str) and item for item in queries):
        raise ParallelWorkflowError("Recipe field 'queries' must be a list of non-empty strings.")

    output_dir = _recipe_output_dir(recipe, DEFAULT_OUTPUT_DIR, output_dir_override)
    source_policy_recipe = recipe.get("source_policy") or {}
    if not isinstance(source_policy_recipe, dict):
        raise ParallelWorkflowError("Recipe field 'source_policy' must be an object when present.")
    source_policy = _build_source_policy(
        include_domains=_string_list(
            recipe.get("include_domains") or source_policy_recipe.get("include_domains")
        ),
        exclude_domains=_string_list(
            recipe.get("exclude_domains") or source_policy_recipe.get("exclude_domains")
        ),
        after_date=_coerce_str(recipe.get("after_date") or source_policy_recipe.get("after_date")),
    )
    fetch_policy_recipe = recipe.get("fetch_policy") or {}
    if not isinstance(fetch_policy_recipe, dict):
        raise ParallelWorkflowError("Recipe field 'fetch_policy' must be an object when present.")
    fetch_policy = _build_fetch_policy(
        max_age_seconds=_optional_int(
            recipe.get("fetch_max_age_seconds") or fetch_policy_recipe.get("max_age_seconds"),
            "fetch_max_age_seconds",
            min_value=600,
        ),
        timeout_seconds=_optional_int(
            recipe.get("fetch_timeout_seconds") or fetch_policy_recipe.get("timeout_seconds"),
            "fetch_timeout_seconds",
        ),
        disable_cache_fallback=bool(
            recipe.get("disable_cache_fallback") or fetch_policy_recipe.get("disable_cache_fallback", False)
        ),
    )

    extract_limit = _required_positive_int(
        recipe.get("extract_limit"),
        "extract_limit",
        default=DEFAULT_EXTRACT_LIMIT,
        max_value=MAX_EXTRACT_URLS_PER_REQUEST,
    )
    max_search_results = _optional_int(recipe.get("max_search_results"))
    task_brief = bool(recipe.get("task_brief", False))
    task_processor = _validate_task_processor(
        _coerce_str(recipe.get("task_processor")) or DEFAULT_TASK_PROCESSOR
    )
    estimated_cost = estimate_context_pack_cost(
        extract_limit=extract_limit,
        max_search_results=max_search_results,
        task_brief=task_brief,
        task_processor=task_processor,
    )
    max_estimated_cost = (
        max_estimated_cost_override
        if max_estimated_cost_override is not None
        else float(recipe.get("max_estimated_cost", DEFAULT_MAX_ESTIMATED_COST_USD))
    )
    dry_run = dry_run_override if dry_run_override is not None else bool(recipe.get("dry_run", False))
    request_options = _build_request_options(
        source_policy=source_policy,
        fetch_policy=fetch_policy,
        excerpt_chars_per_result=_optional_int(
            recipe.get("excerpt_chars_per_result"),
            "excerpt_chars_per_result",
        ),
        location=_coerce_str(recipe.get("location")),
        max_search_results=max_search_results,
        max_search_chars_total=_optional_int(recipe.get("max_search_chars_total")),
        max_extract_chars_total=_optional_int(recipe.get("max_extract_chars_total")),
        max_full_content_chars=None
        if bool(recipe.get("no_full_content", False))
        else _optional_int(recipe.get("max_full_content_chars")) or DEFAULT_MAX_FULL_CONTENT_CHARS,
        client_model=_coerce_str(recipe.get("client_model")),
        full_content=not bool(recipe.get("no_full_content", False)),
    )
    if dry_run:
        (console or Console()).print_json(
            data={
                "recipe": str(recipe_path),
                "workflow": workflow,
                "objective": objective,
                "queries": queries,
                "mode": _coerce_str(recipe.get("mode")) or DEFAULT_MODE,
                "extract_limit": extract_limit,
                "request_options": request_options,
                "estimated_cost_usd": estimated_cost,
                "max_estimated_cost_usd": max_estimated_cost,
            }
        )
        return None
    if estimated_cost > max_estimated_cost:
        raise ParallelWorkflowError(
            "Estimated Parallel cost "
            f"${estimated_cost:.3f} exceeds recipe cost guard ${max_estimated_cost:.3f}."
        )
    return run_live_context_pack(
        objective=objective,
        queries=queries,
        output_dir=output_dir,
        mode=_coerce_str(recipe.get("mode")) or DEFAULT_MODE,
        extract_limit=extract_limit,
        max_tokens_per_file=_optional_int(recipe.get("max_tokens_per_file")) or DEFAULT_MAX_TOKENS,
        source_policy=source_policy,
        max_search_results=max_search_results,
        max_search_chars_total=_optional_int(recipe.get("max_search_chars_total")),
        max_extract_chars_total=_optional_int(recipe.get("max_extract_chars_total")),
        max_full_content_chars=None
        if bool(recipe.get("no_full_content", False))
        else _optional_int(recipe.get("max_full_content_chars")) or DEFAULT_MAX_FULL_CONTENT_CHARS,
        fetch_policy=fetch_policy,
        excerpt_chars_per_result=_optional_int(
            recipe.get("excerpt_chars_per_result"),
            "excerpt_chars_per_result",
        ),
        location=_coerce_str(recipe.get("location")),
        client_model=_coerce_str(recipe.get("client_model")),
        full_content=not bool(recipe.get("no_full_content", False)),
        task_brief=task_brief,
        task_processor=task_processor,
        estimated_cost_usd=estimated_cost,
    )


def _run_non_context_recipe(
    workflow: str,
    recipe: dict[str, Any],
    *,
    recipe_path: Path,
    output_dir_override: Path | None,
    dry_run_override: bool | None,
    max_estimated_cost_override: float | None,
    console: Console | None,
) -> Path | None:
    dry_run = dry_run_override if dry_run_override is not None else bool(recipe.get("dry_run", False))
    max_estimated_cost = (
        max_estimated_cost_override
        if max_estimated_cost_override is not None
        else float(recipe.get("max_estimated_cost", DEFAULT_MAX_ESTIMATED_COST_USD))
    )
    printer = console or Console()

    if workflow == "search-pack":
        objective = _required_recipe_str(recipe, "objective")
        queries = _recipe_queries(recipe, objective)
        source_policy = _recipe_source_policy(recipe)
        fetch_policy = _recipe_fetch_policy(recipe)
        max_search_results = _optional_int(recipe.get("max_search_results"))
        estimated_cost = estimate_search_pack_cost(max_search_results=max_search_results)
        request_options = _build_request_options(
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=_optional_int(recipe.get("excerpt_chars_per_result")),
            location=_coerce_str(recipe.get("location")),
            max_search_results=max_search_results,
            max_search_chars_total=_optional_int(recipe.get("max_search_chars_total")),
            max_extract_chars_total=None,
            max_full_content_chars=None,
            client_model=_coerce_str(recipe.get("client_model")),
            full_content=False,
        )
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "objective": objective,
                    "queries": queries,
                    "mode": _coerce_str(recipe.get("mode")) or DEFAULT_MODE,
                    "request_options": request_options,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "Search")
        return run_search_pack(
            objective=objective,
            queries=queries,
            mode=_coerce_str(recipe.get("mode")) or DEFAULT_MODE,
            output_dir=_recipe_output_dir(recipe, DEFAULT_SEARCH_PACK_OUTPUT_DIR, output_dir_override),
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            max_search_results=max_search_results,
            max_search_chars_total=_optional_int(recipe.get("max_search_chars_total")),
            excerpt_chars_per_result=_optional_int(recipe.get("excerpt_chars_per_result")),
            location=_coerce_str(recipe.get("location")),
            client_model=_coerce_str(recipe.get("client_model")),
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "discover-docs":
        objective = _required_recipe_str(recipe, "objective")
        queries = _recipe_queries(recipe, objective)
        source_policy = _recipe_source_policy(recipe)
        fetch_policy = _recipe_fetch_policy(recipe)
        max_search_results = _optional_int(recipe.get("max_search_results"))
        crawl_profile = _coerce_str(recipe.get("crawl_profile")) or "mirror"
        if crawl_profile not in {"rag", "mirror", "quick", "llm"}:
            raise ParallelWorkflowError("Recipe field 'crawl_profile' must be rag, mirror, quick, or llm.")
        estimated_cost = estimate_search_pack_cost(max_search_results=max_search_results)
        request_options = _build_request_options(
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=_optional_int(recipe.get("excerpt_chars_per_result")),
            location=_coerce_str(recipe.get("location")),
            max_search_results=max_search_results,
            max_search_chars_total=_optional_int(recipe.get("max_search_chars_total")),
            max_extract_chars_total=None,
            max_full_content_chars=None,
            client_model=_coerce_str(recipe.get("client_model")),
            full_content=False,
        )
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "objective": objective,
                    "queries": queries,
                    "mode": _coerce_str(recipe.get("mode")) or DEFAULT_MODE,
                    "crawl_profile": crawl_profile,
                    "request_options": request_options,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "discovery")
        return run_discover_docs_pack(
            objective=objective,
            queries=queries,
            mode=_coerce_str(recipe.get("mode")) or DEFAULT_MODE,
            output_dir=_recipe_output_dir(recipe, DEFAULT_DISCOVERY_PACK_OUTPUT_DIR, output_dir_override),
            source_policy=source_policy,
            fetch_policy=fetch_policy,
            max_search_results=max_search_results,
            max_search_chars_total=_optional_int(recipe.get("max_search_chars_total")),
            excerpt_chars_per_result=_optional_int(recipe.get("excerpt_chars_per_result")),
            location=_coerce_str(recipe.get("location")),
            client_model=_coerce_str(recipe.get("client_model")),
            crawl_profile=crawl_profile,
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "extract-pack":
        urls = _load_extract_urls(
            _string_list(recipe.get("urls") or []),
            _resolve_recipe_path(recipe_path, recipe.get("url_file")),
        )
        fetch_policy = _recipe_fetch_policy(recipe)
        estimated_cost = estimate_extract_pack_cost(url_count=len(urls))
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "urls": urls,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "Extract")
        return run_extract_pack(
            urls=urls,
            objective=_coerce_str(recipe.get("objective")) or "Extract known URLs into an agent context pack",
            queries=_string_list(recipe.get("queries") or recipe.get("search_queries") or []),
            output_dir=_recipe_output_dir(recipe, DEFAULT_EXTRACT_PACK_OUTPUT_DIR, output_dir_override),
            max_tokens_per_file=_optional_int(recipe.get("max_tokens_per_file")) or DEFAULT_MAX_TOKENS,
            max_extract_chars_total=_optional_int(recipe.get("max_extract_chars_total")),
            max_full_content_chars=None
            if bool(recipe.get("no_full_content", False))
            else _optional_int(recipe.get("max_full_content_chars")) or DEFAULT_MAX_FULL_CONTENT_CHARS,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=_optional_int(recipe.get("excerpt_chars_per_result")),
            client_model=_coerce_str(recipe.get("client_model")),
            full_content=not bool(recipe.get("no_full_content", False)),
            session_id=_coerce_str(recipe.get("session_id")),
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "fallback-pack":
        urls = _load_extract_urls(
            _string_list(recipe.get("urls") or []),
            _resolve_recipe_path(recipe_path, recipe.get("url_file")),
        )
        fetch_policy = _recipe_fetch_policy(recipe)
        profile = _coerce_str(recipe.get("profile")) or "rag"
        if profile not in {"rag", "mirror", "quick", "llm"}:
            raise ParallelWorkflowError("Recipe field 'profile' must be rag, mirror, quick, or llm.")
        estimated_cost = estimate_extract_pack_cost(url_count=len(urls))
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "urls": urls,
                    "profile": profile,
                    "estimated_worst_case_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "fallback Extract")
        objective = _coerce_str(recipe.get("objective")) or "Extract URLs with docpull and Parallel fallback"
        return run_fallback_pack(
            urls=urls,
            objective=objective,
            queries=_string_list(recipe.get("queries") or recipe.get("search_queries") or []),
            output_dir=_recipe_output_dir(recipe, DEFAULT_FALLBACK_PACK_OUTPUT_DIR, output_dir_override),
            profile=profile,
            max_tokens_per_file=_optional_int(recipe.get("max_tokens_per_file")) or DEFAULT_MAX_TOKENS,
            max_core_chars=_optional_int(recipe.get("max_core_chars")) or DEFAULT_MAX_FULL_CONTENT_CHARS,
            max_extract_chars_total=_optional_int(recipe.get("max_extract_chars_total")),
            max_full_content_chars=None
            if bool(recipe.get("no_full_content", False))
            else _optional_int(recipe.get("max_full_content_chars")) or DEFAULT_MAX_FULL_CONTENT_CHARS,
            fetch_policy=fetch_policy,
            excerpt_chars_per_result=_optional_int(recipe.get("excerpt_chars_per_result")),
            client_model=_coerce_str(recipe.get("client_model")),
            full_content=not bool(recipe.get("no_full_content", False)),
            session_id=_coerce_str(recipe.get("session_id")),
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "entity-pack":
        objective = _required_recipe_str(recipe, "objective")
        match_limit = _required_positive_int(recipe.get("match_limit"), "match_limit", default=25)
        estimated_cost = estimate_entity_search_cost(match_limit=match_limit)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "objective": objective,
                    "entity_type": _coerce_str(recipe.get("entity_type")) or "companies",
                    "match_limit": match_limit,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "Entity Search")
        return run_entity_pack(
            objective=objective,
            entity_type=_coerce_str(recipe.get("entity_type")) or "companies",
            match_limit=match_limit,
            output_dir=_recipe_output_dir(recipe, Path("packs/parallel-entity-pack"), output_dir_override),
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "findall-pack":
        objective = _required_recipe_str(recipe, "objective")
        match_limit = _required_positive_int(recipe.get("match_limit"), "match_limit", default=5)
        generator = _coerce_str(recipe.get("generator")) or "preview"
        if generator not in FINDALL_GENERATOR_COST_USD:
            raise ParallelWorkflowError("Recipe field 'generator' must be one of: preview, base, core, pro.")
        estimated_cost = estimate_findall_cost(generator=generator, match_limit=match_limit)
        conditions = _parse_match_conditions(_string_list(recipe.get("conditions") or []), objective)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "objective": objective,
                    "generator": generator,
                    "match_limit": match_limit,
                    "match_conditions": conditions,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "FindAll")
        return run_findall_pack(
            objective=objective,
            entity_type=_coerce_str(recipe.get("entity_type")) or "companies",
            match_conditions=conditions,
            generator=generator,
            match_limit=match_limit,
            exclude_list=_parse_exclude_candidates(_string_list(recipe.get("exclude_candidates") or [])),
            metadata=_recipe_metadata(recipe),
            webhook=_recipe_webhook(recipe),
            output_dir=_recipe_output_dir(recipe, Path("packs/parallel-findall-pack"), output_dir_override),
            wait=bool(recipe.get("wait", False)),
            poll_interval=_optional_int(recipe.get("poll_interval")) or 5,
            timeout=_optional_int(recipe.get("timeout")) or 600,
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "taskgroup-pack":
        inputs_path = _resolve_recipe_path(recipe_path, recipe.get("inputs"))
        if inputs_path is None:
            raise ParallelWorkflowError("Recipe field 'inputs' is required for taskgroup-pack.")
        task_inputs = _load_taskgroup_inputs(
            inputs_path,
            prompt_template=_coerce_str(recipe.get("prompt_template")),
        )
        processor = _validate_task_processor(_coerce_str(recipe.get("processor")) or "lite")
        estimated_cost = estimate_taskgroup_cost(len(task_inputs), processor=processor)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "input_count": len(task_inputs),
                    "processor": processor,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "TaskGroup")
        return run_taskgroup_pack(
            task_inputs=task_inputs,
            processor=processor,
            output_dir=_recipe_output_dir(recipe, Path("packs/parallel-taskgroup-pack"), output_dir_override),
            wait=bool(recipe.get("wait", False)),
            poll_interval=_optional_int(recipe.get("poll_interval")) or 5,
            timeout=_optional_int(recipe.get("timeout")) or 600,
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "task-pack":
        processor = _validate_task_processor(_coerce_str(recipe.get("processor")) or DEFAULT_TASK_PROCESSOR)
        estimated_cost = estimate_task_pack_cost(processor=processor)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "processor": processor,
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "Task")
        return run_task_pack(
            task_input=_load_task_input(
                recipe.get("input"),
                _resolve_recipe_path(recipe_path, recipe.get("input_file")),
            ),
            processor=processor,
            output_dir=_recipe_output_dir(recipe, DEFAULT_TASK_PACK_OUTPUT_DIR, output_dir_override),
            output_schema=_recipe_json_schema(recipe_path, recipe, "output_schema", "output_schema_json"),
            input_schema=_recipe_json_schema(recipe_path, recipe, "input_schema", None),
            source_policy=_recipe_source_policy(recipe),
            location=_coerce_str(recipe.get("location")),
            previous_interaction_id=_coerce_str(recipe.get("previous_interaction_id")),
            enable_events=bool(recipe.get("enable_events", False)),
            mcp_servers=_load_mcp_servers(_string_list(recipe.get("mcp_servers") or [])),
            webhook=_recipe_webhook(recipe),
            metadata=_recipe_metadata(recipe),
            api_timeout=_optional_int(recipe.get("api_timeout")) or 3600,
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "task-result":
        run_id = _required_recipe_str(recipe, "run_id")
        output_dir = _recipe_output_dir(recipe, Path("packs/parallel-task-result"), output_dir_override)
        api_timeout = _optional_int(recipe.get("api_timeout")) or 3600
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "run_id": run_id,
                    "output_dir": str(output_dir),
                    "api_timeout": api_timeout,
                }
            )
            return None
        return run_task_result_pack(
            run_id=run_id,
            output_dir=output_dir,
            api_timeout=api_timeout,
        )

    if workflow == "task-events":
        run_id = _required_recipe_str(recipe, "run_id")
        output_dir = _recipe_output_dir(recipe, Path("packs/parallel-task-events"), output_dir_override)
        timeout = _optional_int(recipe.get("timeout")) or 60
        limit = _optional_int(recipe.get("limit")) or 100
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "run_id": run_id,
                    "last_event_id": _coerce_str(recipe.get("last_event_id")),
                    "timeout": timeout,
                    "limit": limit,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_task_events_pack(
            run_id=run_id,
            last_event_id=_coerce_str(recipe.get("last_event_id")),
            timeout=timeout,
            limit=limit,
            output_dir=output_dir,
        )

    if workflow == "diff-brief":
        old_pack_dir = _required_recipe_path(recipe, recipe_path, "old_pack_dir")
        new_pack_dir = _required_recipe_path(recipe, recipe_path, "new_pack_dir")
        output_dir = _recipe_output_dir(recipe, DEFAULT_DIFF_BRIEF_OUTPUT_DIR, output_dir_override)
        processor = _validate_task_processor(_coerce_str(recipe.get("processor")) or DEFAULT_TASK_PROCESSOR)
        api_timeout = _optional_int(recipe.get("api_timeout")) or 3600
        estimated_cost = estimate_task_pack_cost(processor=processor)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "old_pack_dir": str(old_pack_dir),
                    "new_pack_dir": str(new_pack_dir),
                    "processor": processor,
                    "output_dir": str(output_dir),
                    "estimated_cost_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "diff brief")
        return run_diff_brief_pack(
            old_pack_dir=old_pack_dir,
            new_pack_dir=new_pack_dir,
            output_dir=output_dir,
            processor=processor,
            api_timeout=api_timeout,
            estimated_cost_usd=estimated_cost,
        )

    if workflow == "monitor-pack":
        return _run_monitor_recipe(
            recipe,
            recipe_path,
            output_dir_override,
            dry_run,
            max_estimated_cost,
            printer,
        )

    if workflow == "api-pack":
        source = _required_recipe_str(recipe, "source")
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "source": source,
                    "kind": _coerce_str(recipe.get("kind")) or "auto",
                }
            )
            return None
        return run_api_pack(
            source=source,
            kind=_coerce_str(recipe.get("kind")) or "auto",
            output_dir=_recipe_output_dir(recipe, Path("packs/api-pack"), output_dir_override),
        )

    if workflow == "findall-ingest-pack":
        objective = _required_recipe_str(recipe, "objective")
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "objective": objective,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_ingest_pack(
            objective=objective,
            output_dir=output_dir,
        )
    if workflow == "findall-result-pack":
        findall_id = _required_recipe_str(recipe, "findall_id")
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "findall_id": findall_id,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_result_pack(
            findall_id=findall_id,
            output_dir=output_dir,
        )
    if workflow == "findall-schema-pack":
        findall_id = _required_recipe_str(recipe, "findall_id")
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "findall_id": findall_id,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_schema_pack(
            findall_id=findall_id,
            output_dir=output_dir,
        )
    if workflow == "findall-enrich-pack":
        findall_id = _required_recipe_str(recipe, "findall_id")
        output_schema = _recipe_json_schema(
            recipe_path, recipe, "output_schema", "output_schema_json"
        ) or _load_json_file(
            _resolve_recipe_path(recipe_path, recipe.get("output_schema_file")),
            "output_schema",
        )
        processor = _coerce_str(recipe.get("processor")) or "core"
        mcp_servers = _load_mcp_servers(_string_list(recipe.get("mcp_servers") or []))
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "findall_id": findall_id,
                    "processor": processor,
                    "mcp_server_count": len(mcp_servers),
                    "output_schema": _ensure_json_output_schema(output_schema),
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_enrich_pack(
            findall_id=findall_id,
            output_schema=output_schema,
            processor=processor,
            mcp_servers=mcp_servers,
            output_dir=output_dir,
        )
    if workflow == "findall-extend-pack":
        findall_id = _required_recipe_str(recipe, "findall_id")
        additional_match_limit = _required_positive_int(
            recipe.get("additional_match_limit"),
            "additional_match_limit",
            default=1,
        )
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "findall_id": findall_id,
                    "additional_match_limit": additional_match_limit,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_extend_pack(
            findall_id=findall_id,
            additional_match_limit=additional_match_limit,
            output_dir=output_dir,
        )
    if workflow == "findall-cancel-pack":
        findall_id = _required_recipe_str(recipe, "findall_id")
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "findall_id": findall_id,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_cancel_pack(
            findall_id=findall_id,
            output_dir=output_dir,
        )
    if workflow == "findall-events-pack":
        findall_id = _required_recipe_str(recipe, "findall_id")
        limit = _optional_int(recipe.get("limit")) or 100
        output_dir = _recipe_output_dir(recipe, DEFAULT_FINDALL_LIFECYCLE_OUTPUT_DIR, output_dir_override)
        if dry_run:
            printer.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": workflow,
                    "findall_id": findall_id,
                    "limit": limit,
                    "output_dir": str(output_dir),
                }
            )
            return None
        return run_findall_events_pack(
            findall_id=findall_id,
            limit=limit,
            output_dir=output_dir,
        )

    supported = (
        "context-pack, search-pack, discover-docs, extract-pack, fallback-pack, "
        "diff-brief, entity-pack, findall-pack, task-pack, task-result, "
        "task-events, taskgroup-pack, monitor-pack, api-pack, findall-ingest-pack, "
        "findall-result-pack, findall-schema-pack, findall-enrich-pack, "
        "findall-extend-pack, findall-cancel-pack, findall-events-pack"
    )
    raise ParallelWorkflowError(f"Unsupported recipe workflow {workflow!r}. Supported: {supported}.")


def run_entity_pack(
    *,
    objective: str,
    entity_type: str,
    match_limit: int,
    output_dir: Path,
    estimated_cost_usd: float,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.beta.findall.entity_search(
        entity_type=entity_type,
        objective=objective,
        match_limit=match_limit,
    )
    entities = [_jsonable(item) for item in _list(_get(response, "entities"))]
    metadata = {
        "provider": "parallel",
        "workflow": "entity-pack",
        "objective": objective,
        "entity_type": entity_type,
        "match_limit": match_limit,
        "entity_set_id": _get(response, "entity_set_id"),
        "usage": _jsonable(_get(response, "usage")),
        "estimated_cost_usd": estimated_cost_usd,
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="entity-pack",
        pack_filename="entity.pack.json",
        objective=objective,
        items=entities,
        metadata=metadata,
        source_type="parallel_entity",
    )


def run_findall_pack(
    *,
    objective: str,
    entity_type: str,
    match_conditions: list[dict[str, str]],
    generator: str,
    match_limit: int,
    exclude_list: list[dict[str, str]],
    metadata: dict[str, Any],
    webhook: dict[str, Any] | None,
    output_dir: Path,
    wait: bool,
    poll_interval: int,
    timeout: int,
    estimated_cost_usd: float,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    run = client.beta.findall.create(
        objective=objective,
        entity_type=entity_type,
        match_conditions=match_conditions,
        generator=generator,
        match_limit=match_limit,
        **_clean_dict(
            {
                "exclude_list": exclude_list,
                "metadata": metadata or None,
                "webhook": webhook,
            }
        ),
    )
    findall_id = _coerce_str(_get(run, "findall_id")) or _coerce_str(_get(run, "id"))
    result = None
    if wait and not findall_id:
        raise ParallelWorkflowError("Parallel FindAll create response did not include a findall_id.")
    if wait and findall_id:
        deadline = time.monotonic() + timeout
        while True:
            current = client.beta.findall.retrieve(findall_id)
            if not _status_is_active(_get(current, "status")):
                break
            if time.monotonic() >= deadline:
                raise ParallelWorkflowError(
                    f"Parallel FindAll {findall_id} did not complete within {timeout}s."
                )
            time.sleep(poll_interval)
        result = client.beta.findall.result(findall_id)
    candidates = [_jsonable(item) for item in _list(_get(result, "candidates"))] if result else []
    metadata = {
        "provider": "parallel",
        "workflow": "findall-pack",
        "objective": objective,
        "entity_type": entity_type,
        "match_conditions": match_conditions,
        "generator": generator,
        "match_limit": match_limit,
        "exclude_list": exclude_list,
        "metadata": metadata,
        "webhook": _jsonable(webhook),
        "findall_id": findall_id,
        "status": _jsonable(_get(run, "status")),
        "waited_for_result": wait,
        "usage": _jsonable(_get(result, "usage")) if result else None,
        "estimated_cost_usd": estimated_cost_usd,
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-pack",
        pack_filename="findall.pack.json",
        objective=objective,
        items=candidates,
        metadata=metadata,
        source_type="parallel_findall_candidate",
    )


def run_findall_ingest_pack(*, objective: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    ingest = client.beta.findall.ingest(objective=objective)
    item = _jsonable_dict(ingest)
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-ingest-pack",
        pack_filename="findall.ingest.pack.json",
        objective=objective,
        items=[item],
        metadata={"provider": "parallel", "workflow": "findall-ingest-pack", "objective": objective},
        source_type="parallel_findall_schema",
    )


def run_findall_result_pack(*, findall_id: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    result = client.beta.findall.result(findall_id)
    candidates = [_jsonable_dict(item) for item in _list(_get(result, "candidates"))]
    if not candidates:
        candidates = [_jsonable_dict(result)]
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-result-pack",
        pack_filename="findall.result.pack.json",
        objective=f"Parallel FindAll result {findall_id}",
        items=candidates,
        metadata={
            "provider": "parallel",
            "workflow": "findall-result-pack",
            "findall_id": findall_id,
            "run": _jsonable(_get(result, "run")),
            "last_event_id": _get(result, "last_event_id"),
        },
        source_type="parallel_findall_candidate",
    )


def run_findall_schema_pack(*, findall_id: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    schema = client.beta.findall.schema(findall_id)
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-schema-pack",
        pack_filename="findall.schema.pack.json",
        objective=f"Parallel FindAll schema {findall_id}",
        items=[_jsonable_dict(schema)],
        metadata={"provider": "parallel", "workflow": "findall-schema-pack", "findall_id": findall_id},
        source_type="parallel_findall_schema",
    )


def run_findall_enrich_pack(
    *,
    findall_id: str,
    output_schema: dict[str, Any],
    processor: str,
    mcp_servers: list[dict[str, Any]],
    output_dir: Path,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.beta.findall.enrich(
        findall_id=findall_id,
        processor=processor,
        output_schema=_ensure_json_output_schema(output_schema),
        **_clean_dict({"mcp_servers": mcp_servers or None}),
    )
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-enrich-pack",
        pack_filename="findall.enrich.pack.json",
        objective=f"Parallel FindAll enrich {findall_id}",
        items=[_jsonable_dict(response)],
        metadata={
            "provider": "parallel",
            "workflow": "findall-enrich-pack",
            "findall_id": findall_id,
            "processor": processor,
            "mcp_server_count": len(mcp_servers),
        },
        source_type="parallel_findall_schema",
    )


def run_findall_extend_pack(
    *,
    findall_id: str,
    additional_match_limit: int,
    output_dir: Path,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.beta.findall.extend(
        findall_id=findall_id,
        additional_match_limit=additional_match_limit,
    )
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-extend-pack",
        pack_filename="findall.extend.pack.json",
        objective=f"Parallel FindAll extend {findall_id}",
        items=[_jsonable_dict(response)],
        metadata={
            "provider": "parallel",
            "workflow": "findall-extend-pack",
            "findall_id": findall_id,
            "additional_match_limit": additional_match_limit,
        },
        source_type="parallel_findall_schema",
    )


def run_findall_cancel_pack(*, findall_id: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.beta.findall.cancel(findall_id=findall_id)
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-cancel-pack",
        pack_filename="findall.cancel.pack.json",
        objective=f"Parallel FindAll cancel {findall_id}",
        items=[_jsonable_dict(response)],
        metadata={"provider": "parallel", "workflow": "findall-cancel-pack", "findall_id": findall_id},
        source_type="parallel_findall_action",
    )


def run_findall_events_pack(*, findall_id: str, limit: int, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    events = _collect_iterable(client.beta.findall.events(findall_id=findall_id), limit=limit)
    return write_structured_pack(
        output_dir=output_dir,
        workflow="findall-events-pack",
        pack_filename="findall.events.pack.json",
        objective=f"Parallel FindAll events {findall_id}",
        items=events,
        metadata={
            "provider": "parallel",
            "workflow": "findall-events-pack",
            "findall_id": findall_id,
            "limit": limit,
            "event_count": len(events),
        },
        source_type="parallel_findall_event",
    )


def run_taskgroup_pack(
    *,
    task_inputs: list[Any],
    processor: str,
    output_dir: Path,
    wait: bool,
    poll_interval: int,
    timeout: int,
    estimated_cost_usd: float,
) -> Path:
    processor = _validate_task_processor(processor)
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    group = client.task_group.create(metadata={"source": "docpull"})
    task_group_id = _coerce_str(_get(group, "task_group_id")) or _coerce_str(_get(group, "id"))
    if not task_group_id:
        raise ParallelWorkflowError("Parallel TaskGroup create response did not include a task_group_id.")
    run_response = client.task_group.add_runs(
        task_group_id,
        inputs=[
            {
                "input": task_input,
                "processor": processor,
            }
            for task_input in task_inputs
        ],
        default_task_spec=_task_text_spec(),
        refresh_status=False,
    )
    items = _taskgroup_run_items(run_response, task_group_id=task_group_id)
    final_group = None
    if wait:
        final_group = _wait_for_taskgroup_completion(
            client=client,
            task_group_id=task_group_id,
            poll_interval=poll_interval,
            timeout=timeout,
        )
        items = [
            _jsonable(item)
            for item in client.task_group.get_runs(
                task_group_id,
                include_input=True,
                include_output=True,
            )
        ]
    metadata = {
        "provider": "parallel",
        "workflow": "taskgroup-pack",
        "task_group_id": task_group_id,
        "processor": processor,
        "input_count": len(task_inputs),
        "waited_for_outputs": wait,
        "poll_interval": poll_interval if wait else None,
        "timeout": timeout if wait else None,
        "final_group": _jsonable(final_group) if final_group is not None else None,
        "run_response": _jsonable(run_response),
        "estimated_cost_usd": estimated_cost_usd,
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="taskgroup-pack",
        pack_filename="taskgroup.pack.json",
        objective=f"TaskGroup batch with {len(task_inputs)} inputs",
        items=items,
        metadata=metadata,
        source_type="parallel_taskgroup_run",
    )


def _wait_for_taskgroup_completion(
    *,
    client: Any,
    task_group_id: str,
    poll_interval: int,
    timeout: int,
) -> Any:
    deadline = time.monotonic() + timeout
    latest = None
    while True:
        latest = client.task_group.retrieve(task_group_id)
        status = _get(latest, "status")
        if not _status_is_active(status):
            return latest
        if time.monotonic() >= deadline:
            raise ParallelWorkflowError(f"Timed out waiting for TaskGroup {task_group_id} after {timeout}s.")
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))


def _taskgroup_run_items(run_response: Any, *, task_group_id: str) -> list[dict[str, Any]]:
    runs = [_jsonable(item) for item in _list(_get(run_response, "runs"))]
    if runs:
        return runs

    run_ids = [_coerce_str(run_id) for run_id in _list(_get(run_response, "run_ids"))]
    run_ids = [run_id for run_id in run_ids if run_id]
    status = _jsonable(_get(run_response, "status"))
    event_cursor = _coerce_str(_get(run_response, "event_cursor"))
    run_cursor = _coerce_str(_get(run_response, "run_cursor"))
    return [
        _clean_dict(
            {
                "run_id": run_id,
                "task_group_id": task_group_id,
                "status": status,
                "event_cursor": event_cursor,
                "run_cursor": run_cursor,
            }
        )
        for run_id in run_ids
    ]


def run_monitor_create_pack(
    *,
    query: str | None,
    monitor_type: str,
    task_run_id: str | None,
    frequency: str,
    processor: str,
    output_schema: dict[str, Any] | None,
    include_backfill: bool,
    source_policy: dict[str, Any] | None,
    location: str | None,
    webhook: dict[str, Any] | None,
    metadata: dict[str, Any],
    output_dir: Path,
    estimated_cost_usd: float,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    settings = _monitor_settings(
        monitor_type=monitor_type,
        query=query,
        task_run_id=task_run_id,
        output_schema=output_schema,
        include_backfill=include_backfill,
        source_policy=source_policy,
        location=location,
    )
    monitor = client.monitor.create(
        type=monitor_type,
        frequency=frequency,
        processor=processor,
        settings=settings,
        metadata={"source": "docpull", **metadata},
        **_clean_dict({"webhook": webhook}),
    )
    metadata = {
        "provider": "parallel",
        "workflow": "monitor-pack",
        "action": "create",
        "type": monitor_type,
        "query": query,
        "task_run_id": task_run_id,
        "frequency": frequency,
        "processor": processor,
        "settings": _jsonable(settings),
        "webhook": _jsonable(webhook),
        "monitor": _jsonable(monitor),
        "monitor_id": _get(monitor, "monitor_id"),
        "estimated_cost_per_execution_usd": estimated_cost_usd,
    }
    objective = query or task_run_id or "Parallel monitor"
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-pack",
        pack_filename="monitor.pack.json",
        objective=objective,
        items=[_jsonable_dict(monitor)],
        metadata=metadata,
        source_type="parallel_monitor",
    )


def _pagination_next_cursor(response: Any) -> str | None:
    return _coerce_str(_get(response, "next_cursor")) or _coerce_str(_get(response, "cursor"))


def run_monitor_events_pack(
    *,
    monitor_id: str,
    limit: int,
    cursor: str | None,
    event_group_id: str | None,
    include_completions: bool,
    output_dir: Path,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.monitor.events(
        monitor_id,
        **_clean_dict(
            {
                "limit": limit,
                "cursor": cursor,
                "event_group_id": event_group_id,
                "include_completions": include_completions,
            }
        ),
    )
    events = _response_items(response, "events")
    event_groups = _monitor_event_group_summary(events)
    metadata = {
        "provider": "parallel",
        "workflow": "monitor-events-pack",
        "monitor_id": monitor_id,
        "limit": limit,
        "cursor": cursor,
        "event_group_id": event_group_id,
        "include_completions": include_completions,
        "next_cursor": _pagination_next_cursor(response),
        "warnings": _jsonable(_get(response, "warnings")),
        "event_groups": event_groups,
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-events-pack",
        pack_filename="monitor.events.pack.json",
        objective=f"Monitor events for {monitor_id}",
        items=events,
        metadata=metadata,
        source_type="parallel_monitor_event",
    )


def run_monitor_list_pack(
    *,
    limit: int,
    cursor: str | None,
    statuses: list[str],
    monitor_types: list[str],
    output_dir: Path,
) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.monitor.list(
        **_clean_dict(
            {
                "limit": limit,
                "cursor": cursor,
                "status": statuses or None,
                "type": monitor_types or None,
            }
        )
    )
    monitors = _response_items(response, "monitors")
    metadata = {
        "provider": "parallel",
        "workflow": "monitor-list-pack",
        "limit": limit,
        "cursor": cursor,
        "status": statuses,
        "type": monitor_types,
        "next_cursor": _pagination_next_cursor(response),
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-list-pack",
        pack_filename="monitor.list.pack.json",
        objective="Parallel monitor list",
        items=monitors,
        metadata=metadata,
        source_type="parallel_monitor",
    )


def run_monitor_retrieve_pack(*, monitor_id: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    monitor = _jsonable_dict(client.monitor.retrieve(monitor_id))
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-retrieve-pack",
        pack_filename="monitor.retrieve.pack.json",
        objective=f"Parallel monitor {monitor_id}",
        items=[monitor],
        metadata={"provider": "parallel", "workflow": "monitor-retrieve-pack", "monitor_id": monitor_id},
        source_type="parallel_monitor",
    )


def run_monitor_update_pack(
    *,
    monitor_id: str,
    query: str | None,
    frequency: str | None,
    source_policy: dict[str, Any] | None,
    location: str | None,
    webhook: dict[str, Any] | None,
    clear_webhook: bool,
    metadata: dict[str, Any],
    clear_metadata: bool,
    output_dir: Path,
) -> Path:
    advanced_settings = _monitor_advanced_settings(source_policy=source_policy, location=location)
    settings = _clean_dict(
        {
            "query": query,
            "advanced_settings": advanced_settings,
        }
    )
    update_kwargs = _clean_dict(
        {
            "frequency": frequency,
            "settings": settings or None,
            "type": "event_stream" if settings else None,
            "webhook": None if clear_webhook else webhook,
            "metadata": None if clear_metadata else metadata or None,
        }
    )
    if not update_kwargs and not clear_webhook and not clear_metadata:
        raise ParallelWorkflowError(
            "monitor-pack update requires --query, --frequency, source-policy/location, "
            "--webhook-url/--clear-webhook, or metadata changes."
        )
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    if clear_webhook:
        update_kwargs["webhook"] = None
    if clear_metadata:
        update_kwargs["metadata"] = None
    monitor = _jsonable_dict(client.monitor.update(monitor_id, **update_kwargs))
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-update-pack",
        pack_filename="monitor.update.pack.json",
        objective=f"Update Parallel monitor {monitor_id}",
        items=[monitor],
        metadata={
            "provider": "parallel",
            "workflow": "monitor-update-pack",
            "monitor_id": monitor_id,
            "updated": update_kwargs,
        },
        source_type="parallel_monitor",
    )


def run_monitor_cancel_pack(*, monitor_id: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    monitor = _jsonable_dict(client.monitor.cancel(monitor_id))
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-cancel-pack",
        pack_filename="monitor.cancel.pack.json",
        objective=f"Cancel Parallel monitor {monitor_id}",
        items=[monitor],
        metadata={"provider": "parallel", "workflow": "monitor-cancel-pack", "monitor_id": monitor_id},
        source_type="parallel_monitor",
    )


def run_monitor_trigger_pack(*, monitor_id: str, output_dir: Path) -> Path:
    api_key = _require_api_key()
    parallel_client_class = _require_parallel_sdk()
    client = parallel_client_class(api_key=api_key)
    response = client.monitor.trigger(monitor_id)
    item = {
        "name": f"Triggered {monitor_id}",
        "monitor_id": monitor_id,
        "triggered_at": utc_now_iso(),
        "response": _jsonable(response),
    }
    return write_structured_pack(
        output_dir=output_dir,
        workflow="monitor-trigger-pack",
        pack_filename="monitor.trigger.pack.json",
        objective=f"Trigger Parallel monitor {monitor_id}",
        items=[item],
        metadata={"provider": "parallel", "workflow": "monitor-trigger-pack", "monitor_id": monitor_id},
        source_type="parallel_monitor_action",
    )


def run_api_pack(*, source: str, kind: str, output_dir: Path) -> Path:
    text = _read_text_source(source)
    detected_kind = _detect_api_pack_kind(source, text) if kind == "auto" else kind
    if detected_kind == "openapi":
        items, metadata = _openapi_items(text, source=source)
    elif detected_kind == "llms":
        items, metadata = _llms_items(text, source=source)
    else:  # pragma: no cover - argparse/detection prevents this.
        raise ParallelWorkflowError(f"Unsupported api-pack kind: {detected_kind}")
    return write_structured_pack(
        output_dir=output_dir,
        workflow="api-pack",
        pack_filename="api.pack.json",
        objective=f"API context pack from {source}",
        items=items,
        metadata=metadata,
        source_type=f"api_{detected_kind}",
    )


def demo_context_pack(
    *,
    output_dir: Path,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS,
) -> Path:
    """Build a context pack from the packaged Parallel example fixture."""
    try:
        fixture = (
            importlib.resources.files("docpull.fixtures")
            .joinpath(PACKAGE_EXAMPLE_FIXTURE)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError) as err:
        raise ParallelWorkflowError("Packaged Parallel example fixture is missing.") from err
    try:
        raw = json.loads(fixture)
    except json.JSONDecodeError as err:
        raise ParallelWorkflowError(f"Packaged Parallel example fixture is invalid JSON: {err}") from err
    pack = _pack_from_fixture(raw)
    return write_context_pack(pack, output_dir=output_dir, max_tokens_per_file=max_tokens_per_file)


def import_context_pack(
    fixture: Path,
    *,
    output_dir: Path,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS,
) -> Path:
    """Build a context pack from an offline Search/Extract fixture."""
    try:
        raw = json.loads(fixture.read_text(encoding="utf-8"))
    except OSError as err:
        raise ParallelWorkflowError(f"Could not read fixture {fixture}: {err}") from err
    except json.JSONDecodeError as err:
        raise ParallelWorkflowError(f"Invalid JSON fixture {fixture}: {err}") from err

    pack = _pack_from_fixture(raw)
    return write_context_pack(pack, output_dir=output_dir, max_tokens_per_file=max_tokens_per_file)


def write_context_pack(
    pack: ParallelContextPack,
    *,
    output_dir: Path,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS,
) -> Path:
    """Write normalized Parallel data as docpull context-pack artifacts."""
    if not pack.extract_results:
        _write_parallel_pack(output_dir, pack, [], {}, max_tokens_per_file=max_tokens_per_file)
        raise ParallelWorkflowError("Parallel Extract produced no successful results.")

    output_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = output_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    ndjson_path = output_dir / "documents.ndjson"
    manifest = CorpusManifest(output_dir, output_format="ndjson")
    counter = TokenCounter()
    records: list[DocumentRecord] = []
    source_entries: list[dict[str, Any]] = []

    with ndjson_path.open("w", encoding="utf-8") as ndjson:
        for index, result in enumerate(pack.extract_results, start=1):
            url = _require_result_url(result, index)
            title = _coerce_str(_get(result, "title")) or url
            markdown = _result_markdown(result)
            if not markdown.strip():
                pack.extract_errors.append(
                    {
                        "url": url,
                        "error_type": "empty_content",
                        "content": "Extract result did not include full_content or excerpts.",
                    }
                )
                continue

            source_path = sources_dir / f"{index:02d}-{_slugify(title or url)}.md"
            source_path.write_text(markdown, encoding="utf-8")
            source_entries.append(
                {
                    "index": index,
                    "url": url,
                    "title": title,
                    "path": _relative_path(source_path, output_dir),
                }
            )

            chunks = chunk_markdown(markdown, max_tokens=max_tokens_per_file, counter=counter)
            if not chunks:
                chunks = chunk_markdown(
                    f"# {title}\n\n{markdown}",
                    max_tokens=max_tokens_per_file,
                    counter=counter,
                )

            for chunk in chunks:
                provider = _coerce_str(_get(result, "provider")) or "parallel"
                source_type = (
                    "docpull_parallel_fallback" if pack.workflow == "fallback-pack" else "parallel_extract"
                )
                metadata = {
                    "provider": provider,
                    "workflow": pack.workflow,
                    "rank": index,
                    "session_id": pack.session_id,
                    "search_id": pack.search_id,
                    "extract_id": pack.extract_id,
                    "source_path": _relative_path(source_path, output_dir),
                    "fallback_used": _get(result, "fallback_used"),
                }
                record = DocumentRecord.from_page(
                    url=url,
                    title=title,
                    content=chunk.text,
                    metadata={key: value for key, value in metadata.items() if value is not None},
                    extraction={"provider": provider, "result": _safe_result_summary(result)},
                    source_type=source_type,
                    chunk_index=chunk.index,
                    chunk_heading=chunk.heading,
                    token_count=chunk.token_count,
                )
                records.append(record)
                manifest.add_record(record, ndjson_path)
                payload = record.model_dump(mode="json", exclude_none=True)
                ndjson.write(json.dumps(payload, ensure_ascii=False))
                ndjson.write("\n")

    if not records:
        _write_parallel_pack(
            output_dir,
            pack,
            source_entries,
            {"documents_ndjson": "documents.ndjson"},
            max_tokens_per_file=max_tokens_per_file,
        )
        manifest.finalize()
        raise ParallelWorkflowError(
            "Parallel Extract results were present but none contained usable content."
        )

    manifest_path = manifest.finalize()
    sources_path = _write_sources_md(output_dir, pack, source_entries)
    artifacts = {
        "documents_ndjson": _relative_path(ndjson_path, output_dir),
        "corpus_manifest": _relative_path(manifest_path, output_dir),
        "sources": _relative_path(sources_path, output_dir),
    }

    if pack.task_brief:
        brief_path = output_dir / "brief.md"
        brief_path.write_text(pack.task_brief.strip() + "\n", encoding="utf-8")
        artifacts["brief"] = _relative_path(brief_path, output_dir)

    _write_parallel_pack(
        output_dir,
        pack,
        source_entries,
        artifacts,
        max_tokens_per_file=max_tokens_per_file,
        record_count=len(records),
    )
    return output_dir


def write_structured_pack(
    *,
    output_dir: Path,
    workflow: str,
    pack_filename: str,
    objective: str,
    items: list[dict[str, Any]],
    metadata: dict[str, Any],
    source_type: str,
    max_tokens_per_file: int = DEFAULT_MAX_TOKENS,
    extra_artifacts: dict[str, str] | None = None,
) -> Path:
    """Write non-page Parallel products as docpull-compatible records."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sources_dir = output_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = output_dir / "documents.ndjson"
    manifest = CorpusManifest(output_dir, output_format="ndjson")
    counter = TokenCounter()
    records: list[DocumentRecord] = []
    source_entries: list[dict[str, Any]] = []

    with ndjson_path.open("w", encoding="utf-8") as ndjson:
        for index, item in enumerate(items, start=1):
            title = _item_title(item, fallback=f"{workflow} item {index}")
            url = _item_url(item) or f"parallel://{workflow}/{index}"
            markdown = _item_markdown(title=title, url=url, item=item)
            source_path = sources_dir / f"{index:02d}-{_slugify(title)}.md"
            source_path.write_text(markdown, encoding="utf-8")
            source_entries.append(
                {
                    "index": index,
                    "url": url,
                    "title": title,
                    "path": _relative_path(source_path, output_dir),
                }
            )
            for chunk in chunk_markdown(markdown, max_tokens=max_tokens_per_file, counter=counter):
                record = DocumentRecord.from_page(
                    url=url,
                    title=title,
                    content=chunk.text,
                    metadata={
                        "provider": "parallel",
                        "workflow": workflow,
                        "rank": index,
                        "source_path": _relative_path(source_path, output_dir),
                    },
                    extraction={"provider": "parallel", "result": _safe_result_summary(item)},
                    source_type=source_type,
                    chunk_index=chunk.index,
                    chunk_heading=chunk.heading,
                    token_count=chunk.token_count,
                )
                records.append(record)
                manifest.add_record(record, ndjson_path)
                record_payload = record.model_dump(mode="json", exclude_none=True)
                ndjson.write(json.dumps(record_payload, ensure_ascii=False))
                ndjson.write("\n")

    manifest_path = manifest.finalize()
    sources_path = _write_generic_sources_md(output_dir, objective, source_entries)
    artifacts = {
        "documents_ndjson": _relative_path(ndjson_path, output_dir),
        "corpus_manifest": _relative_path(manifest_path, output_dir),
        "sources": _relative_path(sources_path, output_dir),
    }
    artifacts.update(extra_artifacts or {})
    artifacts["agent_context"] = "AGENT_CONTEXT.md"
    request_options = metadata.get("request_options")
    expected_domains = _expected_domains_from_request_options(
        request_options if isinstance(request_options, dict) else {}
    )
    _write_agent_context_md(
        output_dir,
        workflow=workflow,
        objective=objective,
        entries=source_entries,
        artifacts=artifacts,
        record_count=len(records),
        item_count=len(items),
        metadata=metadata,
        expected_domains=expected_domains,
    )
    payload = {
        "schema_version": PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "provider": "parallel",
        "workflow": workflow,
        "objective": objective,
        "item_count": len(items),
        "record_count": len(records),
        "sources": source_entries,
        "metadata": _jsonable(metadata),
        "artifacts": artifacts,
    }
    (output_dir / pack_filename).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return output_dir


def _write_parallel_pack(
    output_dir: Path,
    pack: ParallelContextPack,
    selected_sources: list[dict[str, Any]],
    artifacts: dict[str, str],
    *,
    max_tokens_per_file: int,
    record_count: int = 0,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(artifacts)
    artifacts["agent_context"] = "AGENT_CONTEXT.md"
    _write_agent_context_md(
        output_dir,
        workflow=pack.workflow,
        objective=pack.objective,
        entries=selected_sources,
        artifacts=artifacts,
        record_count=record_count,
        queries=pack.queries,
        search_result_count=len(pack.search_results),
        extract_error_count=len(pack.extract_errors),
        task_brief=bool(pack.task_brief),
        warnings=pack.warnings,
        errors=pack.extract_errors,
        max_tokens_per_file=max_tokens_per_file,
        expected_domains=_expected_domains_from_request_options(pack.request_options),
    )
    payload = {
        "schema_version": PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "provider": "parallel",
        "workflow": pack.workflow,
        "objective": pack.objective,
        "queries": pack.queries,
        "mode": pack.mode,
        "session_id": pack.session_id,
        "search_id": pack.search_id,
        "extract_id": pack.extract_id,
        "task_run_id": pack.task_run_id,
        "max_tokens_per_file": max_tokens_per_file,
        "estimated_cost_usd": pack.estimated_cost_usd,
        "request_options": pack.request_options,
        "selected_urls": [entry["url"] for entry in selected_sources],
        "search_result_count": len(pack.search_results),
        "extract_result_count": len(pack.extract_results),
        "extract_error_count": len(pack.extract_errors),
        "record_count": record_count,
        "sources": selected_sources,
        "errors": pack.extract_errors,
        "warnings": {key: value for key, value in pack.warnings.items() if value},
        "usage": {key: value for key, value in pack.usage.items() if value is not None},
        "artifacts": artifacts,
    }
    if pack.task_basis:
        payload["task_basis"] = pack.task_basis
    path = output_dir / "parallel.pack.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _md_inline_text(value: str) -> str:
    """Neutralize Markdown control characters in provider-supplied inline text."""
    return (
        value.replace("\\", "\\\\")
        .replace("`", "\\`")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\r", " ")
        .replace("\n", " ")
    )


def _md_safe_url(value: str) -> str:
    """Return an http(s) URL safe to embed in Markdown, or '' if not web-safe."""
    cleaned = value.strip().replace("\r", "").replace("\n", "")
    if urlparse(cleaned).scheme not in {"http", "https"}:
        return ""
    return cleaned.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _md_link(title: str, url: str) -> str:
    """Render a provider title/URL as a Markdown link that cannot break out."""
    safe_title = _md_inline_text(title)
    safe_url = _md_safe_url(url)
    if not safe_url:
        return f"{safe_title} (unverified URL)"
    return f"[{safe_title}]({safe_url})"


def _validated_https_urls(urls: list[str]) -> list[str]:
    """Drop any non-HTTPS / private-host URLs from a provider-supplied list."""
    validator = UrlValidator(allowed_schemes={"https"})
    return [url for url in urls if validator.validate(url).is_valid]


def _write_agent_context_md(
    output_dir: Path,
    *,
    workflow: str,
    objective: str,
    entries: list[dict[str, Any]],
    artifacts: dict[str, str],
    record_count: int,
    item_count: int | None = None,
    queries: list[str] | None = None,
    search_result_count: int | None = None,
    extract_error_count: int | None = None,
    task_brief: bool = False,
    warnings: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
    max_tokens_per_file: int | None = None,
    expected_domains: list[str] | None = None,
) -> Path:
    """Write a concise agent load plan for a generated Parallel pack."""
    lines = [
        "# Agent Context",
        "",
        f"Workflow: `{workflow}`",
        f"Objective: {objective}",
        "",
        "## Load Plan",
        "",
    ]

    if record_count:
        lines.extend(
            [
                "1. Start with `documents.ndjson` for chunked context records.",
                "2. Use `sources.md` to inspect source order and local source files.",
                (
                    "3. Use `parallel.pack.json` for workflow IDs, request options, "
                    "warnings, and usage metadata."
                ),
            ]
        )
        if "brief" in artifacts:
            lines.append("4. Load `brief.md` when you need the cited research synthesis.")
    else:
        lines.append(
            "No usable context records were written. Inspect `parallel.pack.json` for preserved errors."
        )

    lines.extend(["", "## Pack Signals", ""])
    signals: list[tuple[str, Any]] = [
        ("Records", record_count),
        ("Sources", len(entries)),
    ]
    if item_count is not None:
        signals.append(("Items", item_count))
    if search_result_count is not None:
        signals.append(("Search results", search_result_count))
    if extract_error_count is not None:
        signals.append(("Extract errors", extract_error_count))
    if max_tokens_per_file is not None:
        signals.append(("Max tokens per record", max_tokens_per_file))
    if task_brief:
        signals.append(("Task brief", "yes"))
    for label, value in signals:
        lines.append(f"- {label}: {value}")

    if queries:
        lines.extend(["", "## Queries", ""])
        lines.extend(f"- {query}" for query in queries)

    if entries:
        lines.extend(["", "## Source Load Order", ""])
        for entry in entries:
            title = _coerce_str(entry.get("title")) or "Untitled source"
            url = _coerce_str(entry.get("url")) or "parallel://unknown"
            source_path = _coerce_str(entry.get("path")) or ""
            index = entry.get("index") or "?"
            local = f" - `{source_path}`" if source_path else ""
            lines.append(f"{index}. {_md_link(title, url)}{local}")

        source_scores = score_source_entries(entries, expected_domains=expected_domains)
        if source_scores:
            lines.extend(["", "## Source Scores", ""])
            for source in source_scores[:10]:
                reason_text = ", ".join(str(reason) for reason in source.get("reasons", []))
                path_text = f" - `{source['path']}`" if source.get("path") else ""
                lines.append(
                    f"- {source['score']}/100 {source['grade']}: "
                    f"{_md_link(str(source['title']), str(source['url']))}{path_text} ({reason_text})"
                )

    warning_lines = _agent_context_warning_lines(warnings or {}, errors or [])
    if warning_lines:
        lines.extend(["", "## Review Before Loading", ""])
        lines.extend(warning_lines)

    if metadata:
        metadata_keys = sorted(str(key) for key in metadata)
        if metadata_keys:
            lines.extend(["", "## Metadata Keys", ""])
            lines.append(", ".join(metadata_keys))

    lines.extend(["", "## Artifact Map", ""])
    for name in sorted(artifacts):
        lines.append(f"- `{name}`: `{artifacts[name]}`")

    path = output_dir / "AGENT_CONTEXT.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _agent_context_warning_lines(
    warnings: dict[str, Any],
    errors: list[dict[str, Any]],
) -> list[str]:
    lines: list[str] = []
    for key in sorted(warnings):
        value = warnings[key]
        if value:
            lines.append(f"- Warning `{key}`: {_compact_json(value)}")
    for error in errors:
        url = _coerce_str(_get(error, "url")) or "unknown URL"
        error_type = _coerce_str(_get(error, "error_type")) or "extract_error"
        lines.append(f"- Extract error `{error_type}`: {_md_inline_text(url)}")
    return lines


def _compact_json(value: Any) -> str:
    try:
        text = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    return text if len(text) <= 240 else text[:237] + "..."


def _expected_domains_from_request_options(request_options: dict[str, Any]) -> list[str]:
    source_policy = request_options.get("source_policy") if isinstance(request_options, dict) else {}
    include_domains = source_policy.get("include_domains") if isinstance(source_policy, dict) else []
    return [str(domain).lower().removeprefix("www.") for domain in include_domains or []]


def _write_sources_md(output_dir: Path, pack: ParallelContextPack, entries: list[dict[str, Any]]) -> Path:
    lines = [
        "# Parallel Context Pack Sources",
        "",
        f"Objective: {pack.objective}",
        "",
        "## Sources",
        "",
    ]
    for entry in entries:
        lines.append(f"{entry['index']}. {_md_link(str(entry['title']), str(entry['url']))}")
        lines.append(f"   - Local: `{entry['path']}`")
    if pack.extract_errors:
        lines.extend(["", "## Extract Errors", ""])
        for error in pack.extract_errors:
            url = _coerce_str(_get(error, "url")) or "unknown URL"
            error_type = _coerce_str(_get(error, "error_type")) or "extract_error"
            lines.append(f"- `{error_type}`: {_md_inline_text(url)}")
    path = output_dir / "sources.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _write_generic_sources_md(output_dir: Path, objective: str, entries: list[dict[str, Any]]) -> Path:
    lines = ["# Context Pack Sources", "", f"Objective: {objective}", "", "## Sources", ""]
    if not entries:
        lines.append("_No records were available when this pack was written._")
    for entry in entries:
        lines.append(f"{entry['index']}. {_md_link(str(entry['title']), str(entry['url']))}")
        lines.append(f"   - Local: `{entry['path']}`")
    path = output_dir / "sources.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _cap_fixture_content(item: Any, max_chars: int = DEFAULT_MAX_FULL_CONTENT_CHARS) -> Any:
    """Bound full_content/excerpts from an imported fixture to the live cap.

    The live Extract path truncates server-side; fixture import has no such
    bound, so a crafted fixture could otherwise write an unbounded source file.
    """
    data = _jsonable(item)
    if not isinstance(data, dict):
        return data
    content = data.get("full_content")
    if isinstance(content, str) and len(content) > max_chars:
        data["full_content"] = content[:max_chars]
    excerpts = data.get("excerpts")
    if isinstance(excerpts, list):
        data["excerpts"] = [
            value[:max_chars] if isinstance(value, str) and len(value) > max_chars else value
            for value in excerpts
        ]
    return data


def _pack_from_fixture(raw: Any) -> ParallelContextPack:
    if not isinstance(raw, dict):
        raise ParallelWorkflowError("Fixture must be a JSON object.")
    objective = _coerce_str(raw.get("objective"))
    if not objective:
        raise ParallelWorkflowError("Fixture is missing required string field: objective.")
    queries = raw.get("queries") or raw.get("search_queries") or []
    if not isinstance(queries, list) or not all(isinstance(item, str) and item for item in queries):
        raise ParallelWorkflowError("Fixture field 'queries' must be a list of non-empty strings.")

    search = raw.get("search") or {}
    extract = raw.get("extract") or {}
    task = raw.get("task") or {}
    if not isinstance(search, dict) or not isinstance(extract, dict) or not isinstance(task, dict):
        raise ParallelWorkflowError(
            "Fixture fields 'search', 'extract', and 'task' must be objects when present."
        )

    extract_results = _list(extract.get("results"))
    extract_errors = _list(extract.get("errors"))
    if not extract_results and not extract_errors:
        raise ParallelWorkflowError("Fixture must include extract.results or extract.errors.")

    session_id = raw.get("session_id") or search.get("session_id") or extract.get("session_id")
    return ParallelContextPack(
        objective=objective,
        queries=queries,
        mode=_coerce_str(raw.get("mode")) or DEFAULT_MODE,
        session_id=_coerce_str(session_id),
        search_id=_coerce_str(search.get("search_id")),
        extract_id=_coerce_str(extract.get("extract_id")),
        task_run_id=_coerce_str(task.get("run_id") or task.get("task_run_id")),
        search_results=[_jsonable(item) for item in _list(search.get("results"))],
        extract_results=[_cap_fixture_content(item) for item in extract_results],
        extract_errors=[_jsonable(item) for item in extract_errors],
        task_brief=_coerce_str(task.get("brief") or task.get("content")),
        task_basis=_list(task.get("basis")),
        request_options=_jsonable(raw.get("request_options") or {}),
        warnings=_jsonable(raw.get("warnings") or {}),
        usage=_jsonable(raw.get("usage") or {}),
        estimated_cost_usd=raw.get("estimated_cost_usd"),
    )


def _load_recipe(recipe_path: Path) -> dict[str, Any]:
    try:
        raw_text = recipe_path.read_text(encoding="utf-8")
    except OSError as err:
        raise ParallelWorkflowError(f"Could not read recipe {recipe_path}: {err}") from err
    if len(raw_text) > MAX_RECIPE_BYTES:
        raise ParallelWorkflowError(f"Recipe {recipe_path} exceeds the {MAX_RECIPE_BYTES}-byte limit.")
    try:
        if recipe_path.suffix.lower() in {".yaml", ".yml"}:
            import yaml

            raw = yaml.safe_load(raw_text)
        else:
            raw = json.loads(raw_text)
    except Exception as err:
        raise ParallelWorkflowError(f"Invalid recipe {recipe_path}: {err}") from err
    if not isinstance(raw, dict):
        raise ParallelWorkflowError("Recipe must be a JSON/YAML object.")
    return raw


def _require_api_key() -> str:
    lookup = _lookup_parallel_api_key()
    api_key = lookup.value
    if lookup.invalid_reason:
        raise ParallelWorkflowError(
            f"Live Parallel workflows found an invalid API key source "
            f"({lookup.source}): {lookup.invalid_reason}."
        )
    if not api_key:
        raise ParallelWorkflowError(
            f"Live Parallel workflows require {PARALLEL_API_KEY_ENV}. "
            "Run `docpull parallel init` for durable setup, set it in your environment, "
            "or use `docpull parallel import` with a fixture."
        )
    return api_key


def _require_parallel_sdk() -> Any:
    try:
        from parallel import Parallel
    except ImportError as err:
        raise ParallelWorkflowError(
            "docpull parallel requires the optional Parallel SDK. "
            f"Install it with: {PARALLEL_INSTALL_COMMAND}"
        ) from err
    return Parallel


def get_parallel_auth_status(*, redact_paths: bool = False) -> dict[str, Any]:
    """Return non-secret Parallel local configuration details for humans and agents."""
    sdk_installed = _parallel_sdk_installed()
    lookup = _lookup_parallel_api_key()
    api_key_present = bool(lookup.value)
    key_invalid = bool(lookup.invalid_reason)
    ready = sdk_installed and api_key_present and not key_invalid
    next_steps: list[str] = []
    if not sdk_installed:
        next_steps.append(f"Install the optional SDK: {PARALLEL_INSTALL_COMMAND}")
    if key_invalid:
        next_steps.append(f"Fix the invalid {PARALLEL_API_KEY_ENV} value before running live workflows.")
    if not api_key_present and not key_invalid:
        next_steps.append(f"Create or copy a key from {PARALLEL_ACCOUNT_URL}.")
        next_steps.append("Run `docpull parallel init` to store a user-level key.")
        next_steps.append("For project-local agent workflows, run `docpull parallel init --project`.")
        next_steps.append(f"For CI, set an environment secret: {PARALLEL_API_KEY_COMMAND}")
    if ready:
        next_steps.append(
            "Run `docpull parallel probe --mode validation --json` for explicit live key validation."
        )
        next_steps.append(f"Plan a workflow before spending credits: {PARALLEL_DRY_RUN_EXAMPLE}")
        next_steps.append("Keep --max-estimated-cost on live workflows to enforce a local spend guard.")

    source_path = str(lookup.path) if lookup.path else None
    if redact_paths and source_path:
        source_path = "[redacted]"
    project_env_path = str(_find_project_env_path(Path.cwd()) or (Path.cwd() / PARALLEL_PROJECT_ENV_FILENAME))

    return {
        "provider": "parallel",
        "ready": ready,
        "sdk_installed": sdk_installed,
        "api_key_env_var": PARALLEL_API_KEY_ENV,
        "api_key_present": api_key_present,
        "api_key_source": lookup.source,
        "api_key_source_path": source_path,
        "api_key_invalid_reason": lookup.invalid_reason,
        "account_url": PARALLEL_ACCOUNT_URL,
        "install_command": PARALLEL_INSTALL_COMMAND,
        "api_key_command": PARALLEL_API_KEY_COMMAND,
        "init_command": "docpull parallel init",
        "project_init_command": "docpull parallel init --project",
        "user_secrets_path": "[redacted]" if redact_paths else str(_parallel_user_secrets_path()),
        "project_env_path": "[redacted]" if redact_paths else project_env_path,
        "paths_redacted": redact_paths,
        "dry_run_example": PARALLEL_DRY_RUN_EXAMPLE,
        "validation": (
            "local SDK/key-presence check only; no live key validation call is made by auth; "
            "run `docpull parallel probe` for explicit live key validation"
        ),
        "key_handling": (
            "PARALLEL_API_KEY environment, project .env.local, or user secrets.env; "
            "docpull does not echo API keys or write them to pack artifacts"
        ),
        "next_steps": next_steps,
    }


def _parallel_sdk_installed() -> bool:
    return importlib.util.find_spec("parallel") is not None


def init_parallel_auth(
    *,
    project: bool = False,
    from_stdin: bool = False,
    force: bool = False,
    update_gitignore: bool = True,
) -> dict[str, Any]:
    """Store a Parallel API key in a non-pack local secrets file."""
    api_key = _read_parallel_api_key_for_init(from_stdin=from_stdin)
    if project:
        path = Path.cwd() / PARALLEL_PROJECT_ENV_FILENAME
        key_source = "project_env"
    else:
        path = _parallel_user_secrets_path()
        key_source = "user_config"
    _write_parallel_secret_file(path, api_key, force=force)
    gitignore_path: Path | None = None
    gitignore_updated = False
    if project and update_gitignore:
        gitignore_path, gitignore_updated = _ensure_gitignore_entry(
            Path.cwd(),
            PARALLEL_PROJECT_ENV_FILENAME,
        )
    return {
        "path": str(path),
        "key_source": key_source,
        "gitignore_path": str(gitignore_path) if gitignore_path else None,
        "gitignore_updated": gitignore_updated,
    }


def _read_parallel_api_key_for_init(*, from_stdin: bool) -> str:
    value = sys.stdin.readline() if from_stdin else getpass.getpass("Parallel API key: ")
    try:
        return validate_provider_api_key(value, label="Parallel API key")
    except ProviderKeyError as err:
        raise ParallelWorkflowError(str(err)) from err


def _lookup_parallel_api_key() -> ParallelApiKeyLookup:
    return lookup_provider_api_key("parallel")


def _clean_parallel_api_key(value: str | None) -> str | None:
    return clean_api_key(value)


def _parallel_user_secrets_path() -> Path:
    return user_secrets_path()


def _find_project_env_path(start: Path) -> Path | None:
    return find_project_env_path(start)


def _read_parallel_key_file(path: Path) -> str | None:
    return read_key_file(path, PARALLEL_API_KEY_ENV)


def _parse_env_assignment(line: str) -> tuple[str, str] | None:
    return parse_env_assignment(line)


def _unquote_env_value(value: str) -> str:
    from .provider_keys import unquote_env_value

    return unquote_env_value(value)


def _write_parallel_secret_file(path: Path, api_key: str, *, force: bool) -> None:
    try:
        write_provider_secret("parallel", path, api_key, force=force)
    except FileExistsError as err:
        raise ParallelWorkflowError(str(err)) from err
    except ProviderKeyError as err:
        raise ParallelWorkflowError(str(err)) from err


def _parallel_key_assignment(api_key: str) -> str:
    return key_assignment(PARALLEL_API_KEY_ENV, api_key)


def _quote_env_value(value: str) -> str:
    return quote_env_value(value)


def _chmod_best_effort(path: Path, mode: int) -> None:
    with suppress(OSError):
        path.chmod(mode)


def _ensure_gitignore_entry(project_dir: Path, entry: str) -> tuple[Path, bool]:
    gitignore = project_dir / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8").splitlines() if gitignore.exists() else []
    normalized = {line.strip() for line in existing if line.strip() and not line.strip().startswith("#")}
    variants = {entry, f"/{entry}"}
    if normalized & variants:
        return gitignore, False
    lines = list(existing)
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(entry)
    gitignore.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return gitignore, True


def _print_parallel_auth_status(console: Console, status: dict[str, Any]) -> None:
    console.print("[bold]Parallel local auth preflight[/bold]")
    console.print(f"SDK: {'installed' if status['sdk_installed'] else 'missing'}")
    console.print(f"{status['api_key_env_var']}: {'detected' if status['api_key_present'] else 'missing'}")
    console.print(f"Key source: {status['api_key_source']}")
    if status.get("api_key_source_path"):
        console.print(f"Key path: {status['api_key_source_path']}")
    console.print("Secret handling: keys are never printed or written to pack artifacts.")
    console.print(
        "Validation: local SDK/key-presence check only; no live key validation call is made by auth. "
        "Use `docpull parallel probe` for live checks."
    )
    if status["ready"]:
        console.print("[green]Local configuration is present for live Parallel workflows.[/green]")
    else:
        console.print("[yellow]Local configuration is incomplete for live Parallel workflows.[/yellow]")
    if status["next_steps"]:
        console.print("Next steps:")
        for step in status["next_steps"]:
            console.print("- " + escape(str(step)))


def _task_text_spec() -> Any:
    try:
        from parallel.types import TaskSpecParam, TextSchemaParam
    except ImportError:
        return {"output_schema": {"type": "text"}}
    return TaskSpecParam(output_schema=TextSchemaParam(type="text"))


def estimate_context_pack_cost(
    *,
    extract_limit: int,
    max_search_results: int | None = None,
    task_brief: bool = False,
    task_processor: str = DEFAULT_TASK_PROCESSOR,
) -> float:
    search_results = max_search_results or 10
    additional_results = max(0, search_results - 10)
    cost = SEARCH_BASE_COST_USD + (additional_results * SEARCH_ADDITIONAL_RESULT_COST_USD)
    cost += extract_limit * EXTRACT_COST_PER_URL_USD
    if task_brief:
        cost += _task_processor_cost(task_processor)
    return round(cost, 6)


def estimate_search_pack_cost(*, max_search_results: int | None = None) -> float:
    search_results = max_search_results or 10
    additional_results = max(0, search_results - 10)
    return round(SEARCH_BASE_COST_USD + (additional_results * SEARCH_ADDITIONAL_RESULT_COST_USD), 6)


def estimate_extract_pack_cost(*, url_count: int) -> float:
    return round(url_count * EXTRACT_COST_PER_URL_USD, 6)


def estimate_task_pack_cost(*, processor: str) -> float:
    return round(_task_processor_cost(processor), 6)


def estimate_entity_search_cost(*, match_limit: int) -> float:
    additional_results = max(0, match_limit - 100)
    return round(
        ENTITY_SEARCH_BASE_COST_USD + additional_results * ENTITY_SEARCH_ADDITIONAL_RESULT_COST_USD,
        6,
    )


def estimate_findall_cost(*, generator: str, match_limit: int) -> float:
    fixed, per_match = FINDALL_GENERATOR_COST_USD[generator]
    return round(fixed + per_match * match_limit, 6)


def estimate_taskgroup_cost(input_count: int, *, processor: str) -> float:
    return round(input_count * _task_processor_cost(processor), 6)


def estimate_monitor_execution_cost(*, processor: str) -> float:
    return MONITOR_EXECUTION_COST_USD[processor]


def _task_processor_cost(processor: str) -> float:
    normalized = _normalize_task_processor(_validate_task_processor(processor))
    return TASK_PROCESSOR_COST_USD[normalized]


def _validate_task_processor(processor: str) -> str:
    processor = processor.strip()
    normalized = processor.removesuffix("-fast")
    if not processor or normalized not in TASK_PROCESSOR_COST_USD:
        choices = ", ".join(VALID_TASK_PROCESSORS)
        raise ParallelWorkflowError(
            f"Unsupported Parallel Task processor: {processor!r}. Use one of: {choices}."
        )
    return processor


def _normalize_task_processor(processor: str) -> str:
    return processor.removesuffix("-fast")


def _parse_match_conditions(raw_conditions: list[str], objective: str) -> list[dict[str, str]]:
    if not raw_conditions:
        return [
            {
                "name": "matches_objective",
                "description": f"Entity must satisfy the objective: {objective}",
            }
        ]
    conditions: list[dict[str, str]] = []
    for index, raw in enumerate(raw_conditions, start=1):
        if "=" in raw:
            name, description = raw.split("=", 1)
            name = name.strip() or f"condition_{index}"
            description = description.strip()
        else:
            name = f"condition_{index}"
            description = raw.strip()
        if not description:
            raise ParallelWorkflowError("FindAll match conditions cannot be empty.")
        conditions.append({"name": name, "description": description})
    return conditions


def _build_source_policy(
    *,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    after_date: str | None = None,
) -> dict[str, Any]:
    include_domains = _validate_source_domains(include_domains or [], "include_domains")
    exclude_domains = _validate_source_domains(exclude_domains or [], "exclude_domains")
    policy = _clean_dict(
        {
            "include_domains": include_domains or None,
            "exclude_domains": exclude_domains or None,
            "after_date": after_date,
        }
    )
    if len(policy.get("include_domains", [])) + len(policy.get("exclude_domains", [])) > 200:
        raise ParallelWorkflowError("Parallel source policy supports at most 200 include/exclude domains.")
    return policy


def _validate_source_domains(domains: list[str], field_name: str) -> list[str]:
    normalized: list[str] = []
    for raw in domains:
        value = raw.strip().lower().removeprefix("www.")
        if not value:
            raise ParallelWorkflowError(f"{field_name} cannot contain empty domains.")
        if "://" in value or "/" in value or "\\" in value or any(ch.isspace() for ch in value):
            raise ParallelWorkflowError(
                f"{field_name} entries must be domains only, not URLs or paths: {raw}"
            )
        normalized.append(value)
    return normalized


def _build_fetch_policy(
    *,
    max_age_seconds: int | None,
    timeout_seconds: int | None,
    disable_cache_fallback: bool,
) -> dict[str, Any]:
    return _clean_dict(
        {
            "max_age_seconds": max_age_seconds,
            "timeout_seconds": timeout_seconds,
            "disable_cache_fallback": True if disable_cache_fallback else None,
        }
    )


def _excerpt_settings(max_chars_per_result: int | None) -> dict[str, Any] | None:
    if max_chars_per_result is None:
        return None
    return {"max_chars_per_result": max_chars_per_result}


def _build_search_kwargs(
    *,
    objective: str,
    queries: list[str],
    mode: str,
    session_id: str,
    source_policy: dict[str, Any] | None,
    fetch_policy: dict[str, Any] | None,
    max_search_results: int | None,
    max_search_chars_total: int | None,
    excerpt_chars_per_result: int | None,
    location: str | None,
    client_model: str | None,
) -> dict[str, Any]:
    search_kwargs: dict[str, Any] = {
        "objective": objective,
        "search_queries": queries,
        "mode": mode,
        "session_id": session_id,
    }
    if client_model:
        search_kwargs["client_model"] = client_model
    if max_search_chars_total:
        search_kwargs["max_chars_total"] = max_search_chars_total
    advanced_settings = _clean_dict(
        {
            "source_policy": source_policy,
            "fetch_policy": fetch_policy,
            "excerpt_settings": _excerpt_settings(excerpt_chars_per_result),
            "location": location,
            "max_results": max_search_results,
        }
    )
    if advanced_settings:
        search_kwargs["advanced_settings"] = advanced_settings
    return search_kwargs


def _build_request_options(
    *,
    source_policy: dict[str, Any] | None,
    fetch_policy: dict[str, Any] | None,
    excerpt_chars_per_result: int | None,
    location: str | None,
    max_search_results: int | None,
    max_search_chars_total: int | None,
    max_extract_chars_total: int | None,
    max_full_content_chars: int | None,
    client_model: str | None,
    full_content: bool,
) -> dict[str, Any]:
    return _clean_dict(
        {
            "source_policy": source_policy,
            "fetch_policy": fetch_policy,
            "excerpt_chars_per_result": excerpt_chars_per_result,
            "location": location,
            "max_search_results": max_search_results,
            "max_search_chars_total": max_search_chars_total,
            "max_extract_chars_total": max_extract_chars_total,
            "max_full_content_chars": max_full_content_chars if full_content else None,
            "client_model": client_model,
            "full_content": full_content,
        }
    )


def _build_extract_advanced_settings(
    *,
    full_content: bool,
    max_full_content_chars: int | None,
    fetch_policy: dict[str, Any] | None,
    excerpt_chars_per_result: int | None,
) -> dict[str, Any]:
    settings = _clean_dict(
        {
            "fetch_policy": fetch_policy,
            "excerpt_settings": _excerpt_settings(excerpt_chars_per_result),
        }
    )
    if not full_content:
        settings["full_content"] = False
        return settings
    if max_full_content_chars:
        settings["full_content"] = {"max_chars_per_result": max_full_content_chars}
        return settings
    settings["full_content"] = True
    return settings


def _clean_dict(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value not in (None, [], {})}


def _load_extract_urls(urls: list[str], url_file: Path | None) -> list[str]:
    combined = list(urls)
    if url_file is not None:
        try:
            text = url_file.read_text(encoding="utf-8")
        except OSError as err:
            raise ParallelWorkflowError(f"Could not read URL file {url_file}: {err}") from err
        stripped = text.strip()
        if stripped.startswith("["):
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as err:
                raise ParallelWorkflowError(f"Invalid URL JSON in {url_file}: {err}") from err
            if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                raise ParallelWorkflowError("URL JSON file must contain an array of strings.")
            combined.extend(raw)
        else:
            combined.extend(line.strip() for line in text.splitlines() if line.strip())
    return _validate_extract_urls(combined)


def _validate_extract_urls(urls: list[str]) -> list[str]:
    if not urls:
        raise ParallelWorkflowError("extract-pack requires at least one URL or --url-file.")
    if len(urls) > MAX_EXTRACT_URLS_PER_REQUEST:
        raise ParallelWorkflowError(
            f"Parallel Extract supports at most {MAX_EXTRACT_URLS_PER_REQUEST} URLs per request."
        )
    validator = UrlValidator(allowed_schemes={"https"})
    validated: list[str] = []
    seen: set[str] = set()
    for raw in urls:
        url = raw.strip()
        if not url:
            continue
        validation = validator.validate(url)
        if not validation.is_valid:
            raise ParallelWorkflowError(f"Extract URL rejected: {validation.rejection_reason}")
        if url not in seen:
            seen.add(url)
            validated.append(url)
    if not validated:
        raise ParallelWorkflowError("extract-pack did not receive any non-empty URLs.")
    return validated


def _discovery_items(
    results: list[dict[str, Any]],
    *,
    expected_domains: list[str],
    crawl_profile: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    raw_by_url: dict[str, dict[str, Any]] = {}
    for index, result in enumerate(results, start=1):
        url = _coerce_str(_get(result, "url"))
        if not url:
            continue
        title = _coerce_str(_get(result, "title")) or url
        entry = {"index": index, "url": url, "title": title}
        entries.append(entry)
        raw = _jsonable_dict(result)
        raw["index"] = index
        raw["url"] = url
        raw["title"] = title
        raw_by_url[url] = raw

    scored = score_source_entries(entries, expected_domains=expected_domains)
    items: list[dict[str, Any]] = []
    for score in scored:
        url = str(score["url"])
        raw = raw_by_url.get(url, {"url": url, "title": score.get("title") or url})
        raw["source_score"] = score
        raw["next_command"] = _docpull_crawl_command(url, crawl_profile=crawl_profile)
        items.append(raw)
    return items


def _docpull_crawl_command(url: str, *, crawl_profile: str) -> str:
    output_dir = f"./docs/{_discovery_output_slug(url)}"
    return (
        f"docpull {shlex.quote(url)} "
        f"--profile {shlex.quote(crawl_profile)} --cache -o {shlex.quote(output_dir)}"
    )


def _discovery_output_slug(url: str) -> str:
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.") or "source"
    path = parsed.path.strip("/")
    if path:
        tail = path.split("/")[-1] or path.replace("/", "-")
        return _slugify(f"{domain}-{tail}")
    return _slugify(domain)


def _discovery_next_steps_md(
    objective: str,
    items: list[dict[str, Any]],
    *,
    crawl_profile: str,
) -> str:
    lines = [
        "# Discovery Next Steps",
        "",
        f"Objective: {objective}",
        f"Crawl profile: `{crawl_profile}`",
        "",
        "## Recommended Crawls",
        "",
    ]
    if not items:
        lines.append("No candidate URLs were discovered.")
        return "\n".join(lines).rstrip() + "\n"
    for item in items[:10]:
        title = _coerce_str(_get(item, "title")) or _coerce_str(_get(item, "url")) or "source"
        url = _coerce_str(_get(item, "url")) or ""
        source_score = _get(item, "source_score") or {}
        command = _coerce_str(_get(item, "next_command")) or _docpull_crawl_command(
            url,
            crawl_profile=crawl_profile,
        )
        lines.extend(
            [
                f"### {_md_inline_text(title)}",
                "",
                f"- URL: {_md_inline_text(url)}",
                (
                    f"- Score: {_get(source_score, 'score', '?')}/100 "
                    f"({_get(source_score, 'grade', 'unscored')})"
                ),
                "",
                "```bash",
                command,
                "```",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _core_docpull_extract_result(url: str, *, profile: str, max_core_chars: int) -> dict[str, Any]:
    from .core.fetcher import fetch_one

    with tempfile.TemporaryDirectory(prefix="docpull-fallback-core-") as temp_dir:
        ctx = fetch_one(url, profile=profile, output={"directory": Path(temp_dir)})
    if ctx.error:
        raise RuntimeError(ctx.error)
    if ctx.should_skip:
        reason = ctx.skip_reason or "core docpull skipped the page"
        raise RuntimeError(reason)
    markdown = ctx.markdown or ""
    if not markdown.strip():
        raise RuntimeError("core docpull returned empty Markdown")
    title = ctx.title or _coerce_str(ctx.metadata.get("title")) or url
    return {
        "url": url,
        "title": title,
        "full_content": _truncate_text(markdown, max_core_chars),
        "provider": "docpull_core",
        "fallback_used": False,
        "core_profile": profile,
        "core_status_code": ctx.status_code,
        "core_content_type": ctx.content_type,
        "extraction_info": _jsonable(ctx.extraction_info),
    }


def _mark_parallel_fallback_result(result: dict[str, Any]) -> dict[str, Any]:
    result["provider"] = "parallel_extract"
    result["fallback_used"] = True
    return result


def _truncate_text(value: str, max_chars: int | None) -> str:
    if not max_chars or len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "\n\n[truncated by docpull fallback-pack]\n"


def _diff_brief_prompt(diff: dict[str, Any]) -> str:
    payload = {
        "old_pack_dir": diff["old_pack_dir"],
        "new_pack_dir": diff["new_pack_dir"],
        "added_urls": diff["added_urls"],
        "removed_urls": diff["removed_urls"],
        "changed_urls": diff["changed_urls"],
        "old_record_count": diff["old_record_count"],
        "new_record_count": diff["new_record_count"],
    }
    return (
        "Write a concise agent-ready change brief for this docpull context pack diff. "
        "Call out added, removed, and changed source URLs, likely impact, and what an "
        "agent should reload first. Use Markdown.\n\n" + json.dumps(payload, indent=2, ensure_ascii=False)
    )


def _load_task_input(raw_input: Any, input_file: Path | None) -> Any:
    if input_file is not None:
        try:
            text = input_file.read_text(encoding="utf-8")
        except OSError as err:
            raise ParallelWorkflowError(f"Could not read task input file {input_file}: {err}") from err
        return _maybe_json(text.strip())
    if raw_input is None:
        raise ParallelWorkflowError("task-pack requires task input text or --input-file.")
    if isinstance(raw_input, str):
        return _maybe_json(raw_input)
    return raw_input


def _maybe_json(value: str) -> Any:
    if value.startswith(("{", "[")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _load_optional_json_schema(path: Path | None, inline_json: str | None) -> dict[str, Any] | None:
    if path is not None and inline_json:
        raise ParallelWorkflowError("Use either a schema file or inline schema JSON, not both.")
    if path is not None:
        return _load_json_file(path, "schema")
    if inline_json:
        try:
            parsed = json.loads(inline_json)
        except json.JSONDecodeError as err:
            raise ParallelWorkflowError(f"Invalid inline schema JSON: {err}") from err
        if not isinstance(parsed, dict):
            raise ParallelWorkflowError("Inline schema JSON must be an object.")
        return parsed
    return None


def _load_json_file(path: Path | None, field_name: str) -> dict[str, Any]:
    if path is None:
        raise ParallelWorkflowError(f"{field_name} JSON file is required.")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise ParallelWorkflowError(f"Could not read {field_name} file {path}: {err}") from err
    except json.JSONDecodeError as err:
        raise ParallelWorkflowError(f"Invalid {field_name} JSON in {path}: {err}") from err
    if not isinstance(parsed, dict):
        raise ParallelWorkflowError(f"{field_name} JSON must be an object.")
    return parsed


def _parse_metadata_pairs(raw_pairs: list[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for raw in raw_pairs:
        if "=" not in raw:
            raise ParallelWorkflowError("Metadata entries must use KEY=VALUE.")
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ParallelWorkflowError("Metadata keys cannot be empty.")
        metadata[key] = value.strip()
    return metadata


def _parse_exclude_candidates(raw_candidates: list[str]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for raw in raw_candidates:
        value = raw.strip()
        if not value:
            continue
        if "=" in value:
            key, name = value.split("=", 1)
            key = key.strip() or "id"
            name = name.strip()
            if not name:
                raise ParallelWorkflowError("Exclude candidates cannot have empty values.")
            candidates.append({key: name})
        else:
            candidates.append({"name": value})
    return candidates


def _build_webhook(url: str | None, event_types: list[str]) -> dict[str, Any] | None:
    if not url:
        return None
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(url)
    if not validation.is_valid:
        raise ParallelWorkflowError(f"Webhook URL rejected: {validation.rejection_reason}")
    return _clean_dict({"url": url, "event_types": event_types or None})


def _load_mcp_servers(raw_servers: list[str]) -> list[dict[str, Any]]:
    servers: list[dict[str, Any]] = []
    for raw in raw_servers:
        source = raw.strip()
        if not source:
            continue
        if source.startswith("{"):
            try:
                value = json.loads(source)
            except json.JSONDecodeError as err:
                raise ParallelWorkflowError(f"Invalid MCP server JSON: {err}") from err
        else:
            value = _load_json_file(Path(source), "mcp_server")
        if not isinstance(value, dict):
            raise ParallelWorkflowError("MCP server entries must be JSON objects.")
        # Live auth headers must reach the Parallel API as-is. Any pack metadata
        # derived from these kwargs is redacted at serialization time via
        # _redact_sensitive_headers, so the raw value is only used for the call.
        servers.append(value)
    return servers


def _redact_sensitive_headers(value: Any) -> Any:
    payload = _jsonable(value)
    if isinstance(payload, dict):
        copy = dict(payload)
        headers = copy.get("headers")
        if isinstance(headers, dict):
            copy["headers"] = {str(key): "<redacted>" for key in headers}
        if "mcp_servers" in copy and isinstance(copy["mcp_servers"], list):
            copy["mcp_servers"] = [_redact_sensitive_headers(item) for item in copy["mcp_servers"]]
        return copy
    return payload


def _task_spec(
    *,
    output_schema: dict[str, Any] | None,
    input_schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if output_schema is None and input_schema is None:
        return None
    return _clean_dict(
        {
            "output_schema": _task_output_schema(output_schema),
            "input_schema": input_schema,
        }
    )


def _task_output_schema(output_schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if output_schema is None:
        return None
    if output_schema.get("type") in {"text", "auto", "json"}:
        return output_schema
    return {"type": "json", "json_schema": output_schema}


def _ensure_json_output_schema(output_schema: dict[str, Any]) -> dict[str, Any]:
    if output_schema.get("type") == "json" and isinstance(output_schema.get("json_schema"), dict):
        return output_schema
    return {"type": "json", "json_schema": output_schema}


def _task_advanced_settings(location: str | None) -> dict[str, Any] | None:
    return _clean_dict({"location": location}) or None


def _task_objective(task_input: Any) -> str:
    if isinstance(task_input, str):
        return task_input[:160] or "Parallel Task"
    return "Parallel structured Task"


def _monitor_advanced_settings(
    *,
    source_policy: dict[str, Any] | None,
    location: str | None,
) -> dict[str, Any] | None:
    return _clean_dict({"source_policy": source_policy, "location": location}) or None


def _monitor_settings(
    *,
    monitor_type: str,
    query: str | None,
    task_run_id: str | None,
    output_schema: dict[str, Any] | None,
    include_backfill: bool,
    source_policy: dict[str, Any] | None,
    location: str | None,
) -> dict[str, Any]:
    if monitor_type == "snapshot":
        if not task_run_id:
            raise ParallelWorkflowError("snapshot monitor creation requires --task-run-id.")
        return {"task_run_id": task_run_id}
    if not query:
        raise ParallelWorkflowError("event_stream monitor creation requires a query.")
    return _clean_dict(
        {
            "query": query,
            "output_schema": _ensure_json_output_schema(output_schema) if output_schema else None,
            "include_backfill": True if include_backfill else None,
            "advanced_settings": _monitor_advanced_settings(
                source_policy=source_policy,
                location=location,
            ),
        }
    )


def _monitor_event_group_summary(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for event in events:
        group_id = (
            _coerce_str(_get(event, "event_group_id")) or _coerce_str(_get(event, "group_id")) or "ungrouped"
        )
        counts[group_id] = counts.get(group_id, 0) + 1
    return [{"event_group_id": key, "event_count": count} for key, count in sorted(counts.items())]


def _collect_iterable(iterable: Any, *, limit: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in iterable:
        items.append(_jsonable_dict(item))
        if len(items) >= limit:
            break
    return items


def _ensure_within_cwd(candidate: Path, *, field: str) -> Path:
    """Confine a recipe-sourced path to the working tree.

    The CLI ``--output-dir`` override is trusted and exempt; a recipe field must
    not redirect writes outside the current directory via an absolute path or
    ``..`` traversal (e.g. into ~/.ssh or /etc). Returns the original candidate
    on success so existing relative-path behavior is preserved.
    """
    cwd = Path.cwd().resolve()
    resolved = (cwd / candidate).resolve()
    try:
        resolved.relative_to(cwd)
    except ValueError:
        raise ParallelWorkflowError(
            f"Recipe '{field}' must stay within the working directory "
            f"({cwd}); use --output-dir for a path outside it."
        ) from None
    return candidate


def _recipe_output_dir(
    recipe: dict[str, Any],
    default: Path,
    override: Path | None,
) -> Path:
    if override is not None:
        return override
    return _ensure_within_cwd(Path(str(recipe.get("output_dir") or default)), field="output_dir")


def _resolve_recipe_path(recipe_path: Path, value: Any) -> Path | None:
    if value is None:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else recipe_path.parent / path


def _required_recipe_path(recipe: dict[str, Any], recipe_path: Path, field_name: str) -> Path:
    path = _resolve_recipe_path(recipe_path, recipe.get(field_name))
    if path is None:
        raise ParallelWorkflowError(f"Recipe is missing required path field: {field_name}.")
    return path


def _required_recipe_str(recipe: dict[str, Any], field_name: str) -> str:
    value = _coerce_str(recipe.get(field_name))
    if not value:
        raise ParallelWorkflowError(f"Recipe is missing required string field: {field_name}.")
    return value


def _recipe_queries(recipe: dict[str, Any], objective: str) -> list[str]:
    queries = recipe.get("queries") or recipe.get("search_queries") or []
    if not queries:
        return [objective]
    return _string_list(queries)


def _recipe_source_policy(recipe: dict[str, Any]) -> dict[str, Any]:
    source_policy_recipe = recipe.get("source_policy") or {}
    if not isinstance(source_policy_recipe, dict):
        raise ParallelWorkflowError("Recipe field 'source_policy' must be an object when present.")
    return _build_source_policy(
        include_domains=_string_list(
            recipe.get("include_domains") or source_policy_recipe.get("include_domains")
        ),
        exclude_domains=_string_list(
            recipe.get("exclude_domains") or source_policy_recipe.get("exclude_domains")
        ),
        after_date=_coerce_str(recipe.get("after_date") or source_policy_recipe.get("after_date")),
    )


def _recipe_fetch_policy(recipe: dict[str, Any]) -> dict[str, Any]:
    fetch_policy_recipe = recipe.get("fetch_policy") or {}
    if not isinstance(fetch_policy_recipe, dict):
        raise ParallelWorkflowError("Recipe field 'fetch_policy' must be an object when present.")
    return _build_fetch_policy(
        max_age_seconds=_optional_int(
            recipe.get("fetch_max_age_seconds") or fetch_policy_recipe.get("max_age_seconds"),
            "fetch_max_age_seconds",
            min_value=600,
        ),
        timeout_seconds=_optional_int(
            recipe.get("fetch_timeout_seconds") or fetch_policy_recipe.get("timeout_seconds"),
            "fetch_timeout_seconds",
        ),
        disable_cache_fallback=bool(
            recipe.get("disable_cache_fallback") or fetch_policy_recipe.get("disable_cache_fallback", False)
        ),
    )


def _recipe_json_schema(
    recipe_path: Path,
    recipe: dict[str, Any],
    file_or_object_key: str,
    inline_key: str | None,
) -> dict[str, Any] | None:
    inline_value = _coerce_str(recipe.get(inline_key)) if inline_key else None
    raw = recipe.get(file_or_object_key)
    if isinstance(raw, dict):
        if inline_value:
            raise ParallelWorkflowError(f"Recipe cannot combine {file_or_object_key} and {inline_key}.")
        return raw
    return _load_optional_json_schema(_resolve_recipe_path(recipe_path, raw), inline_value)


def _recipe_metadata(recipe: dict[str, Any]) -> dict[str, Any]:
    raw = recipe.get("metadata")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    return _parse_metadata_pairs(_string_list(raw))


def _recipe_webhook(recipe: dict[str, Any]) -> dict[str, Any] | None:
    webhook = recipe.get("webhook")
    if isinstance(webhook, dict):
        return webhook
    return _build_webhook(
        _coerce_str(recipe.get("webhook_url")),
        _string_list(recipe.get("webhook_event_types") or []),
    )


def _enforce_cost_guard(estimated_cost: float, max_estimated_cost: float, label: str) -> None:
    if estimated_cost > max_estimated_cost:
        raise ParallelWorkflowError(
            f"Estimated {label} cost ${estimated_cost:.3f} exceeds cost guard ${max_estimated_cost:.3f}."
        )


def _run_monitor_recipe(
    recipe: dict[str, Any],
    recipe_path: Path,
    output_dir_override: Path | None,
    dry_run: bool,
    max_estimated_cost: float,
    console: Console,
) -> Path | None:
    action = _coerce_str(recipe.get("action") or recipe.get("monitor_command")) or "create"
    output_dir = _recipe_output_dir(recipe, Path("packs/parallel-monitor-pack"), output_dir_override)
    if action == "create":
        processor = _coerce_str(recipe.get("processor")) or "lite"
        estimated_cost = estimate_monitor_execution_cost(processor=processor)
        source_policy = _recipe_source_policy(recipe)
        output_schema = _recipe_json_schema(recipe_path, recipe, "output_schema", "output_schema_json")
        if dry_run:
            console.print_json(
                data={
                    "recipe": str(recipe_path),
                    "workflow": "monitor-pack",
                    "action": action,
                    "type": _coerce_str(recipe.get("type")) or "event_stream",
                    "estimated_cost_per_execution_usd": estimated_cost,
                    "max_estimated_cost_usd": max_estimated_cost,
                }
            )
            return None
        _enforce_cost_guard(estimated_cost, max_estimated_cost, "Monitor execution")
        return run_monitor_create_pack(
            query=_coerce_str(recipe.get("query")),
            monitor_type=_coerce_str(recipe.get("type")) or "event_stream",
            task_run_id=_coerce_str(recipe.get("task_run_id")),
            frequency=_coerce_str(recipe.get("frequency")) or "1d",
            processor=processor,
            output_schema=output_schema,
            include_backfill=bool(recipe.get("include_backfill", False)),
            source_policy=source_policy,
            location=_coerce_str(recipe.get("location")),
            webhook=_recipe_webhook(recipe),
            metadata=_recipe_metadata(recipe),
            output_dir=output_dir,
            estimated_cost_usd=estimated_cost,
        )
    if action == "events":
        return run_monitor_events_pack(
            monitor_id=_required_recipe_str(recipe, "monitor_id"),
            limit=_optional_int(recipe.get("limit")) or 20,
            cursor=_coerce_str(recipe.get("cursor")),
            event_group_id=_coerce_str(recipe.get("event_group_id")),
            include_completions=bool(recipe.get("include_completions", False)),
            output_dir=output_dir,
        )
    if action == "list":
        return run_monitor_list_pack(
            limit=_optional_int(recipe.get("limit")) or 20,
            cursor=_coerce_str(recipe.get("cursor")),
            statuses=_string_list(recipe.get("status") or []),
            monitor_types=_string_list(recipe.get("type") or []),
            output_dir=output_dir,
        )
    if action == "retrieve":
        return run_monitor_retrieve_pack(
            monitor_id=_required_recipe_str(recipe, "monitor_id"),
            output_dir=output_dir,
        )
    if action == "update":
        return run_monitor_update_pack(
            monitor_id=_required_recipe_str(recipe, "monitor_id"),
            query=_coerce_str(recipe.get("query")),
            frequency=_coerce_str(recipe.get("frequency")),
            source_policy=_recipe_source_policy(recipe),
            location=_coerce_str(recipe.get("location")),
            webhook=_recipe_webhook(recipe),
            clear_webhook=bool(recipe.get("clear_webhook", False)),
            metadata=_recipe_metadata(recipe),
            clear_metadata=bool(recipe.get("clear_metadata", False)),
            output_dir=output_dir,
        )
    if action == "cancel":
        return run_monitor_cancel_pack(
            monitor_id=_required_recipe_str(recipe, "monitor_id"),
            output_dir=output_dir,
        )
    if action == "trigger":
        return run_monitor_trigger_pack(
            monitor_id=_required_recipe_str(recipe, "monitor_id"),
            output_dir=output_dir,
        )
    raise ParallelWorkflowError("Unsupported monitor recipe action.")


def _load_taskgroup_inputs(path: Path, *, prompt_template: str | None = None) -> list[Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        raise ParallelWorkflowError(f"Could not read TaskGroup inputs {path}: {err}") from err
    try:
        if path.suffix.lower() == ".json":
            raw = json.loads(text)
            if not isinstance(raw, list):
                raise ParallelWorkflowError("TaskGroup JSON input must be an array.")
            rows = raw
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError as err:
        raise ParallelWorkflowError(f"Invalid TaskGroup input JSON: {err}") from err
    if prompt_template:
        return [_format_task_input(prompt_template, row) for row in rows]
    return rows


def _format_task_input(template: str, row: Any) -> str:
    if isinstance(row, dict):
        try:
            return template.format(**row)
        except KeyError as err:
            raise ParallelWorkflowError(f"Prompt template references missing key: {err}") from err
    return template.format(input=row)


def _read_text_source(source: str) -> str:
    if source.startswith(("http://", "https://")):
        return _read_remote_text_source(source)
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as err:
        raise ParallelWorkflowError(f"Could not read {source}: {err}") from err


def _read_remote_text_source(source: str) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read_remote_text_source_async(source))
    raise ParallelWorkflowError(
        "Remote api-pack sources cannot be fetched while an event loop is already running. "
        "Use a local file path or call `docpull parallel api-pack` from the CLI."
    )


async def _read_remote_text_source_async(source: str) -> str:
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(source)
    if not validation.is_valid:
        raise ParallelWorkflowError(f"Remote api-pack source rejected: {validation.rejection_reason}")

    rate_limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    async with AsyncHttpClient(
        rate_limiter=rate_limiter,
        url_validator=validator,
        default_timeout=30.0,
    ) as client:
        robots = RobotsChecker(user_agent=client.user_agent, url_validator=validator)
        if not robots.is_allowed(source):
            raise ParallelWorkflowError(
                f"Robots.txt disallows or could not verify remote api-pack source: {source}"
            )

        try:
            response = await client.get(
                source,
                headers={"Accept": "text/plain, application/json;q=0.9"},
            )
        except Exception as err:  # noqa: BLE001
            raise ParallelWorkflowError(f"Could not fetch {source}: {err}") from err

    if response.status_code >= 400:
        raise ParallelWorkflowError(f"Could not fetch {source}: HTTP {response.status_code}")
    return _decode_text_response(response.content, response.content_type)


def _decode_text_response(body: bytes, content_type: str) -> str:
    encoding = "utf-8"
    for part in content_type.split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            encoding = part.split("=", 1)[1].strip().strip("\"'") or encoding
            break
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _detect_api_pack_kind(source: str, text: str) -> str:
    stripped = text.lstrip()
    if source.endswith(".json") or stripped.startswith("{"):
        return "openapi"
    return "llms"


def _openapi_items(text: str, *, source: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as err:
        raise ParallelWorkflowError(f"Invalid OpenAPI JSON: {err}") from err
    if not isinstance(spec, dict) or "paths" not in spec:
        raise ParallelWorkflowError("OpenAPI source must be a JSON object with a paths field.")
    info_raw = spec.get("info")
    info: dict[str, Any] = info_raw if isinstance(info_raw, dict) else {}
    items: list[dict[str, Any]] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise ParallelWorkflowError("OpenAPI paths field must be an object.")
    for path, methods in sorted(paths.items()):
        if not isinstance(methods, dict):
            continue
        for method, operation in sorted(methods.items()):
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            if not isinstance(operation, dict):
                continue
            title = _coerce_str(operation.get("summary")) or f"{method.upper()} {path}"
            description = _coerce_str(operation.get("description")) or ""
            items.append(
                {
                    "name": f"{method.upper()} {path}",
                    "title": title,
                    "url": f"openapi://{method.upper()} {path}",
                    "description": description,
                    "method": method.upper(),
                    "path": path,
                    "operation_id": operation.get("operationId"),
                    "tags": operation.get("tags"),
                    "parameters": operation.get("parameters"),
                    "request_body": operation.get("requestBody"),
                    "responses": operation.get("responses"),
                }
            )
    return items, {
        "source": source,
        "kind": "openapi",
        "title": info.get("title"),
        "version": info.get("version"),
        "operation_count": len(items),
    }


_LLMS_LINK_RE = re.compile(r"-\s+\[([^\]]+)\]\(([^)]+)\)(?::\s*(.*))?")


def _llms_items(text: str, *, source: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for match in _LLMS_LINK_RE.finditer(text):
        title, url, description = match.groups()
        items.append(
            {
                "name": title.strip(),
                "title": title.strip(),
                "url": url.strip(),
                "description": (description or "").strip(),
            }
        )
    if not items:
        raise ParallelWorkflowError("llms.txt source did not contain Markdown links.")
    return items, {
        "source": source,
        "kind": "llms",
        "link_count": len(items),
    }


def _status_is_active(status: Any) -> bool:
    if isinstance(status, bool):
        return status
    active = _get(status, "is_active")
    if isinstance(active, bool):
        return active
    status_value = _coerce_str(status) or _coerce_str(_get(status, "status"))
    return status_value in {"queued", "running", "active"}


def _item_title(item: dict[str, Any], *, fallback: str) -> str:
    for key in ("name", "title", "field", "event_id", "run_id", "monitor_id", "id"):
        value = _coerce_str(_get(item, key))
        if value:
            return value
    output = _get(item, "output")
    value = _coerce_str(_get(output, "title"))
    return value or fallback


def _item_url(item: dict[str, Any]) -> str | None:
    for key in ("url", "website", "source_url"):
        value = _coerce_str(_get(item, key))
        if value:
            return value
    citations = _list(_get(item, "citations"))
    for citation in citations:
        value = _coerce_str(_get(citation, "url"))
        if value:
            return value
    return None


def _item_markdown(*, title: str, url: str, item: dict[str, Any]) -> str:
    lines = [f"# {title}", "", f"_source: {url}_", ""]
    description = _coerce_str(_get(item, "description")) or _coerce_str(_get(item, "content"))
    output = _get(item, "output")
    output_content = _coerce_str(_get(output, "content"))
    if description:
        lines.extend([description, ""])
    if output_content:
        lines.extend(["## Output", "", output_content, ""])
    raw_metadata = json.dumps(_jsonable(item), indent=2, ensure_ascii=False)
    lines.extend(["## Raw Metadata", "", "```json", raw_metadata, "```"])
    return "\n".join(lines).rstrip() + "\n"


def _select_urls(results: list[Any], limit: int) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for result in results:
        url = _coerce_str(_get(result, "url"))
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def _result_markdown(result: dict[str, Any]) -> str:
    title = _coerce_str(_get(result, "title"))
    url = _coerce_str(_get(result, "url"))
    full_content = _coerce_str(_get(result, "full_content"))
    excerpts: list[str] = [
        item for item in (_coerce_str(raw) for raw in _list(_get(result, "excerpts"))) if item
    ]
    body = full_content or "\n\n".join(excerpts)
    header = []
    if title and not body.lstrip().startswith("#"):
        header.append(f"# {title}")
    if url:
        header.append(f"_source: {url}_")
    if header:
        return "\n\n".join(header + [body]).strip() + "\n"
    return body.strip() + "\n"


def _require_result_url(result: dict[str, Any], index: int) -> str:
    url = _coerce_str(_get(result, "url"))
    if not url:
        raise ParallelWorkflowError(f"Extract result #{index} is missing a URL.")
    return url


def _safe_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "url",
        "title",
        "publish_date",
        "provider",
        "fallback_used",
        "core_profile",
        "core_status_code",
    ):
        value = _get(result, key)
        if value is not None:
            summary[key] = _jsonable(value)
    excerpts = _list(_get(result, "excerpts"))
    if excerpts:
        summary["excerpt_count"] = len(excerpts)
    if _get(result, "full_content") is not None:
        summary["has_full_content"] = True
    return summary


def _relative_path(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def _extract_task_content(task_result: Any) -> str | None:
    output = _get(task_result, "output")
    content = _get(output, "content")
    if isinstance(content, str):
        return content
    if isinstance(output, str):
        return output
    return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ParallelWorkflowError("Expected a list of non-empty strings.")
    return value


def _required_positive_int(
    value: Any,
    field_name: str,
    *,
    default: int,
    max_value: int | None = None,
    min_value: int = 1,
) -> int:
    if value is None:
        return default
    return _positive_int_value(value, field_name, max_value=max_value, min_value=min_value)


def _optional_int(
    value: Any,
    field_name: str = "value",
    *,
    max_value: int | None = None,
    min_value: int = 1,
) -> int | None:
    if value is None:
        return None
    return _positive_int_value(value, field_name, max_value=max_value, min_value=min_value)


def _positive_int_value(
    value: Any,
    field_name: str,
    *,
    max_value: int | None = None,
    min_value: int = 1,
) -> int:
    if isinstance(value, bool):
        raise ParallelWorkflowError(f"Recipe field '{field_name}' must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as err:
        raise ParallelWorkflowError(f"Recipe field '{field_name}' must be a positive integer.") from err
    if parsed < min_value:
        raise ParallelWorkflowError(f"Recipe field '{field_name}' must be at least {min_value}.")
    if max_value is not None and parsed > max_value:
        raise ParallelWorkflowError(f"Recipe field '{field_name}' must be at most {max_value}.")
    return parsed


def _coerce_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(val) for key, val in value.items()}
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if hasattr(value, "dict"):
        return _jsonable(value.dict())
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return str(value)


def _jsonable_dict(value: Any) -> dict[str, Any]:
    payload = _jsonable(value)
    if isinstance(payload, dict):
        return payload
    return {"value": payload}


def _response_items(response: Any, primary_key: str) -> list[dict[str, Any]]:
    if isinstance(response, list | tuple):
        return [_jsonable_dict(item) for item in response]
    for key in (primary_key, "items", "data", "results"):
        values = _list(_get(response, key))
        if values:
            return [_jsonable_dict(item) for item in values]
    return []


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-").lower()
    return slug[:80].strip("-") or "source"


if __name__ == "__main__":
    sys.exit(run_parallel_cli())
