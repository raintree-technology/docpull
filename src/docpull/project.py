"""Persistent DocPull project workflows.

This module keeps project mode as an orchestration layer over the existing
fetcher, pack, manifest, diff, and export primitives. A project run is also a
normal local DocPull pack, so existing pack tooling can read it directly.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import ipaddress
import json
import os
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from rich.console import Console
from rich.markup import escape

from .accounting import RunAccounting
from .context_aliases import context_alias_for_url, get_context_alias, list_context_aliases
from .conversion.chunking import TokenCounter, chunk_markdown
from .core.fetcher import Fetcher
from .exports import export_pack
from .models.config import (
    AuthConfig,
    AuthType,
    BudgetConfig,
    CacheConfig,
    CrawlConfig,
    DocpullConfig,
    OutputConfig,
    ProfileName,
)
from .models.events import EventType, SkipReason
from .pack_tools import (
    PackToolError,
    _diff_markdown,
    build_citation_map,
    diff_packs,
)
from .time_utils import utc_now, utc_now_iso

PROJECT_SCHEMA_VERSION = 1
PROJECT_INDEX_USER_VERSION = 3
PROJECT_CONFIG_FILENAME = "docpull.yaml"
PROJECT_DIRNAME = ".docpull"
CONTEXT_LOCK_FILENAME = "context.lock.json"
DEFAULT_CHUNK_TOKENS = 4000
WATCH_AD_HOC_MAX_PAGES = 1
WATCH_AD_HOC_MAX_DEPTH = 1
SEMANTIC_MODEL_ENV = "DOCPULL_SEMANTIC_DIFF_MODEL"
SEMANTIC_ENABLE_ENV = "DOCPULL_SEMANTIC_DIFF"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
SEMANTIC_REQUEST_TIMEOUT_S = 60.0
_RUN_ID_RE = re.compile(r"^[0-9A-Za-z_.-]+$")

SourceType = Literal[
    "auto",
    "html",
    "pdf",
    "markdown",
    "github",
    "sitemap",
    "openapi",
    "feed",
    "paper",
    "repo",
    "package",
    "standards",
    "dataset",
    "transcript",
    "wiki",
    "brand",
    "product",
    "styleguide",
    "visual",
    "policy",
]
OutputFormat = Literal["markdown", "ndjson", "sqlite", "context-pack"]
SemanticMode = Literal["auto", "off", "on"]
ContextTarget = Literal["cursor", "claude", "codex", "openai", "llamaindex", "langchain"]
SourceAuthType = Literal["bearer_env", "basic_env", "cookie_env", "header_env"]

CRAWL_SOURCE_TYPES: tuple[str, ...] = ("auto", "html", "pdf", "markdown", "github", "sitemap")
TYPED_PROJECT_SOURCE_TYPES: tuple[str, ...] = (
    "openapi",
    "feed",
    "paper",
    "repo",
    "package",
    "standards",
    "dataset",
    "transcript",
    "wiki",
    "brand",
    "product",
    "styleguide",
    "visual",
    "policy",
)
SOURCE_TYPES: tuple[str, ...] = (*CRAWL_SOURCE_TYPES, *TYPED_PROJECT_SOURCE_TYPES)
CONTEXT_TARGETS: tuple[str, ...] = ("cursor", "claude", "codex", "openai", "llamaindex", "langchain")
OUTPUT_FORMATS: tuple[str, ...] = ("markdown", "ndjson", "sqlite", "context-pack")

SemanticClient = Callable[[str], str]


class ProjectError(RuntimeError):
    """User-facing project workflow error."""


class ProjectSourceAuth(BaseModel):
    """Environment-backed source credential reference.

    The referenced environment variable value is resolved only at sync time and
    is never serialized into run artifacts, SQLite, or remote payloads.
    """

    type: SourceAuthType
    env: str
    policy: Literal["explicit-private", "public-token-only"] = "explicit-private"
    header_name: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("env")
    @classmethod
    def _validate_env(cls, value: str) -> str:
        env = value.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env):
            raise ValueError("auth env must be a valid environment variable name")
        return env

    @model_validator(mode="after")
    def _validate_header_name(self) -> ProjectSourceAuth:
        if self.type == "header_env" and not self.header_name:
            raise ValueError("header_env auth requires header_name")
        if self.type != "header_env" and self.header_name:
            raise ValueError("header_name is only valid for header_env auth")
        return self


class ProjectSource(BaseModel):
    """One durable source in a DocPull project."""

    name: str
    url: str
    type: SourceType = "auto"
    discover: bool = False
    discovered_urls: list[str] = Field(default_factory=list)
    discovered_at: str | None = None
    auth: ProjectSourceAuth | None = None

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = _slug(value)
        if not name:
            raise ValueError("source name must not be empty")
        return name

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        url = value.strip()
        if not url:
            raise ValueError("source url must not be empty")
        parsed = urlparse(url)
        if parsed.username or parsed.password:
            raise ValueError("source url must not contain embedded credentials")
        return url

    @model_validator(mode="after")
    def _validate_source_spec(self) -> ProjectSource:
        parsed = urlparse(self.url)
        if self.type in CRAWL_SOURCE_TYPES:
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("crawl source url must be an absolute https URL")
            return self
        if self.type not in TYPED_PROJECT_SOURCE_TYPES:
            raise ValueError(f"unsupported source type: {self.type}")
        if not _typed_project_source_spec_allowed(self.type, self.url):
            raise ValueError(f"{self.type} source is not a supported typed source spec")
        if self.auth is not None:
            raise ValueError("typed project sources do not support auth credentials yet")
        return self

    @field_validator("discovered_urls")
    @classmethod
    def _validate_discovered_urls(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []
        for item in value:
            url = item.strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"https"} or not parsed.netloc:
                raise ValueError("discovered URLs must be absolute https URLs")
            if parsed.username or parsed.password:
                raise ValueError("discovered URLs must not contain embedded credentials")
            if url not in seen:
                urls.append(url)
                seen.add(url)
        return urls


class ProjectCrawlConfig(BaseModel):
    """Crawl settings used by project sync."""

    max_pages: int | None = Field(500, ge=1)
    max_depth: int = Field(5, ge=1)
    max_concurrent: int = Field(10, ge=1)
    per_host_concurrent: int = Field(3, ge=1)
    rate_limit: float = Field(0.5, ge=0)
    include_paths: list[str] = Field(default_factory=list)
    exclude_paths: list[str] = Field(default_factory=list)
    streaming_discovery: bool = True

    model_config = {"extra": "forbid"}


class ProjectPolicyConfig(BaseModel):
    """Project source policy."""

    robots: Literal["respect"] = "respect"

    model_config = {"extra": "forbid"}


class ProjectBudgetConfig(BaseModel):
    """Project spend guard."""

    maximum_paid_cost_usd: float | None = Field(0, ge=0)

    model_config = {"extra": "forbid"}


class ProjectRefreshConfig(BaseModel):
    """Refresh preferences for project commands."""

    schedule: str = "manual"
    semantic_diff: SemanticMode = "auto"

    model_config = {"extra": "forbid"}


class ProjectCIConfig(BaseModel):
    """Context CI thresholds for project runs."""

    min_pack_score: int = Field(80, ge=0, le=100)
    min_audit_score: int = Field(80, ge=0, le=100)
    min_citation_coverage: float = Field(0.90, ge=0, le=1)
    min_context_pass_rate: float = Field(0.80, ge=0, le=1)
    min_freshdocs_pass_rate: float | None = Field(None, ge=0, le=1)
    fail_on_medium_coverage: bool = False
    max_age_days: int | None = Field(None, ge=1)
    require_rights: list[Literal["eval_generation", "redistribution", "model_training"]] = Field(
        default_factory=list
    )

    model_config = {"extra": "forbid"}

    @field_validator("require_rights")
    @classmethod
    def _dedupe_rights(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for item in value:
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        return deduped


def _default_output_formats() -> list[OutputFormat]:
    return ["markdown", "ndjson", "sqlite", "context-pack"]


class ProjectOutputsConfig(BaseModel):
    """Output formats expected from project runs."""

    formats: list[OutputFormat] = Field(default_factory=_default_output_formats)

    model_config = {"extra": "forbid"}

    @field_validator("formats")
    @classmethod
    def _dedupe_formats(cls, value: list[OutputFormat]) -> list[OutputFormat]:
        seen: set[OutputFormat] = set()
        deduped: list[OutputFormat] = []
        for item in value:
            if item not in seen:
                deduped.append(item)
                seen.add(item)
        return deduped


class ProjectConfig(BaseModel):
    """Persistent project configuration."""

    name: str
    sources: list[ProjectSource] = Field(default_factory=list)
    crawl: ProjectCrawlConfig = Field(default_factory=ProjectCrawlConfig)
    policy: ProjectPolicyConfig = Field(default_factory=ProjectPolicyConfig)
    budget: ProjectBudgetConfig = Field(default_factory=ProjectBudgetConfig)
    refresh: ProjectRefreshConfig = Field(default_factory=ProjectRefreshConfig)
    ci: ProjectCIConfig = Field(default_factory=ProjectCIConfig)
    outputs: ProjectOutputsConfig = Field(default_factory=ProjectOutputsConfig)

    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        name = _slug(value)
        if not name:
            raise ValueError("project name must not be empty")
        return name

    @model_validator(mode="after")
    def _validate_sources_unique(self) -> ProjectConfig:
        names = [source.name for source in self.sources]
        if len(names) != len(set(names)):
            raise ValueError("source names must be unique")
        urls = [source.url for source in self.sources]
        if len(urls) != len(set(urls)):
            raise ValueError("source URLs must be unique")
        return self


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved project filesystem paths."""

    root: Path
    config: Path
    state: Path
    runs: Path
    cache: Path
    manifests: Path
    exports: Path
    evals: Path
    releases: Path
    index: Path
    latest_run: Path
    remote_config: Path
    context_lock: Path


def run_init_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull init", description="Create a DocPull project")
    parser.add_argument("name", nargs="?", help="Project name")
    parser.add_argument("--source", help="Initial source URL")
    parser.add_argument("--force", action="store_true", help="Overwrite an existing docpull.yaml")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = init_project(name=args.name, source=args.source, force=args.force)
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    console.print(f"[green]Project initialized:[/green] {payload['name']} -> {payload['config']}")
    return 0


def run_add_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull add", description="Add a source to a DocPull project")
    parser.add_argument("sources", nargs="+", help="HTTPS URL, bundled alias, or typed source spec")
    parser.add_argument("--name", help="Source name")
    parser.add_argument("--type", choices=SOURCE_TYPES, default="auto")
    parser.add_argument("--discover", action="store_true", help="Discover and store source URLs now")
    parser.add_argument(
        "--auth",
        choices=["bearer-env", "basic-env", "cookie-env", "header-env"],
        help="Use a credential from an environment variable for this source",
    )
    parser.add_argument("--auth-env", help="Environment variable that provides the source credential")
    parser.add_argument(
        "--auth-policy",
        choices=["explicit-private", "public-token-only"],
        default="explicit-private",
    )
    parser.add_argument("--auth-header-name", help="Header name for --auth header-env")
    args = parser.parse_args(argv)
    console = Console()
    try:
        auth = _auth_ref_from_cli(
            auth_type=args.auth,
            env=args.auth_env,
            policy=args.auth_policy,
            header_name=args.auth_header_name,
        )
        payload = add_sources(
            args.sources,
            name=args.name,
            source_type=args.type,
            discover=args.discover,
            auth=auth,
        )
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    if len(payload["sources"]) == 1:
        source = payload["sources"][0]
        console.print(f"[green]Source added:[/green] {source['name']} -> {source['url']}")
    else:
        console.print(f"[green]Sources added:[/green] {len(payload['sources'])}")
    return 0


def run_install_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull install",
        description="Validate context dependencies and write a reproducibility lockfile",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Run `docpull sync` after validating dependencies",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = install_project(sync=args.sync)
    except ProjectError as err:
        console.print("[red]Project install error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(
            "[green]Context dependencies installed:[/green] "
            f"{payload['source_count']} sources -> {payload['lockfile']}"
        )
        for source in payload["sources"]:
            alias = source.get("alias")
            label = f"{source['name']} ({alias})" if alias else source["name"]
            console.print(f"- {escape(label)}: {escape(source['url'])}")
    return 0


def run_deps_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull deps",
        description="Show context dependency and lockfile status",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = context_dependency_status()
    except ProjectError as err:
        console.print("[red]Project deps error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(f"Project: {escape(payload['project'])}")
        console.print(f"Lockfile: {escape(payload['lock_status'])}")
        console.print(f"Last run: {escape(str(payload.get('last_run_id') or 'none'))}")
        console.print(f"Sources: {payload['source_count']}")
        for source in payload["sources"]:
            alias = source.get("alias")
            label = f"{source['name']} ({alias})" if alias else source["name"]
            lock_marker = "locked" if source.get("locked") else "unlocked"
            console.print(f"- {escape(label)} [{lock_marker}] {escape(source['url'])}")
        exports = payload.get("exports") or []
        if exports:
            console.print("Exports:")
            for item in exports:
                console.print(f"- {escape(str(item.get('target')))} -> {escape(str(item.get('output_dir')))}")
    return 0


def run_sources_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull sources",
        description="List bundled context source aliases",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="List bundled aliases")
    list_parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    console = Console()
    if args.command != "list":  # pragma: no cover - guarded by argparse
        parser.error(f"Unknown sources command: {args.command}")
    payload = list_context_sources()
    if args.json_output:
        console.print_json(data=payload)
    else:
        for item in payload["sources"]:
            console.print(
                f"[bold]{escape(item['name'])}[/bold]  {escape(item['title'])}  "
                f"{escape(item['url'])}\n  {escape(item['description'])}"
            )
    return 0


def run_sync_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull sync", description="Sync configured project sources")
    parser.add_argument("--source", help="Only sync one source name")
    parser.add_argument("--run-id", help="Explicit run ID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--update-discovery",
        action="store_true",
        help="Record that source discovery should be refreshed before future syncs",
    )
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = sync_project(
            source_name=args.source,
            run_id=args.run_id,
            dry_run=args.dry_run,
            update_discovery=args.update_discovery,
        )
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        summary = payload["summary"]
        console.print(
            "[green]Project sync:[/green] "
            f"{payload['run_id']} docs={summary['document_count']} "
            f"chunks={summary['chunk_count']} failed={summary['failed_count']}"
        )
    return 0


def run_diff_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull diff", description="Diff project runs")
    parser.add_argument("--from", dest="from_run_id", help="Older run ID")
    parser.add_argument("--to", dest="to_run_id", help="Newer run ID")
    parser.add_argument("--semantic", choices=["auto", "off", "on"], default="auto")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = diff_project(
            from_run_id=args.from_run_id,
            to_run_id=args.to_run_id,
            semantic=args.semantic,
        )
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        _print_project_diff(console, payload)
    return 0


def run_status_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull status", description="Show project status")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = project_status()
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(f"Project: {payload['project']}")
        console.print(f"Last run: {payload.get('last_run_id') or 'none'}")
        console.print(f"Sources: {payload['source_count']}")
        console.print(f"Discovered URLs: {payload['discovered_url_count']}")
        console.print(f"Documents: {payload['document_count']}")
        console.print(f"Changed since previous run: {payload['changed_since_previous_run']}")
        console.print(f"Failed URLs: {payload['failed_url_count']}")
        console.print(f"Paid/cloud routes used: {payload['paid_cloud_routes_used']}")
        console.print(f"Robots blocked: {payload['robots_blocked']}")
        console.print(f"Total size: {_format_bytes(payload['total_size_bytes'])}")
    return 0


def run_project_export_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull export context-pack",
        description="Export a project run as an agent context pack",
    )
    parser.add_argument("--target", required=True, choices=CONTEXT_TARGETS)
    parser.add_argument("--run", dest="run_id", help="Run ID to export")
    parser.add_argument("--output", "-o", type=Path, help="Output directory")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = export_context_pack(target=args.target, run_id=args.run_id, output_dir=args.output)
    except (ProjectError, PackToolError) as err:
        console.print("[red]Project export error:[/red] " + escape(str(err)))
        return 1
    console.print(f"[green]Context pack exported:[/green] {payload['target']} -> {payload['output_dir']}")
    return 0


def run_eval_set_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull eval-set", description="Generate a local eval set")
    parser.add_argument("--run", dest="run_id", help="Run ID")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output", "-o", type=Path, help="JSONL output path")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = generate_eval_set(run_id=args.run_id, limit=args.limit, output=args.output)
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    console.print(f"[green]Eval set:[/green] {payload['case_count']} cases -> {payload['path']}")
    return 0


def run_history_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull history", description="Show project run history")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = project_history(limit=args.limit)
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(f"Project: {payload['project']}")
        for item in payload["runs"]:
            console.print(
                f"- {item['run_id']} {item['status']} docs={item['document_count']} "
                f"changed={item['changed_count']} finished={item['finished_at']}"
            )
    return 0


def run_review_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull review", description="Review a project run")
    parser.add_argument("--run", dest="run_id", help="Run ID to review")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = review_project_run(run_id=args.run_id)
    except ProjectError as err:
        console.print("[red]Project error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        summary = payload["summary"]
        console.print(
            f"[green]Project review:[/green] {payload['run_id']} "
            f"docs={summary['document_count']} changed={summary['changed_count']} "
            f"failed={summary['failed_count']}"
        )
        console.print(f"Review: {payload['paths']['json']}")
    return 0


def run_release_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull release", description="Create project release artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    context = subparsers.add_parser("context-pack", help="Release a context pack")
    context.add_argument("--target", required=True, choices=CONTEXT_TARGETS)
    context.add_argument("--run", dest="run_id", help="Run ID to release")
    context.add_argument("--tag", help="Release tag")
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.command == "context-pack":
            payload = release_context_pack(target=args.target, run_id=args.run_id, tag=args.tag)
        else:
            parser.error(f"Unknown release command: {args.command}")
    except (ProjectError, PackToolError) as err:
        console.print("[red]Project release error:[/red] " + escape(str(err)))
        return 1
    console.print(f"[green]Release created:[/green] {payload['tag']} -> {payload['release_dir']}")
    return 0


def run_remote_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull remote", description="Use a hosted DocPull API")
    subparsers = parser.add_subparsers(dest="command", required=True)
    login = subparsers.add_parser("login", help="Store hosted API connection metadata")
    login.add_argument("--api-url", required=True)
    login.add_argument("--token", required=True)
    login.add_argument(
        "--allow-insecure-local-http",
        action="store_true",
        help="Allow http://localhost or loopback API URLs for local development only",
    )
    for name in ("sync", "status", "diff", "export", "release"):
        command = subparsers.add_parser(name, help=f"Call remote {name}")
        command.add_argument("--project", required=True, help="Hosted project ID")
        command.add_argument("--json", action="store_true", dest="json_output")
        if name == "export":
            command.add_argument("--target", required=True, choices=CONTEXT_TARGETS)
        if name == "release":
            command.add_argument("--target", required=True, choices=CONTEXT_TARGETS)
            command.add_argument("--run")
            command.add_argument("--tag")
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.command == "login":
            payload = remote_login(
                api_url=args.api_url,
                token=args.token,
                allow_insecure_local_http=args.allow_insecure_local_http,
            )
        else:
            payload = remote_project_call(args.command, args)
    except ProjectError as err:
        console.print("[red]Remote error:[/red] " + escape(str(err)))
        return 1
    if getattr(args, "json_output", False):
        console.print_json(data=payload)
    elif args.command == "login":
        console.print(f"[green]Remote configured:[/green] {payload['api_url']}")
    else:
        console.print(f"[green]Remote {args.command}:[/green] {payload.get('status', 'ok')}")
    return 0


def run_watch_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull watch", description="Sync, diff, and export one URL")
    parser.add_argument("url", help="HTTPS source URL")
    parser.add_argument("--export", required=True, choices=CONTEXT_TARGETS, dest="export_target")
    parser.add_argument("--alert", choices=["changes"], default="changes")
    parser.add_argument("--interval", type=float, help="Repeat locally every N seconds")
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Bound pages fetched per watch sync. Defaults to 1 for ad hoc watch projects.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        help="Bound crawl depth per watch sync. Defaults to 1 for ad hoc watch projects.",
    )
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = watch_project(
            args.url,
            export_target=args.export_target,
            alert=args.alert,
            interval_seconds=args.interval,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
        )
    except (ProjectError, PackToolError) as err:
        console.print("[red]Project watch error:[/red] " + escape(str(err)))
        return 1
    console.print(
        f"[green]Watch run:[/green] {payload['run_id']} "
        f"changed={payload['changed']} export={payload['export']['output_dir']}"
    )
    return 0


def init_project(
    *,
    name: str | None = None,
    source: str | None = None,
    force: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = (root or Path.cwd()).resolve()
    paths = project_paths(project_root)
    if paths.config.exists() and not force:
        raise ProjectError(f"{paths.config} already exists; pass --force to overwrite it")

    sources: list[ProjectSource] = []
    if source:
        try:
            sources.append(ProjectSource(name=_source_name_from_url(source), url=source, type="auto"))
        except ValidationError as err:
            raise ProjectError(f"Invalid initial source: {err}") from err
    try:
        config = ProjectConfig(name=name or project_root.name or "docpull-project", sources=sources)
    except ValidationError as err:
        raise ProjectError(f"Invalid project config: {err}") from err

    ensure_project_dirs(project_root)
    save_project_config(project_root, config)
    ensure_project_index(project_root, config)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "name": config.name,
        "config": str(paths.config),
        "project_dir": str(paths.state),
    }


def add_source(
    url: str,
    *,
    name: str | None = None,
    source_type: str = "auto",
    discover: bool = False,
    auth: ProjectSourceAuth | dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    try:
        source = ProjectSource(
            name=name or _unique_source_name(config.sources, _source_name_from_url(url)),
            url=url,
            type=_source_type(source_type),
            discover=discover,
            auth=ProjectSourceAuth.model_validate(auth) if auth else None,
        )
    except ValidationError as err:
        raise ProjectError(f"Invalid source: {err}") from err
    if discover and source.type in TYPED_PROJECT_SOURCE_TYPES:
        raise ProjectError("Typed project sources do not support discovery; sync reads the source directly.")
    if any(existing.name == source.name for existing in config.sources):
        raise ProjectError(f"Source name already exists: {source.name}")
    if any(existing.url == source.url for existing in config.sources):
        raise ProjectError(f"Source URL already exists: {source.url}")
    updated = config.model_copy(update={"sources": [*config.sources, source]})
    if discover:
        updated = _refresh_project_discovery(project_root, updated, source_names=[source.name])
    save_project_config(project_root, updated)
    ensure_project_index(project_root, updated)
    final_source = next(item for item in updated.sources if item.name == source.name)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project": updated.name,
        "source": final_source.model_dump(mode="json"),
    }


def add_sources(
    values: list[str],
    *,
    name: str | None = None,
    source_type: str = "auto",
    discover: bool = False,
    auth: ProjectSourceAuth | dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Add one or more URL or alias sources to a project."""

    if not values:
        raise ProjectError("At least one source or alias is required")
    if len(values) > 1 and name:
        raise ProjectError("--name can only be used when adding one source")
    if len(values) > 1 and auth:
        raise ProjectError("--auth can only be used when adding one source")
    project_root = find_project_root(root or Path.cwd())
    added: list[dict[str, Any]] = []
    for value in values:
        spec = _resolve_source_input(value, source_type=source_type)
        if discover and spec["type"] in TYPED_PROJECT_SOURCE_TYPES:
            raise ProjectError(
                "Typed project sources do not support --discover; sync reads the source directly."
            )
        if spec["alias"] and auth:
            raise ProjectError("Bundled aliases cannot define auth; add the resolved URL explicitly")
        payload = add_source(
            str(spec["url"]),
            name=name or str(spec["name"]),
            source_type=source_type if source_type != "auto" or not spec["type"] else str(spec["type"]),
            discover=(discover if not spec["alias"] else False),
            auth=auth,
            root=project_root,
        )
        if spec["alias"] and (discover or bool(spec["discover"])):
            config = load_project_config(project_root)
            updated_sources = [
                source.model_copy(update={"discover": True})
                if source.name == payload["source"]["name"]
                else source
                for source in config.sources
            ]
            updated_config = config.model_copy(update={"sources": updated_sources})
            save_project_config(project_root, updated_config)
            ensure_project_index(project_root, updated_config)
        source_payload = dict(payload["source"])
        if spec["alias"] and (discover or bool(spec["discover"])):
            source_payload["discover"] = True
        if spec["alias"]:
            source_payload["alias"] = spec["alias"]
            source_payload["alias_title"] = spec["title"]
        added.append(source_payload)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project": load_project_config(project_root).name,
        "sources": added,
    }


def list_context_sources() -> dict[str, Any]:
    """Return bundled context source aliases."""

    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "sources": [alias.to_dict() for alias in list_context_aliases()],
    }


def install_project(
    *,
    sync: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """Validate context dependencies and write or verify the lockfile."""

    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    ensure_project_index(project_root, config)
    existing_lock = _read_context_lock(paths.context_lock)
    if existing_lock:
        _validate_context_lock(config, existing_lock)
    payload = _write_context_lock(
        project_root=project_root,
        config=config,
        run_id=_latest_run_id(project_root),
    )
    sync_payload: dict[str, Any] | None = None
    if sync:
        sync_payload = sync_project(root=project_root)
        payload = _read_context_lock(paths.context_lock) or payload
    result = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project": config.name,
        "source_count": len(config.sources),
        "sources": [_context_dependency_payload(source) for source in config.sources],
        "lockfile": str(paths.context_lock),
        "validated_existing_lock": bool(existing_lock),
    }
    if sync_payload:
        result["sync"] = sync_payload
    if payload.get("run_id"):
        result["run_id"] = payload["run_id"]
    return result


def context_dependency_status(*, root: Path | None = None) -> dict[str, Any]:
    """Return project dependency, lockfile, and latest run status."""

    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = project_paths(project_root)
    lock = _read_context_lock(paths.context_lock)
    latest = _latest_run_id(project_root)
    latest_run = _run_payload(_run_dir_for_id(paths, latest)[1]) if latest else None
    locked_by_url: dict[str, dict[str, Any]] = {}
    if isinstance(lock, dict):
        for item in lock.get("sources", []):
            if isinstance(item, dict) and item.get("url"):
                locked_by_url[str(item["url"])] = item
    sources = []
    for source in config.sources:
        payload = _context_dependency_payload(source)
        locked = locked_by_url.get(source.url)
        payload["locked"] = bool(locked)
        if locked and locked.get("discovered_urls") != source.discovered_urls:
            payload["lock_drift"] = "discovered_urls"
        sources.append(payload)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project": config.name,
        "project_root": str(project_root),
        "source_count": len(config.sources),
        "sources": sources,
        "lockfile": str(paths.context_lock),
        "lock_status": _context_lock_status(config, lock),
        "lock_hash": lock.get("lock_hash") if isinstance(lock, dict) else None,
        "last_run_id": latest,
        "last_run_status": latest_run.get("status") if latest_run else None,
        "content_hash_summary": lock.get("content_hash_summary") if isinstance(lock, dict) else None,
        "exports": lock.get("exports", []) if isinstance(lock, dict) else [],
    }


def sync_project(
    *,
    source_name: str | None = None,
    run_id: str | None = None,
    dry_run: bool = False,
    update_discovery: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    if not config.sources:
        raise ProjectError("Project has no sources. Add one with `docpull add URL`.")
    selected = _selected_sources(config, source_name)
    discovery_refresh_names = [source.name for source in selected if source.discover]
    if update_discovery or discovery_refresh_names:
        refresh_names = [source.name for source in selected] if update_discovery else discovery_refresh_names
        config = _refresh_project_discovery(project_root, config, source_names=refresh_names)
        save_project_config(project_root, config)
        selected = _selected_sources(config, source_name)
    _validate_auth_ready(selected)
    paths = ensure_project_dirs(project_root)
    ensure_project_index(project_root, config)
    current_run_id = _safe_run_id(run_id or _new_run_id())
    run_dir = paths.runs / current_run_id
    if run_dir.exists():
        raise ProjectError(f"Run already exists: {current_run_id}")
    run_dir.mkdir(parents=True)

    started_at = utc_now_iso()
    source_health: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    skip_entries: list[dict[str, Any]] = []
    fetch_stats: dict[str, int] = {
        "pages_fetched": 0,
        "pages_failed": 0,
        "pages_skipped": 0,
        "robots_blocked": 0,
        "http_request_count": 0,
    }

    if dry_run:
        for source in selected:
            source_health.append(_source_health(source, status="planned"))
        return _finalize_project_run(
            project_root=project_root,
            config=config,
            run_id=current_run_id,
            run_dir=run_dir,
            started_at=started_at,
            status="dry_run",
            records=[],
            errors=[],
            skips=[],
            source_health=source_health,
            fetch_stats=fetch_stats,
            update_discovery=update_discovery,
        )

    for source in selected:
        source_output_dir = run_dir / "_fetch" / source.name
        source_errors: list[dict[str, Any]] = []
        source_skips: list[dict[str, Any]] = []
        try:
            result = asyncio.run(
                _sync_source(
                    project_root=project_root,
                    config=config,
                    source=source,
                    output_dir=source_output_dir,
                )
            )
        except Exception as err:  # noqa: BLE001
            source_error = {
                "source_name": source.name,
                "url": source.url,
                "error": str(err),
                "type": "source_sync_failed",
            }
            errors.append(source_error)
            source_health.append(_source_health(source, status="error", failed_count=1, last_error=str(err)))
            continue

        records.extend(result["records"])
        source_errors = result["errors"]
        source_skips = result["skips"]
        errors.extend(source_errors)
        skip_entries.extend(source_skips)
        stats = result["stats"]
        for key in fetch_stats:
            fetch_stats[key] += int(stats.get(key, 0))
        health_status = "ok" if not source_errors else "degraded"
        source_health.append(
            _source_health(
                source,
                status=health_status,
                document_count=len(result["records"]),
                failed_count=len(source_errors),
                skipped_count=len(source_skips),
                robots_blocked_count=int(stats.get("robots_blocked", 0)),
                last_error=source_errors[-1].get("error") if source_errors else None,
            )
        )

    records = _dedupe_project_records(records, config=config)
    final_counts = _record_counts_by_source(records)
    source_health = [
        {
            **item,
            "document_count": final_counts.get(str(item.get("source_name") or ""), 0),
        }
        if item.get("status") in {"ok", "degraded"}
        else item
        for item in source_health
    ]

    status = "success" if records or not errors else "failed"
    return _finalize_project_run(
        project_root=project_root,
        config=config,
        run_id=current_run_id,
        run_dir=run_dir,
        started_at=started_at,
        status=status,
        records=records,
        errors=errors,
        skips=skip_entries,
        source_health=source_health,
        fetch_stats=fetch_stats,
        update_discovery=update_discovery,
    )


def diff_project(
    *,
    from_run_id: str | None = None,
    to_run_id: str | None = None,
    semantic: str = "auto",
    semantic_client: SemanticClient | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    ensure_project_index(project_root, config)
    old_run_id, new_run_id = _resolve_diff_run_ids(project_root, from_run_id, to_run_id)
    old_run_dir = paths.runs / old_run_id
    new_run_dir = paths.runs / new_run_id
    if not old_run_dir.exists() or not new_run_dir.exists():
        raise ProjectError("Both diff runs must exist")
    try:
        base = diff_packs(old_run_dir, new_run_dir)
    except PackToolError as err:
        raise ProjectError(str(err)) from err

    old_records = _records_by_url(_read_jsonl(old_run_dir / "documents.ndjson"))
    new_records = _records_by_url(_read_jsonl(new_run_dir / "documents.ndjson"))
    likely_api = _likely_api_behavior_changes(base, old_records, new_records)
    pricing = _pricing_changes(base, old_records, new_records)
    health_delta = _source_health_delta(
        _read_json(new_run_dir / "source-health.json", default={"sources": []}),
        _read_json(old_run_dir / "source-health.json", default={"sources": []}),
    )
    semantic_payload = _semantic_diff(
        base,
        config=config,
        mode=_semantic_mode(semantic),
        client=semantic_client,
    )
    payload = {
        **base,
        "project": config.name,
        "from_run_id": old_run_id,
        "to_run_id": new_run_id,
        "summary": {
            "added_count": len(base["added_urls"]),
            "removed_count": len(base["removed_urls"]),
            "changed_count": len(base["changed_urls"]),
            "unchanged_count": len(base["unchanged_urls"]),
            "likely_api_behavior_change_count": len(likely_api),
            "pricing_change_count": len(pricing),
            "change_event_count": len(base.get("change_events") or []),
        },
        "likely_api_behavior_changes": likely_api,
        "pricing_changes": pricing,
        "source_health_delta": health_delta,
        "semantic": semantic_payload,
    }
    diff_path = new_run_dir / "project.diff.json"
    semantic_diff_path = new_run_dir / "semantic.diff.json"
    change_events_path = new_run_dir / "change.events.jsonl"
    markdown_path = new_run_dir / "PROJECT_DIFF.md"
    semantic_diff_payload = base.get("semantic_diff")
    if isinstance(semantic_diff_payload, dict):
        _write_json(semantic_diff_path, semantic_diff_payload)
    from .change_events import write_change_events

    write_change_events(change_events_path, list(payload.get("change_events") or []))
    _write_json(diff_path, payload)
    markdown_path.write_text(_project_diff_markdown(payload), encoding="utf-8")
    _index_diff(project_root, payload)
    return payload


def project_status(*, root: Path | None = None) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    ensure_project_index(project_root, config)
    latest = _latest_run_id(project_root)
    latest_run = _run_payload(paths.runs / latest) if latest else None
    diff_payload = _latest_diff_payload(paths.runs / latest) if latest else None
    failed_url_count = 0
    robots_blocked = 0
    paid_cloud_routes_used = 0
    document_count = 0
    changed_since_previous = 0
    if latest_run:
        summary = _dict_value(latest_run.get("summary"))
        document_count = _safe_int(summary.get("document_count"))
        failed_url_count = _safe_int(summary.get("failed_count"))
        robots_blocked = _safe_int(summary.get("robots_blocked"))
        paid_cloud_routes_used = _safe_int(summary.get("paid_cloud_routes_used"))
    if diff_payload:
        summary = _dict_value(diff_payload.get("summary"))
        changed_since_previous = _safe_int(summary.get("changed_count"))
    elif latest:
        previous = _previous_run_id(project_root, latest)
        if previous:
            try:
                diff_payload = diff_project(
                    from_run_id=previous,
                    to_run_id=latest,
                    semantic="off",
                    root=project_root,
                )
                changed_since_previous = _safe_int(diff_payload["summary"].get("changed_count"))
            except ProjectError:
                changed_since_previous = 0
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project": config.name,
        "project_root": str(project_root),
        "last_run_id": latest,
        "last_run_at": latest_run.get("finished_at") if latest_run else None,
        "source_count": len(config.sources),
        "sources": [_source_public_payload(source) for source in config.sources],
        "discovered_url_count": sum(len(source.discovered_urls) for source in config.sources),
        "document_count": document_count,
        "changed_since_previous_run": changed_since_previous,
        "failed_url_count": failed_url_count,
        "paid_cloud_routes_used": paid_cloud_routes_used,
        "robots_blocked": robots_blocked,
        "total_size_bytes": _directory_size(paths.state),
    }


def export_context_pack(
    *,
    target: str,
    run_id: str | None = None,
    output_dir: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    ensure_project_index(project_root, config)
    target_name = _context_target(target)
    selected_run_id = run_id or _latest_run_id(project_root)
    if not selected_run_id:
        raise ProjectError("No project run exists. Run `docpull sync` first.")
    selected_run_id, run_dir = _run_dir_for_id(paths, selected_run_id)
    if not run_dir.exists():
        raise ProjectError(f"Run does not exist: {selected_run_id}")
    out = (output_dir or (paths.exports / f"{target_name}-{selected_run_id}")).resolve()
    out.mkdir(parents=True, exist_ok=True)

    records = _read_jsonl(run_dir / "documents.ndjson")
    chunks = _read_jsonl(run_dir / "chunks.jsonl")
    citation_payload = build_citation_map(run_dir)
    sources = citation_payload.get("sources", [])

    chunks_path = out / "chunks.jsonl"
    shutil.copyfile(run_dir / "chunks.jsonl", chunks_path)
    sources_path = out / "sources.json"
    citations_path = out / "citations.json"
    context_path = out / "context.md"
    manifest_path = out / "manifest.json"
    _write_json(sources_path, {"schema_version": PROJECT_SCHEMA_VERSION, "sources": sources})
    _write_json(citations_path, citation_payload)
    context_path.write_text(
        _context_markdown(config.name, selected_run_id, chunks, sources),
        encoding="utf-8",
    )

    native_artifacts = _write_native_context_export(
        run_dir=run_dir,
        output_dir=out,
        target=target_name,
        project_name=config.name,
    )
    aliases = [
        alias.name for source in config.sources if (alias := context_alias_for_url(source.url)) is not None
    ]
    content_summary = _content_hash_summary(records)
    lock_path = project_paths(project_root).context_lock
    manifest = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project": config.name,
        "pack_name": config.name,
        "pack_version": selected_run_id,
        "run_id": selected_run_id,
        "target": target_name,
        "source_count": len(config.sources),
        "source_aliases": aliases,
        "document_count": len(records),
        "chunk_count": len(chunks),
        "content_hash_summary": content_summary,
        "context_lock": str(lock_path.relative_to(project_root)) if lock_path.exists() else None,
        "artifacts": {
            "context": context_path.name,
            "sources": sources_path.name,
            "chunks": chunks_path.name,
            "citations": citations_path.name,
            **native_artifacts,
        },
    }
    _write_json(manifest_path, manifest)
    payload = {
        **manifest,
        "output_dir": str(out),
        "manifest_path": str(manifest_path),
    }
    _write_context_lock(
        project_root=project_root,
        config=config,
        run_id=selected_run_id,
        export={
            "target": target_name,
            "run_id": selected_run_id,
            "output_dir": str(out),
            "manifest_path": str(manifest_path),
            "generated_at": manifest["generated_at"],
        },
    )
    _index_export(project_root, payload)
    return payload


def generate_eval_set(
    *,
    run_id: str | None = None,
    limit: int = 50,
    output: Path | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if limit < 1:
        raise ProjectError("--limit must be at least 1")
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    selected_run_id = run_id or _latest_run_id(project_root)
    if not selected_run_id:
        raise ProjectError("No project run exists. Run `docpull sync` first.")
    selected_run_id, run_dir = _run_dir_for_id(paths, selected_run_id)
    records = _read_jsonl(run_dir / "documents.ndjson")
    changed_urls = _eval_changed_urls(run_dir)
    selected = (
        [record for record in records if record.get("url") in changed_urls] if changed_urls else records
    )
    citation_payload = build_citation_map(run_dir)
    citation_by_url = {
        str(source.get("url")): str(source.get("citation_id"))
        for source in citation_payload.get("sources", [])
        if isinstance(source, dict) and source.get("url") and source.get("citation_id")
    }
    cases = [
        _eval_case(record, citation_by_url, changed_urls)
        for record in selected[:limit]
        if str(record.get("content") or "").strip()
    ]
    output_path = (output or (paths.evals / f"{selected_run_id}.evals.jsonl")).resolve()
    _write_jsonl(output_path, cases)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project": config.name,
        "run_id": selected_run_id,
        "case_count": len(cases),
        "path": str(output_path),
    }


def project_history(*, limit: int = 20, root: Path | None = None) -> dict[str, Any]:
    if limit < 1:
        raise ProjectError("--limit must be at least 1")
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    ensure_project_index(project_root, config)
    conn = sqlite3.connect(paths.index)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT run_id, started_at, finished_at, status, source_count, document_count,
                   chunk_count, failed_count, skipped_count, changed_count, added_count,
                   removed_count, output_dir
            FROM runs
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project": config.name,
        "runs": [dict(row) for row in rows],
    }


def review_project_run(*, run_id: str | None = None, root: Path | None = None) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    selected_candidate = run_id or _latest_run_id(project_root)
    if not selected_candidate:
        raise ProjectError("No project run exists. Run `docpull sync` first.")
    selected_run_id, run_dir = _run_dir_for_id(paths, selected_candidate)
    if not run_dir.exists():
        raise ProjectError(f"Run does not exist: {selected_run_id}")
    run_payload = _run_payload(run_dir)
    if run_payload is None:
        raise ProjectError(f"Run metadata is missing: {selected_run_id}")
    summary = dict(_dict_value(run_payload.get("summary")))
    diff_payload = _latest_diff_payload(run_dir)
    previous = _previous_run_id(project_root, selected_run_id)
    if diff_payload is None and previous:
        diff_payload = diff_project(
            from_run_id=previous,
            to_run_id=selected_run_id,
            semantic="off",
            root=project_root,
        )
    diff_summary = _dict_value(diff_payload.get("summary") if diff_payload else None)
    review_summary = {
        "document_count": _safe_int(summary.get("document_count")),
        "chunk_count": _safe_int(summary.get("chunk_count")),
        "failed_count": _safe_int(summary.get("failed_count")),
        "skipped_count": _safe_int(summary.get("skipped_count")),
        "robots_blocked": _safe_int(summary.get("robots_blocked")),
        "changed_count": _safe_int(diff_summary.get("changed_count")),
        "added_count": _safe_int(diff_summary.get("added_count")),
        "removed_count": _safe_int(diff_summary.get("removed_count")),
        "likely_api_behavior_change_count": _safe_int(diff_summary.get("likely_api_behavior_change_count")),
        "pricing_change_count": _safe_int(diff_summary.get("pricing_change_count")),
        "change_event_count": _safe_int(diff_summary.get("change_event_count")),
    }
    health = _read_json(run_dir / "source-health.json", default={"sources": []})
    errors = _read_jsonl(run_dir / "errors.jsonl")
    payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project": config.name,
        "run_id": selected_run_id,
        "previous_run_id": previous,
        "summary": review_summary,
        "sources": health.get("sources", []) if isinstance(health, dict) else [],
        "errors": errors,
        "diff": diff_payload,
        "auth": [_source_auth_public_payload(source) for source in config.sources],
    }
    json_path = run_dir / "project.review.json"
    markdown_path = run_dir / "PROJECT_REVIEW.md"
    _write_json(json_path, payload)
    markdown_path.write_text(_project_review_markdown(payload), encoding="utf-8")
    payload["paths"] = {"json": str(json_path), "markdown": str(markdown_path)}
    _index_review(project_root, payload)
    return payload


def release_context_pack(
    *,
    target: str,
    run_id: str | None = None,
    tag: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    project_root = find_project_root(root or Path.cwd())
    config = load_project_config(project_root)
    paths = ensure_project_dirs(project_root)
    selected_candidate = run_id or _latest_run_id(project_root)
    if not selected_candidate:
        raise ProjectError("No project run exists. Run `docpull sync` first.")
    selected_run_id, run_dir = _run_dir_for_id(paths, selected_candidate)
    target_name = _context_target(target)
    release_tag = _safe_release_tag(tag or f"{target_name}-{selected_run_id}")
    release_dir = paths.releases / release_tag
    if release_dir.exists():
        raise ProjectError(f"Release already exists: {release_tag}")
    release_dir.mkdir(parents=True)
    export_payload = export_context_pack(
        target=target_name,
        run_id=selected_run_id,
        output_dir=release_dir / "context-pack",
        root=project_root,
    )
    diff_payload = _latest_diff_payload(run_dir)
    review_payload = review_project_run(run_id=selected_run_id, root=project_root)
    release_payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "project": config.name,
        "tag": release_tag,
        "run_id": selected_run_id,
        "target": target_name,
        "release_dir": str(release_dir),
        "export": export_payload,
        "diff_summary": _dict_value(diff_payload.get("summary") if diff_payload else None),
        "review_summary": review_payload["summary"],
        "citations": "context-pack/citations.json",
        # Descriptive metadata, not a credential.
        "secret_handling": "Source credential values are not stored in release artifacts.",  # nosec B105
    }
    _write_json(release_dir / "release.json", release_payload)
    _index_release(project_root, release_payload)
    return release_payload


def remote_login(
    *,
    api_url: str,
    token: str,
    root: Path | None = None,
    allow_insecure_local_http: bool = False,
) -> dict[str, Any]:
    if not token.strip():
        raise ProjectError("Remote token must not be empty")
    normalized_api_url = _validate_remote_api_url(
        api_url,
        allow_insecure_local_http=allow_insecure_local_http,
    )
    paths = ensure_project_dirs(_remote_root(root))
    payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "api_url": normalized_api_url,
        "token": token,
        "allow_insecure_local_http": allow_insecure_local_http,
        "configured_at": utc_now_iso(),
    }
    _write_json(paths.remote_config, payload)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "api_url": payload["api_url"],
        "config": str(paths.remote_config),
        # Response placeholder, not a credential.
        "token": "[stored]",  # nosec B105
    }


def remote_project_call(command: str, args: Any, *, root: Path | None = None) -> dict[str, Any]:
    remote = _load_remote_config(root)
    project_id = str(args.project)
    if command == "sync":
        return _remote_json_request(remote, "POST", f"/v1/projects/{project_id}/syncs", {})
    if command == "status":
        return _remote_json_request(remote, "GET", f"/v1/projects/{project_id}", None)
    if command == "diff":
        return _remote_json_request(remote, "GET", f"/v1/projects/{project_id}/diffs/latest", None)
    if command == "export":
        return _remote_json_request(
            remote,
            "POST",
            f"/v1/projects/{project_id}/exports/context-pack",
            {"target": args.target},
        )
    if command == "release":
        return _remote_json_request(
            remote,
            "POST",
            f"/v1/projects/{project_id}/releases",
            {"target": args.target, "run_id": args.run, "tag": args.tag},
        )
    raise ProjectError(f"Unsupported remote command: {command}")


def watch_project(
    url: str,
    *,
    export_target: str,
    alert: str = "changes",
    interval_seconds: float | None = None,
    max_pages: int | None = None,
    max_depth: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    if alert != "changes":
        raise ProjectError("Only --alert changes is supported")
    if interval_seconds is not None and interval_seconds <= 0:
        raise ProjectError("--interval must be greater than 0")
    if max_pages is not None and max_pages <= 0:
        raise ProjectError("--max-pages must be greater than 0")
    if max_depth is not None and max_depth <= 0:
        raise ProjectError("--max-depth must be greater than 0")
    project_root = (root or Path.cwd()).resolve()
    if not (project_root / PROJECT_CONFIG_FILENAME).exists():
        init_project(name=_source_name_from_url(url), source=url, root=project_root)
        _configure_watch_project(
            project_root,
            max_pages=max_pages or WATCH_AD_HOC_MAX_PAGES,
            max_depth=max_depth or WATCH_AD_HOC_MAX_DEPTH,
            disable_discovery=True,
        )
    else:
        config = load_project_config(project_root)
        if not any(source.url == url for source in config.sources):
            add_source(url, root=project_root)
        if max_pages is not None or max_depth is not None:
            _configure_watch_project(
                project_root,
                max_pages=max_pages,
                max_depth=max_depth,
                disable_discovery=False,
            )

    latest_payload: dict[str, Any] | None = None
    while True:
        before = _latest_run_id(project_root)
        sync_payload = sync_project(root=project_root)
        diff_payload: dict[str, Any] | None = None
        if before:
            diff_payload = diff_project(
                from_run_id=before,
                to_run_id=sync_payload["run_id"],
                semantic="off",
                root=project_root,
            )
        export_payload = export_context_pack(
            target=export_target,
            run_id=sync_payload["run_id"],
            root=project_root,
        )
        changed = 0
        if diff_payload:
            summary = _dict_value(diff_payload.get("summary"))
            changed = (
                _safe_int(summary.get("added_count"))
                + _safe_int(summary.get("removed_count"))
                + _safe_int(summary.get("changed_count"))
            )
        latest_payload = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "run_id": sync_payload["run_id"],
            "changed": changed,
            "alert": alert,
            "sync": sync_payload,
            "diff": diff_payload,
            "export": export_payload,
        }
        if interval_seconds is None:
            return latest_payload
        time.sleep(interval_seconds)


def _configure_watch_project(
    project_root: Path,
    *,
    max_pages: int | None,
    max_depth: int | None,
    disable_discovery: bool,
) -> None:
    """Persist explicit watch bounds before the sync loop starts."""
    config = load_project_config(project_root)
    crawl_updates: dict[str, int] = {}
    if max_pages is not None:
        crawl_updates["max_pages"] = max_pages
    if max_depth is not None:
        crawl_updates["max_depth"] = max_depth
    bounded_crawl = config.crawl.model_copy(update=crawl_updates)
    sources = (
        [source.model_copy(update={"discover": False}) for source in config.sources]
        if disable_discovery
        else config.sources
    )
    save_project_config(
        project_root,
        config.model_copy(update={"crawl": bounded_crawl, "sources": sources}),
    )
    ensure_project_index(project_root)


async def _sync_source(
    *,
    project_root: Path,
    config: ProjectConfig,
    source: ProjectSource,
    output_dir: Path,
) -> dict[str, Any]:
    if source.type in TYPED_PROJECT_SOURCE_TYPES:
        return await asyncio.to_thread(
            _sync_typed_project_source,
            project_root=project_root,
            config=config,
            source=source,
            output_dir=output_dir,
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    fetch_config = _fetch_config(project_root, config, source, output_dir)
    errors: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    robots_blocked = 0
    async with Fetcher(fetch_config) as fetcher:
        if source.discovered_urls:
            urls = _unique_urls([source.url, *source.discovered_urls])
            if config.crawl.max_pages is not None:
                urls = urls[: config.crawl.max_pages]
            for url in urls:
                ctx = await fetcher.fetch_one(url, save=True)
                if ctx.error:
                    errors.append(
                        {
                            "source_name": source.name,
                            "url": url,
                            "error": ctx.error,
                            "type": "fetch_failed",
                        }
                    )
                elif ctx.should_skip:
                    if ctx.skip_code == SkipReason.ROBOTS_DISALLOWED:
                        robots_blocked += 1
                    skips.append(
                        {
                            "source_name": source.name,
                            "url": url,
                            "reason": _skip_reason_value(ctx.skip_code or ctx.skip_reason),
                            "type": "fetch_skipped",
                        }
                    )
        else:
            async for event in fetcher.run():
                event_type = getattr(event, "type", None)
                if event_type == EventType.FETCH_FAILED:
                    errors.append(
                        {
                            "source_name": source.name,
                            "url": str(getattr(event, "url", "") or ""),
                            "error": str(getattr(event, "error", "") or "fetch failed"),
                            "type": "fetch_failed",
                        }
                    )
                elif event_type == EventType.FETCH_SKIPPED:
                    skip_reason = getattr(event, "skip_reason", None)
                    if skip_reason == SkipReason.ROBOTS_DISALLOWED:
                        robots_blocked += 1
                    skips.append(
                        {
                            "source_name": source.name,
                            "url": str(getattr(event, "url", "") or ""),
                            "reason": _skip_reason_value(skip_reason),
                            "type": "fetch_skipped",
                        }
                    )
    records_path = output_dir / "documents.ndjson"
    records = _read_jsonl(records_path) if records_path.exists() else []
    normalized = [_normalize_project_record(record, source) for record in records]
    stats = {
        "pages_fetched": int(getattr(fetcher.stats, "pages_fetched", len(normalized))),
        "pages_failed": int(getattr(fetcher.stats, "pages_failed", len(errors))),
        "pages_skipped": int(getattr(fetcher.stats, "pages_skipped", len(skips))),
        "robots_blocked": robots_blocked,
        "http_request_count": int(getattr(fetcher.stats, "pages_fetched", 0))
        + int(getattr(fetcher.stats, "pages_failed", 0)),
    }
    return {
        "records": normalized,
        "errors": errors,
        "skips": skips,
        "stats": stats,
    }


def _sync_typed_project_source(
    *,
    project_root: Path,
    config: ProjectConfig,
    source: ProjectSource,
    output_dir: Path,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    max_items = config.crawl.max_pages or 50
    cache_dir = project_paths(project_root).cache / source.name / "typed"
    source_spec = source.url

    if source.type == "openapi":
        from .context_packs.openapi import build_openapi_pack

        build_openapi_pack(source_spec, output_dir=output_dir, chunk_tokens=DEFAULT_CHUNK_TOKENS)
    elif source.type == "feed":
        from .context_packs.feed import build_feed_pack

        build_feed_pack(
            source_spec,
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
        )
    elif source.type == "paper":
        from .context_packs.paper import build_paper_pack

        build_paper_pack(
            [source_spec],
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
            cache_dir=cache_dir,
        )
    elif source.type == "repo":
        from .context_packs.repo import build_repo_pack

        build_repo_pack(
            source_spec,
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
            cache_dir=cache_dir,
        )
    elif source.type == "package":
        from .context_packs.package import build_package_pack

        build_package_pack(
            source_spec,
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
            cache_dir=cache_dir,
        )
    elif source.type == "standards":
        from .context_packs.standards import build_standards_pack

        build_standards_pack(
            [source_spec],
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
            cache_dir=cache_dir,
        )
    elif source.type == "dataset":
        from .context_packs.dataset import build_dataset_pack

        build_dataset_pack(
            [source_spec],
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
        )
    elif source.type == "transcript":
        from .context_packs.transcript import build_transcript_pack

        build_transcript_pack(
            [source_spec],
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
        )
    elif source.type == "wiki":
        from .context_packs.wiki import build_wiki_pack

        build_wiki_pack(
            [source_spec],
            output_dir=output_dir,
            max_items=max_items,
            chunk_tokens=DEFAULT_CHUNK_TOKENS,
            cache_dir=cache_dir,
        )
    elif source.type == "brand":
        from .context_packs.brand import build_brand_pack

        build_brand_pack(
            source_spec,
            output_dir=output_dir,
            download_assets=False,
            max_pages=min(max_items, 6),
        )
    elif source.type == "product":
        from .context_packs.product import build_product_pack

        build_product_pack(
            source_spec,
            mode="site",
            output_dir=output_dir,
            max_pages=min(max_items, 8),
        )
    elif source.type == "styleguide":
        from .context_packs.styleguide import build_styleguide_pack

        build_styleguide_pack(
            source_spec,
            output_dir=output_dir,
            render=False,
        )
    elif source.type == "visual":
        from .context_packs.visuals import build_image_pack

        build_image_pack(
            source_spec,
            output_dir=output_dir,
            download_assets=False,
            max_assets=min(max_items, 40),
        )
    elif source.type == "policy":
        from .context_packs.policy_pack import build_policy_pack

        build_policy_pack(
            source_spec,
            output_dir=output_dir,
            max_pages=min(max_items, 16),
        )
    else:
        raise ProjectError(f"Unsupported typed project source type: {source.type}")

    records_path = output_dir / "documents.ndjson"
    records = _read_jsonl(records_path) if records_path.exists() else []
    normalized = [_normalize_project_record(record, source) for record in records]
    accounting_payload = _read_json(output_dir / "run.accounting.json", default={})
    stats = {
        "pages_fetched": len(normalized),
        "pages_failed": 0,
        "pages_skipped": 0,
        "robots_blocked": 0,
        "http_request_count": _safe_int(accounting_payload.get("http_request_count")),
    }
    return {
        "records": normalized,
        "errors": [],
        "skips": [],
        "stats": stats,
    }


def _refresh_project_discovery(
    project_root: Path,
    config: ProjectConfig,
    *,
    source_names: list[str],
) -> ProjectConfig:
    if not source_names:
        return config
    requested = set(source_names)
    updated_sources: list[ProjectSource] = []
    for source in config.sources:
        if source.name not in requested:
            updated_sources.append(source)
            continue
        if source.type in TYPED_PROJECT_SOURCE_TYPES:
            updated_sources.append(
                source.model_copy(
                    update={
                        "discover": False,
                        "discovered_urls": [],
                        "discovered_at": None,
                    }
                )
            )
            continue
        urls = asyncio.run(_discover_source_urls(project_root=project_root, config=config, source=source))
        updated_sources.append(
            source.model_copy(
                update={
                    "discover": True,
                    "discovered_urls": urls,
                    "discovered_at": utc_now_iso(),
                }
            )
        )
    return config.model_copy(update={"sources": updated_sources})


async def _discover_source_urls(
    *,
    project_root: Path,
    config: ProjectConfig,
    source: ProjectSource,
) -> list[str]:
    output_dir = project_paths(project_root).cache / "discovery" / source.name
    discover_config = _fetch_config(project_root, config, source, output_dir)
    async with Fetcher(discover_config) as fetcher:
        try:
            urls = await fetcher.discover()
        except Exception as err:  # noqa: BLE001
            raise ProjectError(f"Discovery failed for {source.name}: {err}") from err
    return _unique_urls(urls)


def _unique_urls(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        url = value.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(url)
    return output


def _fetch_config(
    project_root: Path,
    config: ProjectConfig,
    source: ProjectSource,
    output_dir: Path,
) -> DocpullConfig:
    return DocpullConfig(
        profile=ProfileName.CUSTOM,
        url=source.url,
        crawl=CrawlConfig(
            max_pages=config.crawl.max_pages,
            max_depth=config.crawl.max_depth,
            max_concurrent=config.crawl.max_concurrent,
            per_host_concurrent=config.crawl.per_host_concurrent,
            rate_limit=config.crawl.rate_limit,
            include_paths=config.crawl.include_paths,
            exclude_paths=config.crawl.exclude_paths,
            streaming_discovery=config.crawl.streaming_discovery,
        ),
        output=OutputConfig(
            directory=output_dir,
            format="ndjson",
            ndjson_filename="documents.ndjson",
            naming_strategy="hierarchical",
        ),
        cache=CacheConfig(
            enabled=True,
            directory=project_paths(project_root).cache / source.name,
            skip_unchanged=False,
        ),
        budget=BudgetConfig(maximum_paid_cost_usd=config.budget.maximum_paid_cost_usd),
        auth=_resolve_source_auth(source),
    )


def _finalize_project_run(
    *,
    project_root: Path,
    config: ProjectConfig,
    run_id: str,
    run_dir: Path,
    started_at: str,
    status: str,
    records: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    skips: list[dict[str, Any]],
    source_health: list[dict[str, Any]],
    fetch_stats: dict[str, int],
    update_discovery: bool,
) -> dict[str, Any]:
    paths = project_paths(project_root)
    finished_at = utc_now_iso()
    source_entries = _write_run_sources(run_dir, records)
    chunks = _build_chunks(records)
    _write_jsonl(run_dir / "documents.jsonl", records)
    _write_jsonl(run_dir / "documents.ndjson", records)
    _write_jsonl(run_dir / "chunks.jsonl", chunks)
    _write_jsonl(run_dir / "errors.jsonl", errors)
    _write_json(
        run_dir / "source-health.json",
        {"schema_version": PROJECT_SCHEMA_VERSION, "sources": source_health},
    )
    manifest = _run_manifest(
        config=config,
        run_id=run_id,
        status=status,
        records=records,
        chunks=chunks,
        source_entries=source_entries,
        started_at=started_at,
        finished_at=finished_at,
        update_discovery=update_discovery,
    )
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "corpus.manifest.json", _corpus_manifest(manifest, records, source_entries))
    (run_dir / "sources.md").write_text(_sources_markdown(source_entries), encoding="utf-8")
    _write_json(run_dir / "local.pack.json", _local_pack(config, records, source_entries))
    accounting = _run_accounting(config, fetch_stats)
    _write_json(run_dir / "accounting.json", accounting)
    _write_json(paths.manifests / f"{run_id}.json", manifest)
    lock_payload = _write_context_lock(
        project_root=project_root,
        config=config,
        run_id=run_id if status != "dry_run" else None,
        records=records,
    )

    run_payload = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "project": config.name,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "summary": {
            "source_count": len(source_health),
            "document_count": len(records),
            "chunk_count": len(chunks),
            "failed_count": len(errors),
            "skipped_count": len(skips),
            "robots_blocked": sum(_safe_int(item.get("robots_blocked_count")) for item in source_health),
            "paid_cloud_routes_used": 0,
        },
        "artifacts": {
            "run": "run.json",
            "documents_jsonl": "documents.jsonl",
            "chunks_jsonl": "chunks.jsonl",
            "manifest": "manifest.json",
            "errors": "errors.jsonl",
            "accounting": "accounting.json",
            "source_health": "source-health.json",
            "documents_ndjson": "documents.ndjson",
            "corpus_manifest": "corpus.manifest.json",
            "sources": "sources.md",
            "pack": "local.pack.json",
            "context_lock": str(paths.context_lock.relative_to(project_root)),
        },
    }
    run_payload["context_lock"] = str(paths.context_lock)
    run_payload["context_lock_hash"] = lock_payload.get("lock_hash")
    if status != "dry_run":
        from .agent_publish import AgentPublishError, publish_agent_docs
        from .pack_tools import PackToolError

        try:
            publish_payload = publish_agent_docs(run_dir)
        except (AgentPublishError, PackToolError) as err:
            run_payload["agent_publish"] = {"status": "error", "error": str(err)}
        else:
            run_payload["agent_publish"] = {
                "status": "completed",
                "artifacts": publish_payload.get("artifacts", {}),
            }
    _write_json(run_dir / "run.json", run_payload)
    if status != "dry_run":
        paths.latest_run.write_text(run_id + "\n", encoding="utf-8")
    _index_run(project_root, config, run_payload, records, chunks, errors, source_health)
    return run_payload


def project_paths(root: Path) -> ProjectPaths:
    project_root = root.resolve()
    state = project_root / PROJECT_DIRNAME
    return ProjectPaths(
        root=project_root,
        config=project_root / PROJECT_CONFIG_FILENAME,
        state=state,
        runs=state / "runs",
        cache=state / "cache",
        manifests=state / "manifests",
        exports=state / "exports",
        evals=state / "evals",
        releases=state / "releases",
        index=state / "index.sqlite",
        latest_run=state / "latest-run",
        remote_config=state / "remote.json",
        context_lock=state / CONTEXT_LOCK_FILENAME,
    )


def ensure_project_dirs(root: Path) -> ProjectPaths:
    paths = project_paths(root)
    for directory in (
        paths.state,
        paths.runs,
        paths.cache,
        paths.manifests,
        paths.exports,
        paths.evals,
        paths.releases,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def find_project_root(start: Path) -> Path:
    current = start.resolve()
    for directory in (current, *current.parents):
        if (directory / PROJECT_CONFIG_FILENAME).exists():
            return directory
    raise ProjectError("No docpull.yaml found. Run `docpull init` first.")


def load_project_config(root: Path) -> ProjectConfig:
    paths = project_paths(root)
    if not paths.config.exists():
        raise ProjectError("No docpull.yaml found. Run `docpull init` first.")
    try:
        raw = yaml.safe_load(paths.config.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as err:
        raise ProjectError(f"Invalid {PROJECT_CONFIG_FILENAME}: {err}") from err
    if not isinstance(raw, dict):
        raise ProjectError(f"{PROJECT_CONFIG_FILENAME} must contain a YAML object")
    raw = dict(raw)
    raw["sources"] = _coerce_sources(raw.get("sources", []))
    try:
        return ProjectConfig.model_validate(raw)
    except Exception as err:  # noqa: BLE001
        raise ProjectError(f"Invalid project config: {err}") from err


def save_project_config(root: Path, config: ProjectConfig) -> None:
    paths = project_paths(root)
    paths.config.write_text(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )


def ensure_project_index(root: Path, config: ProjectConfig | None = None) -> Path:
    paths = ensure_project_dirs(root)
    conn = sqlite3.connect(paths.index)
    try:
        current_version = conn.execute("PRAGMA user_version").fetchone()[0]
        if int(current_version) < PROJECT_INDEX_USER_VERSION:
            _create_index_schema(conn)
            conn.execute(f"PRAGMA user_version = {PROJECT_INDEX_USER_VERSION}")
        if config is not None:
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO projects (name, config_path, project_dir, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    config_path=excluded.config_path,
                    project_dir=excluded.project_dir,
                    updated_at=excluded.updated_at
                """,
                (config.name, str(paths.config), str(paths.state), now, now),
            )
            for source in config.sources:
                conn.execute(
                    """
                    INSERT INTO sources
                    (source_name, url, source_type, discover, discovered_count, discovered_at,
                     auth_type, auth_policy, auth_ready, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_name) DO UPDATE SET
                        url=excluded.url,
                        source_type=excluded.source_type,
                        discover=excluded.discover,
                        discovered_count=excluded.discovered_count,
                        discovered_at=excluded.discovered_at,
                        auth_type=excluded.auth_type,
                        auth_policy=excluded.auth_policy,
                        auth_ready=excluded.auth_ready,
                        updated_at=excluded.updated_at
                    """,
                    (
                        source.name,
                        source.url,
                        source.type,
                        int(source.discover),
                        len(source.discovered_urls),
                        source.discovered_at,
                        _source_auth_type(source),
                        source.auth.policy if source.auth else "none",
                        int(_source_auth_ready(source)),
                        now,
                    ),
                )
            if config.sources:
                placeholders = ",".join("?" for _source in config.sources)
                conn.execute(
                    # Placeholders are generated from source count; values are bound separately.
                    f"DELETE FROM sources WHERE source_name NOT IN ({placeholders})",  # nosec B608
                    tuple(source.name for source in config.sources),
                )
            else:
                conn.execute("DELETE FROM sources")
        conn.commit()
    finally:
        conn.close()
    return paths.index


def _create_index_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            name TEXT PRIMARY KEY,
            config_path TEXT NOT NULL,
            project_dir TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources (
            source_name TEXT PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            discover INTEGER NOT NULL DEFAULT 0,
            discovered_count INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT,
            auth_type TEXT NOT NULL DEFAULT 'none',
            auth_policy TEXT NOT NULL DEFAULT 'none',
            auth_ready INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            source_count INTEGER NOT NULL,
            document_count INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            changed_count INTEGER NOT NULL DEFAULT 0,
            added_count INTEGER NOT NULL DEFAULT 0,
            removed_count INTEGER NOT NULL DEFAULT 0,
            output_dir TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            chunk_id TEXT,
            url TEXT NOT NULL,
            canonical_url TEXT,
            title TEXT,
            content_hash TEXT NOT NULL,
            source_type TEXT,
            license_hint TEXT,
            fetched_at TEXT,
            text_path TEXT,
            source_name TEXT,
            metadata_json TEXT,
            extraction_json TEXT,
            token_count INTEGER
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_project_documents_unique
            ON documents(run_id, document_id, IFNULL(chunk_id, ''));
        CREATE TABLE IF NOT EXISTS chunks (
            run_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            url TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            chunk_heading TEXT,
            token_count INTEGER,
            content_hash TEXT NOT NULL,
            PRIMARY KEY (run_id, chunk_id)
        );
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            source_name TEXT,
            url TEXT,
            error TEXT,
            reason TEXT,
            code TEXT
        );
        CREATE TABLE IF NOT EXISTS diffs (
            diff_id TEXT PRIMARY KEY,
            from_run_id TEXT NOT NULL,
            to_run_id TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            added_count INTEGER NOT NULL,
            removed_count INTEGER NOT NULL,
            changed_count INTEGER NOT NULL,
            pricing_count INTEGER NOT NULL,
            api_behavior_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            target TEXT NOT NULL,
            output_dir TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            artifact_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_health (
            run_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            auth_type TEXT NOT NULL DEFAULT 'none',
            auth_policy TEXT NOT NULL DEFAULT 'none',
            auth_ready INTEGER NOT NULL DEFAULT 1,
            discovered_url_count INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT,
            document_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            robots_blocked_count INTEGER NOT NULL,
            last_error TEXT,
            PRIMARY KEY (run_id, source_name)
        );
        CREATE TABLE IF NOT EXISTS reviews (
            run_id TEXT PRIMARY KEY,
            generated_at TEXT NOT NULL,
            changed_count INTEGER NOT NULL,
            failed_count INTEGER NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS releases (
            tag TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            target TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            release_dir TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """
    )
    _ensure_sqlite_columns(
        conn,
        "sources",
        {
            "discovered_count": "INTEGER NOT NULL DEFAULT 0",
            "discovered_at": "TEXT",
            "auth_type": "TEXT NOT NULL DEFAULT 'none'",
            "auth_policy": "TEXT NOT NULL DEFAULT 'none'",
            "auth_ready": "INTEGER NOT NULL DEFAULT 1",
        },
    )
    _ensure_sqlite_columns(
        conn,
        "documents",
        {
            "canonical_url": "TEXT",
            "license_hint": "TEXT",
        },
    )
    _ensure_sqlite_columns(
        conn,
        "source_health",
        {
            "auth_type": "TEXT NOT NULL DEFAULT 'none'",
            "auth_policy": "TEXT NOT NULL DEFAULT 'none'",
            "auth_ready": "INTEGER NOT NULL DEFAULT 1",
            "discovered_url_count": "INTEGER NOT NULL DEFAULT 0",
            "discovered_at": "TEXT",
        },
    )


def _ensure_sqlite_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _index_run(
    project_root: Path,
    config: ProjectConfig,
    run_payload: dict[str, Any],
    records: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    source_health: list[dict[str, Any]],
) -> None:
    paths = project_paths(project_root)
    ensure_project_index(project_root, config)
    summary = _dict_value(run_payload.get("summary"))
    conn = sqlite3.connect(paths.index)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO runs
            (run_id, started_at, finished_at, status, source_count, document_count,
             chunk_count, failed_count, skipped_count, output_dir)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_payload["run_id"],
                run_payload["started_at"],
                run_payload["finished_at"],
                run_payload["status"],
                _safe_int(summary.get("source_count")),
                _safe_int(summary.get("document_count")),
                _safe_int(summary.get("chunk_count")),
                _safe_int(summary.get("failed_count")),
                _safe_int(summary.get("skipped_count")),
                str(paths.runs / str(run_payload["run_id"])),
            ),
        )
        for record in records:
            metadata = _dict_value(record.get("metadata"))
            extraction = _dict_value(record.get("extraction"))
            conn.execute(
                """
                INSERT OR REPLACE INTO documents
                (run_id, document_id, chunk_id, url, canonical_url, title, content_hash, source_type,
                 license_hint, fetched_at, text_path, source_name, metadata_json, extraction_json,
                 token_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_payload["run_id"],
                    str(record.get("document_id") or ""),
                    record.get("chunk_id"),
                    str(record.get("url") or ""),
                    record.get("canonical_url"),
                    record.get("title"),
                    str(record.get("content_hash") or ""),
                    record.get("source_type"),
                    record.get("license_hint"),
                    record.get("fetched_at"),
                    record.get("text_path"),
                    metadata.get("docpull_project_source"),
                    json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                    json.dumps(extraction, ensure_ascii=False, sort_keys=True),
                    record.get("token_count") if isinstance(record.get("token_count"), int) else None,
                ),
            )
        for chunk in chunks:
            conn.execute(
                """
                INSERT OR REPLACE INTO chunks
                (run_id, chunk_id, document_id, url, chunk_index, chunk_heading, token_count, content_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_payload["run_id"],
                    str(chunk["chunk_id"]),
                    str(chunk["document_id"]),
                    str(chunk["url"]),
                    _safe_int(chunk.get("chunk_index")),
                    chunk.get("chunk_heading"),
                    chunk.get("token_count") if isinstance(chunk.get("token_count"), int) else None,
                    str(chunk["content_hash"]),
                ),
            )
        for item in errors:
            conn.execute(
                """
                INSERT INTO errors (run_id, source_name, url, error, reason, code)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_payload["run_id"],
                    item.get("source_name"),
                    item.get("url"),
                    item.get("error"),
                    item.get("reason"),
                    item.get("code"),
                ),
            )
        for health in source_health:
            conn.execute(
                """
                INSERT OR REPLACE INTO source_health
                (run_id, source_name, url, status, auth_type, auth_policy, auth_ready,
                 discovered_url_count, discovered_at,
                 document_count, failed_count, skipped_count, robots_blocked_count, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_payload["run_id"],
                    health["source_name"],
                    health["url"],
                    health["status"],
                    health.get("auth_type", "none"),
                    health.get("auth_policy", "none"),
                    int(bool(health.get("auth_ready", True))),
                    _safe_int(health.get("discovered_url_count")),
                    health.get("discovered_at"),
                    _safe_int(health.get("document_count")),
                    _safe_int(health.get("failed_count")),
                    _safe_int(health.get("skipped_count")),
                    _safe_int(health.get("robots_blocked_count")),
                    health.get("last_error"),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _index_diff(project_root: Path, payload: dict[str, Any]) -> None:
    paths = project_paths(project_root)
    summary = _dict_value(payload.get("summary"))
    diff_id = _stable_id("diff", str(payload["from_run_id"]), str(payload["to_run_id"]))
    conn = sqlite3.connect(paths.index)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO diffs
            (diff_id, from_run_id, to_run_id, generated_at, added_count, removed_count,
             changed_count, pricing_count, api_behavior_count, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diff_id,
                payload["from_run_id"],
                payload["to_run_id"],
                payload["generated_at"],
                _safe_int(summary.get("added_count")),
                _safe_int(summary.get("removed_count")),
                _safe_int(summary.get("changed_count")),
                _safe_int(summary.get("pricing_change_count")),
                _safe_int(summary.get("likely_api_behavior_change_count")),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.execute(
            "UPDATE runs SET changed_count=?, added_count=?, removed_count=? WHERE run_id=?",
            (
                _safe_int(summary.get("changed_count")),
                _safe_int(summary.get("added_count")),
                _safe_int(summary.get("removed_count")),
                payload["to_run_id"],
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _index_export(project_root: Path, payload: dict[str, Any]) -> None:
    paths = project_paths(project_root)
    conn = sqlite3.connect(paths.index)
    try:
        conn.execute(
            """
            INSERT INTO exports (run_id, target, output_dir, generated_at, artifact_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload["run_id"],
                payload["target"],
                payload["output_dir"],
                payload["generated_at"],
                json.dumps(payload.get("artifacts", {}), ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _index_review(project_root: Path, payload: dict[str, Any]) -> None:
    paths = project_paths(project_root)
    summary = _dict_value(payload.get("summary"))
    conn = sqlite3.connect(paths.index)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO reviews
            (run_id, generated_at, changed_count, failed_count, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                payload["run_id"],
                payload["generated_at"],
                _safe_int(summary.get("changed_count")),
                _safe_int(summary.get("failed_count")),
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _index_release(project_root: Path, payload: dict[str, Any]) -> None:
    paths = project_paths(project_root)
    conn = sqlite3.connect(paths.index)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO releases
            (tag, run_id, target, generated_at, release_dir, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload["tag"],
                payload["run_id"],
                payload["target"],
                payload["generated_at"],
                payload["release_dir"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _coerce_sources(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProjectError("sources must be a list")
    output: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for item in value:
        if isinstance(item, str):
            name = _unique_name(used_names, _source_name_from_url(item))
            output.append({"name": name, "url": item, "type": "auto"})
            used_names.add(name)
            continue
        if isinstance(item, dict):
            entry = dict(item)
            raw_url = entry.get("url")
            if not isinstance(raw_url, str):
                raise ProjectError("source entries must include a URL")
            raw_name = entry.get("name")
            if raw_name:
                name = _slug(str(raw_name))
            else:
                name = _unique_name(used_names, _source_name_from_url(raw_url))
            entry["name"] = name
            entry.setdefault("type", "auto")
            output.append(entry)
            used_names.add(name)
            continue
        raise ProjectError("sources entries must be URLs or objects")
    return output


def _selected_sources(config: ProjectConfig, source_name: str | None) -> list[ProjectSource]:
    if not source_name:
        return list(config.sources)
    normalized = _slug(source_name)
    for source in config.sources:
        if source.name == normalized:
            return [source]
    raise ProjectError(f"Unknown source: {source_name}")


def _auth_ref_from_cli(
    *,
    auth_type: str | None,
    env: str | None,
    policy: str,
    header_name: str | None,
) -> ProjectSourceAuth | None:
    if not auth_type:
        if env or header_name:
            raise ProjectError("--auth-env and --auth-header-name require --auth")
        return None
    if not env:
        raise ProjectError("--auth requires --auth-env")
    try:
        return ProjectSourceAuth.model_validate(
            {
                "type": auth_type.replace("-", "_"),
                "env": env,
                "policy": policy,
                "header_name": header_name,
            }
        )
    except ValidationError as err:
        raise ProjectError(f"Invalid source auth: {err}") from err


def _validate_auth_ready(sources: list[ProjectSource]) -> None:
    missing = [source for source in sources if source.auth and not os.environ.get(source.auth.env)]
    if missing:
        names = ", ".join(f"{source.name} ({source.auth.env})" for source in missing if source.auth)
        raise ProjectError(f"Missing source auth environment variable(s): {names}")


def _resolve_source_auth(source: ProjectSource) -> AuthConfig:
    if source.auth is None:
        return AuthConfig()
    value = os.environ.get(source.auth.env)
    if not value:
        raise ProjectError(f"Missing source auth environment variable: {source.auth.env}")
    payload: dict[str, Any] = {"policy": source.auth.policy}
    if source.auth.type == "bearer_env":
        payload.update({"type": AuthType.BEARER, "token": value})
    elif source.auth.type == "basic_env":
        if ":" not in value:
            raise ProjectError(f"{source.auth.env} must contain USER:PASS for basic_env auth")
        username, password = value.split(":", 1)
        payload.update({"type": AuthType.BASIC, "username": username, "password": password})
    elif source.auth.type == "cookie_env":
        payload.update({"type": AuthType.COOKIE, "cookie": value})
    elif source.auth.type == "header_env":
        payload.update(
            {
                "type": AuthType.HEADER,
                "header_name": source.auth.header_name,
                "header_value": value,
            }
        )
    else:
        raise ProjectError(f"Unsupported source auth type: {source.auth.type}")
    try:
        return AuthConfig.model_validate(payload)
    except ValidationError as err:
        raise ProjectError(f"Invalid resolved source auth for {source.name}: {err}") from err


def _resolve_source_input(value: str, *, source_type: str = "auto") -> dict[str, Any]:
    text = value.strip()
    requested_type = _source_type(source_type)
    inferred_type = _infer_typed_source_type(text)
    if requested_type in TYPED_PROJECT_SOURCE_TYPES or (
        requested_type == "auto" and inferred_type is not None
    ):
        resolved_type = requested_type if requested_type != "auto" else inferred_type
        return {
            "alias": None,
            "title": None,
            "name": _source_name_from_url(text),
            "url": text,
            "type": resolved_type,
            "discover": False,
        }
    parsed = urlparse(text)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme != "https" or not parsed.netloc:
            raise ProjectError("Sources must be bundled aliases or absolute https URLs")
        return {
            "alias": None,
            "title": None,
            "name": _source_name_from_url(text),
            "url": text,
            "type": requested_type,
            "discover": False,
        }
    alias = get_context_alias(_slug(text))
    if not alias:
        raise ProjectError(f"Unknown context alias: {value}. Run `docpull sources list`.")
    return {
        "alias": alias.name,
        "title": alias.title,
        "name": alias.name,
        "url": alias.url,
        "type": alias.source_type,
        "discover": alias.discover,
    }


def _infer_typed_source_type(value: str) -> str | None:
    text = value.strip()
    lowered = text.lower()
    parsed = urlparse(text)
    if lowered.startswith(("wiki:", "wikipedia:")):
        return "wiki"
    if lowered.startswith(("npm:", "pypi:")):
        return "package"
    if lowered.startswith(("arxiv:", "doi:", "pmid:")):
        return "paper"
    if lowered.startswith(("rfc:", "ietf:", "w3c:", "whatwg:")):
        return "standards"
    if parsed.scheme and parsed.netloc:
        if _is_github_repo_url(parsed):
            return "repo"
        if _is_wiki_page_url(parsed):
            return "wiki"
        return None
    suffix = Path(text).suffix.lower()
    if suffix in {".csv", ".tsv", ".db", ".sqlite", ".sqlite3", ".parquet"}:
        return "dataset"
    if suffix in {".vtt", ".srt"}:
        return "transcript"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:@\S+)?", text):
        return "repo"
    return None


def _typed_project_source_spec_allowed(source_type: str, value: str) -> bool:
    text = value.strip()
    lowered = text.lower()
    parsed = urlparse(text)
    is_https = parsed.scheme == "https" and bool(parsed.netloc)
    if source_type in {"openapi", "feed", "transcript"}:
        return is_https or bool(text)
    if source_type == "paper":
        return is_https or lowered.startswith(("arxiv:", "doi:", "pmid:")) or bool(Path(text).suffix)
    if source_type == "repo":
        return _is_github_repo_url(parsed) or bool(
            re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:@\S+)?", text)
        )
    if source_type == "package":
        return lowered.startswith(("npm:", "pypi:"))
    if source_type == "standards":
        return is_https or lowered.startswith(("rfc:", "ietf:", "w3c:", "whatwg:"))
    if source_type == "dataset":
        return not parsed.scheme
    if source_type == "wiki":
        return _is_wiki_page_url(parsed) or lowered.startswith(("wiki:", "wikipedia:"))
    if source_type in {"brand", "product", "styleguide", "visual", "policy"}:
        return is_https or bool(re.fullmatch(r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text))
    return False


def _is_github_repo_url(parsed: Any) -> bool:
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 2


def _is_wiki_page_url(parsed: Any) -> bool:
    if parsed.scheme != "https" or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    if not (
        host == "www.mediawiki.org"
        or host == "mediawiki.org"
        or host.endswith(".wikipedia.org")
        or host.endswith(".wikimedia.org")
        or host.endswith(".wiktionary.org")
        or host.endswith(".wikibooks.org")
        or host.endswith(".wikiquote.org")
        or host.endswith(".wikivoyage.org")
        or host.endswith(".wikiversity.org")
        or host.endswith(".wikisource.org")
        or host.endswith(".wikinews.org")
    ):
        return False
    return bool(re.match(r"^/(wiki|w/rest\.php/v1/page)/", parsed.path))


def _context_dependency_payload(source: ProjectSource) -> dict[str, Any]:
    alias = context_alias_for_url(source.url)
    payload = {
        "name": source.name,
        "url": source.url,
        "type": source.type,
        "discover": source.discover,
        "discovered_urls": list(source.discovered_urls),
        "discovered_at": source.discovered_at,
        "auth": _source_auth_public_payload(source),
    }
    if alias:
        payload["alias"] = alias.name
        payload["alias_title"] = alias.title
        payload["alias_homepage"] = alias.homepage
    return payload


def _content_hash_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    hashes = sorted(str(record.get("content_hash")) for record in records if record.get("content_hash"))
    digest = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest() if hashes else None
    return {
        "algorithm": "sha256",
        "hash": digest,
        "record_count": len(records),
        "content_hash_count": len(hashes),
    }


def _read_run_records_for_lock(project_root: Path, run_id: str | None) -> list[dict[str, Any]]:
    if not run_id:
        return []
    paths = project_paths(project_root)
    try:
        _selected_run_id, run_dir = _run_dir_for_id(paths, run_id)
    except ProjectError:
        return []
    records_path = run_dir / "documents.jsonl"
    return _read_jsonl(records_path) if records_path.exists() else []


def _write_context_lock(
    *,
    project_root: Path,
    config: ProjectConfig,
    run_id: str | None,
    records: list[dict[str, Any]] | None = None,
    export: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from . import __version__

    paths = ensure_project_dirs(project_root)
    previous = _read_context_lock(paths.context_lock)
    current_records = records if records is not None else _read_run_records_for_lock(project_root, run_id)
    exports: list[dict[str, Any]] = []
    if isinstance(previous, dict) and isinstance(previous.get("exports"), list):
        exports = [item for item in previous["exports"] if isinstance(item, dict)]
    if export:
        exports = [
            item
            for item in exports
            if item.get("target") != export.get("target") or item.get("run_id") != export.get("run_id")
        ]
        exports.append(export)
        exports.sort(key=lambda item: (str(item.get("target") or ""), str(item.get("run_id") or "")))
    source_urls: list[str] = []
    source_specs: list[str] = []
    for source in config.sources:
        source_specs.append(source.url)
        parsed = urlparse(source.url)
        if parsed.scheme == "https" and parsed.netloc:
            source_urls.append(source.url)
            source_urls.extend(source.discovered_urls)
    payload: dict[str, Any] = {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "docpull_version": __version__,
        "project": config.name,
        "run_id": run_id,
        "sources": [_context_dependency_payload(source) for source in config.sources],
        "source_urls": _unique_urls(source_urls),
        "source_specs": _unique_urls(source_specs),
        "content_hashes": sorted(
            str(record.get("content_hash")) for record in current_records if record.get("content_hash")
        ),
        "content_hash_summary": _content_hash_summary(current_records),
        "exports": exports,
    }
    lock_hash_payload = dict(payload)
    lock_hash_payload.pop("generated_at", None)
    lock_hash_payload.pop("docpull_version", None)
    payload["lock_hash"] = hashlib.sha256(
        json.dumps(lock_hash_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    _write_json(paths.context_lock, payload)
    return payload


def _read_context_lock(path: Path) -> dict[str, Any] | None:
    value = _read_json(path, default=None)
    return value if isinstance(value, dict) else None


def _validate_context_lock(config: ProjectConfig, lock: dict[str, Any]) -> None:
    locked_sources = lock.get("sources")
    if not isinstance(locked_sources, list):
        raise ProjectError("Invalid context lockfile: sources must be a list")
    current = []
    for source in config.sources:
        alias = context_alias_for_url(source.url)
        current.append(
            {
                "name": source.name,
                "url": source.url,
                "type": source.type,
                "discover": source.discover,
                "discovered_urls": list(source.discovered_urls),
                "alias": alias.name if alias else None,
            }
        )
    locked = [
        {
            "name": str(item.get("name")),
            "url": str(item.get("url")),
            "type": str(item.get("type")),
            "discover": bool(item.get("discover")),
            "discovered_urls": list(item.get("discovered_urls") or []),
            "alias": item.get("alias"),
        }
        for item in locked_sources
        if isinstance(item, dict)
    ]
    if current != locked:
        raise ProjectError(
            "docpull.yaml diverges from .docpull/context.lock.json; run `docpull sync` "
            "or update dependencies intentionally"
        )
    for item in locked:
        alias_name = item.get("alias")
        if alias_name and get_context_alias(str(alias_name)) is None:
            raise ProjectError(f"Context lockfile references unknown alias: {alias_name}")


def _context_lock_status(config: ProjectConfig, lock: dict[str, Any] | None) -> str:
    if not lock:
        return "missing"
    try:
        _validate_context_lock(config, lock)
    except ProjectError:
        return "drifted"
    return "locked"


def _source_public_payload(source: ProjectSource) -> dict[str, Any]:
    payload = source.model_dump(mode="json", exclude={"auth"})
    payload["auth"] = _source_auth_public_payload(source)
    alias = context_alias_for_url(source.url)
    if alias:
        payload["alias"] = alias.name
        payload["alias_title"] = alias.title
    return payload


def _source_auth_public_payload(source: ProjectSource) -> dict[str, Any]:
    return {
        "source_name": source.name,
        "type": _source_auth_type(source),
        "policy": source.auth.policy if source.auth else "none",
        "ready": _source_auth_ready(source),
        "credential": "[env]" if source.auth else None,
    }


def _source_auth_type(source: ProjectSource) -> str:
    return source.auth.type if source.auth else "none"


def _source_auth_ready(source: ProjectSource) -> bool:
    return bool(source.auth is None or os.environ.get(source.auth.env))


def _normalize_project_record(record: dict[str, Any], source: ProjectSource) -> dict[str, Any]:
    normalized = dict(record)
    metadata = dict(_dict_value(normalized.get("metadata")))
    metadata["docpull_project_source"] = source.name
    metadata["docpull_source_type"] = source.type
    normalized["metadata"] = metadata
    normalized["title"] = _clean_title(str(normalized.get("title") or normalized.get("url") or ""))
    normalized["canonical_url"] = _canonical_url(
        str(
            metadata.get("canonical_url")
            or metadata.get("canonical")
            or normalized.get("canonical_url")
            or normalized.get("url")
            or ""
        )
    )
    normalized.setdefault("license_hint", metadata.get("license_hint"))
    normalized.setdefault("source_type", None if source.type == "auto" else source.type)
    if not normalized.get("document_id"):
        normalized["document_id"] = _stable_id(
            "doc",
            str(normalized.get("url") or ""),
            str(normalized.get("content_hash") or ""),
        )
    if not normalized.get("content_hash"):
        normalized["content_hash"] = _sha256(str(normalized.get("content") or ""))
    return normalized


def _dedupe_project_records(
    records: list[dict[str, Any]],
    *,
    config: ProjectConfig,
) -> list[dict[str, Any]]:
    source_hosts = {source.name: (urlparse(source.url).hostname or "").lower() for source in config.sources}
    seen: dict[str, int] = {}
    output: list[dict[str, Any]] = []
    for record in records:
        keys = _record_dedupe_keys(record)
        existing_index = next((seen[key] for key in keys if key in seen), None)
        if existing_index is None:
            output.append(record)
            current_index = len(output) - 1
            for key in keys:
                seen[key] = current_index
            continue

        existing = output[existing_index]
        if _record_quality_score(record, source_hosts) > _record_quality_score(existing, source_hosts):
            output[existing_index] = record
            for key in _record_dedupe_keys(existing):
                if seen.get(key) == existing_index:
                    del seen[key]
            for key in keys:
                seen[key] = existing_index
    return output


def _record_dedupe_keys(record: dict[str, Any]) -> list[str]:
    source_name = _record_source_name(record)
    title = _clean_title(str(record.get("title") or ""))
    keys: list[str] = []
    canonical = _canonical_url(str(record.get("canonical_url") or ""))
    if canonical:
        keys.append(f"canonical:{canonical}")
    url = _canonical_url(str(record.get("url") or ""))
    if url:
        keys.append(f"url:{url}")
    content_hash = str(record.get("content_hash") or "")
    if content_hash:
        keys.append(f"hash:{content_hash}")
    fingerprint = _content_fingerprint(str(record.get("content") or ""))
    if source_name and title and fingerprint:
        keys.append(f"fingerprint:{source_name}:{title.lower()}:{fingerprint}")
    return keys


def _record_quality_score(record: dict[str, Any], source_hosts: dict[str, str]) -> tuple[int, int, int]:
    source_name = _record_source_name(record)
    source_host = source_hosts.get(source_name, "")
    url_host = (urlparse(str(record.get("url") or "")).hostname or "").lower()
    canonical_host = (urlparse(str(record.get("canonical_url") or "")).hostname or "").lower()
    host_score = int(bool(source_host and url_host == source_host)) + int(
        bool(source_host and canonical_host == source_host)
    )
    content_length = len(str(record.get("content") or ""))
    title_length = len(str(record.get("title") or ""))
    return (host_score, content_length, title_length)


def _record_counts_by_source(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        source_name = _record_source_name(record)
        if source_name:
            counts[source_name] = counts.get(source_name, 0) + 1
    return counts


def _record_source_name(record: dict[str, Any]) -> str:
    metadata = _dict_value(record.get("metadata"))
    return str(metadata.get("docpull_project_source") or "")


def _clean_title(value: str) -> str:
    without_comments = re.sub(r"<!--.*?-->", "", html.unescape(value), flags=re.DOTALL)
    return re.sub(r"\s+", " ", without_comments).strip()


def _canonical_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        path=path,
        params="",
        fragment="",
    ).geturl()


def _content_fingerprint(content: str) -> str:
    text = html.unescape(content).lower()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    text = re.sub(r"https?://[^\s)>\]\"']+", "<url>", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _sha256(text) if text else ""


def _write_run_sources(run_dir: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources_dir = run_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    source_entries: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        source_path = sources_dir / f"{index:03d}.md"
        content = str(record.get("content") or "")
        source_path.write_text(content, encoding="utf-8")
        rel_path = source_path.relative_to(run_dir).as_posix()
        record["text_path"] = rel_path
        source_entries.append(
            {
                "index": index,
                "url": record.get("url"),
                "title": record.get("title") or record.get("url"),
                "path": rel_path,
                "source_name": (
                    record.get("metadata", {}).get("docpull_project_source")
                    if isinstance(record.get("metadata"), dict)
                    else None
                ),
            }
        )
    return source_entries


def _build_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = TokenCounter()
    chunks: list[dict[str, Any]] = []
    for record in records:
        content = str(record.get("content") or "")
        if not content.strip():
            continue
        document_id = str(
            record.get("document_id") or _stable_id("doc", str(record.get("url") or ""), content)
        )
        markdown_chunks = chunk_markdown(content, max_tokens=DEFAULT_CHUNK_TOKENS, counter=counter)
        if not markdown_chunks:
            markdown_chunks = []
        for index, chunk in enumerate(markdown_chunks):
            chunk_hash = _sha256(chunk.text)
            chunk_id = _stable_id("chunk", document_id, str(index), chunk_hash)
            chunks.append(
                {
                    "schema_version": PROJECT_SCHEMA_VERSION,
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "url": record.get("url"),
                    "title": record.get("title"),
                    "content": chunk.text,
                    "content_hash": chunk_hash,
                    "source_content_hash": record.get("content_hash"),
                    "chunk_index": chunk.index,
                    "chunk_heading": chunk.heading,
                    "token_count": chunk.token_count,
                    "source_name": (
                        record.get("metadata", {}).get("docpull_project_source")
                        if isinstance(record.get("metadata"), dict)
                        else None
                    ),
                }
            )
    return chunks


def _run_manifest(
    *,
    config: ProjectConfig,
    run_id: str,
    status: str,
    records: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    source_entries: list[dict[str, Any]],
    started_at: str,
    finished_at: str,
    update_discovery: bool,
) -> dict[str, Any]:
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "generated_at": finished_at,
        "project": config.name,
        "run_id": run_id,
        "status": status,
        "started_at": started_at,
        "finished_at": finished_at,
        "update_discovery": update_discovery,
        "sources": [_source_public_payload(source) for source in config.sources],
        "document_count": len(records),
        "chunk_count": len(chunks),
        "records": [
            {
                "document_id": record.get("document_id"),
                "url": record.get("url"),
                "canonical_url": record.get("canonical_url"),
                "title": record.get("title"),
                "content_hash": record.get("content_hash"),
                "source_type": record.get("source_type"),
                "license_hint": record.get("license_hint"),
                "output_path": entry.get("path") if index < len(source_entries) else None,
            }
            for index, (record, entry) in enumerate(zip(records, source_entries, strict=False))
        ],
    }


def _corpus_manifest(
    manifest: dict[str, Any],
    records: list[dict[str, Any]],
    source_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_at": manifest["generated_at"],
        "output_format": "ndjson",
        "run": {
            "project": manifest["project"],
            "run_id": manifest["run_id"],
            "status": manifest["status"],
        },
        "document_count": len({str(record.get("document_id")) for record in records}),
        "record_count": len(records),
        "chunk_count": 0,
        "records": [
            {
                "document_id": record.get("document_id"),
                "url": record.get("url"),
                "canonical_url": record.get("canonical_url"),
                "title": record.get("title"),
                "content_hash": record.get("content_hash"),
                "source_type": record.get("source_type"),
                "license_hint": record.get("license_hint"),
                "output_path": source_entries[index].get("path") if index < len(source_entries) else None,
            }
            for index, record in enumerate(records)
        ],
    }


def _local_pack(
    config: ProjectConfig,
    records: list[dict[str, Any]],
    source_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    include_domains = sorted(
        {
            urlparse(str(source.url)).hostname or ""
            for source in config.sources
            if urlparse(str(source.url)).hostname
        }
    )
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "provider": "local",
        "workflow": "project-sync",
        "objective": f"Persistent DocPull project: {config.name}",
        "request_options": {
            "source_policy": {
                "include_domains": include_domains,
                "robots": config.policy.robots,
            }
        },
        "record_count": len(records),
        "extract_error_count": 0,
        "sources": source_entries,
        "artifacts": {
            "documents_ndjson": "documents.ndjson",
            "corpus_manifest": "corpus.manifest.json",
            "sources": "sources.md",
            "project_manifest": "manifest.json",
            "chunks": "chunks.jsonl",
            "accounting": "accounting.json",
        },
    }


def _run_accounting(config: ProjectConfig, stats: dict[str, int]) -> dict[str, Any]:
    accounting = RunAccounting(
        budget_limit_usd=config.budget.maximum_paid_cost_usd,
        estimated_paid_cost_usd=0.0,
        paid_request_count=0,
        http_request_count=_safe_int(stats.get("http_request_count")),
        cache_hit_count=0,
        command="project sync",
    )
    return accounting.to_dict()


def _source_health(
    source: ProjectSource,
    *,
    status: str,
    document_count: int = 0,
    failed_count: int = 0,
    skipped_count: int = 0,
    robots_blocked_count: int = 0,
    last_error: str | None = None,
) -> dict[str, Any]:
    return {
        "source_name": source.name,
        "url": source.url,
        "source_type": source.type,
        "status": status,
        "auth_type": _source_auth_type(source),
        "auth_policy": source.auth.policy if source.auth else "none",
        "auth_ready": _source_auth_ready(source),
        "discovered_url_count": len(source.discovered_urls),
        "discovered_at": source.discovered_at,
        "document_count": document_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "robots_blocked_count": robots_blocked_count,
        "last_error": last_error,
    }


def _sources_markdown(source_entries: list[dict[str, Any]]) -> str:
    lines = ["# Sources", ""]
    if not source_entries:
        lines.append("_No sources fetched._")
    for source in source_entries:
        lines.append(f"- [{source.get('title') or source.get('url')}]({source.get('url')})")
        lines.append(f"  - Path: `{source.get('path')}`")
        if source.get("source_name"):
            lines.append(f"  - Project source: `{source.get('source_name')}`")
    return "\n".join(lines).rstrip() + "\n"


def _likely_api_behavior_changes(
    diff_payload: dict[str, Any],
    old_records: dict[str, dict[str, Any]],
    new_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = set(diff_payload.get("changed_urls", [])) | set(diff_payload.get("added_urls", []))
    output: list[dict[str, Any]] = []
    for url in sorted(candidates):
        old = old_records.get(url, {})
        new = new_records.get(url, {})
        text = f"{url}\n{new.get('title') or ''}\n{new.get('content') or ''}".lower()
        signals = [
            signal
            for signal in (
                "api",
                "sdk",
                "openapi",
                "webhook",
                "required",
                "deprecated",
                "parameter",
                "field",
                "retry",
                "auth",
                "rate limit",
            )
            if signal in text
        ]
        if signals:
            output.append(
                {
                    "url": url,
                    "title": new.get("title") or old.get("title"),
                    "signals": signals[:6],
                    "old_hash": old.get("content_hash"),
                    "new_hash": new.get("content_hash"),
                }
            )
    return output


def _pricing_changes(
    diff_payload: dict[str, Any],
    old_records: dict[str, dict[str, Any]],
    new_records: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = (
        set(diff_payload.get("changed_urls", []))
        | set(diff_payload.get("added_urls", []))
        | set(diff_payload.get("removed_urls", []))
    )
    output: list[dict[str, Any]] = []
    for url in sorted(candidates):
        record = new_records.get(url) or old_records.get(url) or {}
        text = f"{url}\n{record.get('title') or ''}\n{record.get('content') or ''}".lower()
        signals = [
            signal for signal in ("pricing", "price", "billing", "plan", "fee", "usage") if signal in text
        ]
        if signals:
            output.append(
                {
                    "url": url,
                    "title": record.get("title"),
                    "signals": signals[:6],
                    "content_hash": record.get("content_hash"),
                }
            )
    return output


def _source_health_delta(new_health: dict[str, Any], old_health: dict[str, Any]) -> list[dict[str, Any]]:
    old_by_name = {
        str(item.get("source_name")): item
        for item in old_health.get("sources", [])
        if isinstance(item, dict) and item.get("source_name")
    }
    deltas: list[dict[str, Any]] = []
    for item in new_health.get("sources", []):
        if not isinstance(item, dict) or not item.get("source_name"):
            continue
        name = str(item["source_name"])
        old = old_by_name.get(name, {})
        if not old:
            deltas.append({"source_name": name, "change": "added", "new_status": item.get("status")})
            continue
        fields = {}
        for key in (
            "status",
            "discovered_url_count",
            "discovered_at",
            "document_count",
            "failed_count",
            "skipped_count",
            "robots_blocked_count",
        ):
            if old.get(key) != item.get(key):
                fields[key] = {"old": old.get(key), "new": item.get(key)}
        if fields:
            deltas.append({"source_name": name, "change": "changed", "fields": fields})
    return deltas


def _semantic_diff(
    diff_payload: dict[str, Any],
    *,
    config: ProjectConfig,
    mode: SemanticMode,
    client: SemanticClient | None,
) -> dict[str, Any]:
    effective_mode = mode if mode != "auto" else config.refresh.semantic_diff
    model = os.environ.get(SEMANTIC_MODEL_ENV)
    if effective_mode == "off":
        return _semantic_skipped("semantic diff disabled", model=model)
    if mode == "auto" and effective_mode == "auto":
        enabled = os.environ.get(SEMANTIC_ENABLE_ENV) == "1"
        if not (enabled and model and os.environ.get(ANTHROPIC_API_KEY_ENV)):
            return _semantic_skipped(
                "semantic diff auto skipped; set BYOK semantic model to enable",
                model=model,
            )
    resolved_client = client
    resolved_model = model or "claude-opus-4-7"
    if resolved_client is None:
        api_key = os.environ.get(ANTHROPIC_API_KEY_ENV)
        if not api_key:
            return _semantic_skipped(f"{ANTHROPIC_API_KEY_ENV} not set", model=resolved_model)
        resolved_client = _AnthropicSemanticClient(api_key=api_key, model=resolved_model)
    prompt = _semantic_prompt(diff_payload)
    try:
        text = resolved_client(prompt)
    except _SemanticTransportError as err:
        return _semantic_skipped(f"semantic transport error: {err}", model=resolved_model)
    parsed = _parse_json_object(text)
    if parsed is None:
        return _semantic_skipped("semantic provider returned non-JSON response", model=resolved_model)
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "skipped": False,
        "skip_reason": None,
        "model": resolved_model,
        "summary": parsed,
    }


def _semantic_skipped(reason: str, *, model: str | None) -> dict[str, Any]:
    return {
        "schema_version": PROJECT_SCHEMA_VERSION,
        "skipped": True,
        "skip_reason": reason,
        "model": model,
        "summary": None,
    }


def _semantic_prompt(diff_payload: dict[str, Any]) -> str:
    details = diff_payload.get("changed_details")
    if not isinstance(details, list):
        details = []
    compact = [
        {
            "url": item.get("url"),
            "content_changed": item.get("content_changed"),
            "title_changed": item.get("title_changed"),
            "old_titles": item.get("old_titles"),
            "new_titles": item.get("new_titles"),
        }
        for item in details[:25]
        if isinstance(item, dict)
    ]
    return (
        "Summarize likely documentation behavior changes from this DocPull hash diff. "
        "Return only JSON with keys: summary, likely_behavior_changes, risks.\n\n"
        + json.dumps(
            {
                "added_urls": diff_payload.get("added_urls", [])[:25],
                "removed_urls": diff_payload.get("removed_urls", [])[:25],
                "changed_details": compact,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


class _SemanticTransportError(RuntimeError):
    """Raised for semantic model transport failures."""


class _AnthropicSemanticClient:
    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def __call__(self, prompt: str) -> str:
        body = json.dumps(
            {
                "model": self._model,
                "max_tokens": 700,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            }
        ).encode()
        request = urllib.request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=SEMANTIC_REQUEST_TIMEOUT_S) as response:  # nosec B310
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise _SemanticTransportError(f"HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise _SemanticTransportError(str(exc.reason)) from exc
        except json.JSONDecodeError as exc:
            raise _SemanticTransportError("response was not JSON") from exc
        content = payload.get("content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            text = content[0].get("text")
            if isinstance(text, str):
                return text
        raise _SemanticTransportError("response missing content[0].text")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _project_diff_markdown(payload: dict[str, Any]) -> str:
    lines = [
        _diff_markdown(payload),
        "## Project Signals",
        "",
        f"- Likely API behavior changes: {payload['summary']['likely_api_behavior_change_count']}",
        f"- Pricing changes: {payload['summary']['pricing_change_count']}",
    ]
    semantic = payload.get("semantic")
    if isinstance(semantic, dict):
        lines.extend(["", "## Semantic Summary", ""])
        if semantic.get("skipped"):
            lines.append(f"Skipped: {semantic.get('skip_reason')}")
        else:
            lines.append(json.dumps(semantic.get("summary"), indent=2, ensure_ascii=False))
    return "\n".join(lines).rstrip() + "\n"


def _project_review_markdown(payload: dict[str, Any]) -> str:
    summary = _dict_value(payload.get("summary"))
    lines = [
        f"# Project Review: {payload.get('run_id')}",
        "",
        f"- Documents: {summary.get('document_count', 0)}",
        f"- Chunks: {summary.get('chunk_count', 0)}",
        f"- Changed: {summary.get('changed_count', 0)}",
        f"- Added: {summary.get('added_count', 0)}",
        f"- Removed: {summary.get('removed_count', 0)}",
        f"- Failed URLs: {summary.get('failed_count', 0)}",
        f"- Robots blocked: {summary.get('robots_blocked', 0)}",
        f"- Likely API behavior changes: {summary.get('likely_api_behavior_change_count', 0)}",
        f"- Pricing changes: {summary.get('pricing_change_count', 0)}",
        "",
        "## Source Health",
        "",
    ]
    sources = payload.get("sources")
    if isinstance(sources, list) and sources:
        for source in sources:
            if isinstance(source, dict):
                lines.append(
                    f"- `{source.get('source_name')}`: {source.get('status')} "
                    f"docs={source.get('document_count', 0)} failed={source.get('failed_count', 0)} "
                    f"auth={source.get('auth_type', 'none')} ready={source.get('auth_ready', True)}"
                )
    else:
        lines.append("_No source health recorded._")
    errors = payload.get("errors")
    lines.extend(["", "## Errors", ""])
    if isinstance(errors, list) and errors:
        for error in errors[:20]:
            if isinstance(error, dict):
                lines.append(f"- {error.get('url') or error.get('source_name')}: {error.get('error')}")
    else:
        lines.append("_No errors recorded._")
    return "\n".join(lines).rstrip() + "\n"


def _print_project_diff(console: Console, payload: dict[str, Any]) -> None:
    summary = _dict_value(payload.get("summary"))
    console.print(
        "[green]Project diff:[/green] "
        f"+{summary.get('added_count', 0)} -{summary.get('removed_count', 0)} "
        f"~{summary.get('changed_count', 0)} "
        f"api={summary.get('likely_api_behavior_change_count', 0)} "
        f"pricing={summary.get('pricing_change_count', 0)}"
    )
    details = {
        str(item.get("url")): item
        for item in payload.get("changed_details", [])
        if isinstance(item, dict) and item.get("url")
    }
    api_urls = {
        str(item.get("url")): item
        for item in payload.get("likely_api_behavior_changes", [])
        if isinstance(item, dict) and item.get("url")
    }
    pricing_urls = {
        str(item.get("url")): item
        for item in payload.get("pricing_changes", [])
        if isinstance(item, dict) and item.get("url")
    }
    changed_urls = [str(url) for url in payload.get("changed_urls", []) if url]
    added_urls = [str(url) for url in payload.get("added_urls", []) if url]
    removed_urls = [str(url) for url in payload.get("removed_urls", []) if url]
    if changed_urls:
        console.print("")
        console.print("[bold]Changed pages:[/bold]")
        for url in changed_urls[:10]:
            labels = _diff_signal_labels(url, api_urls, pricing_urls)
            suffix = f" ({', '.join(labels)})" if labels else ""
            title = details.get(url, {}).get("new_titles") or details.get(url, {}).get("old_titles")
            title_text = _first_title(title)
            console.print(f"- {_display_url_path(url)}{suffix}")
            if title_text:
                console.print(f"  {escape(title_text)}")
        if len(changed_urls) > 10:
            console.print(f"  ... {len(changed_urls) - 10} more changed pages")
    if added_urls:
        _print_url_group(console, "Added pages", added_urls, "+")
    if removed_urls:
        _print_url_group(console, "Removed pages", removed_urls, "-")
    health_delta = payload.get("source_health_delta")
    if isinstance(health_delta, list) and health_delta:
        console.print("")
        console.print("[bold]Source health:[/bold]")
        for item in health_delta[:5]:
            if isinstance(item, dict):
                console.print(f"- {escape(str(item.get('source_name') or 'unknown'))}: {item.get('change')}")


def _diff_signal_labels(
    url: str,
    api_urls: dict[str, dict[str, Any]],
    pricing_urls: dict[str, dict[str, Any]],
) -> list[str]:
    labels: list[str] = []
    if url in api_urls:
        labels.append("likely API behavior change")
    if url in pricing_urls:
        labels.append("pricing/billing change")
    return labels


def _print_url_group(console: Console, title: str, urls: list[str], marker: str) -> None:
    console.print("")
    console.print(f"[bold]{title}:[/bold]")
    for url in urls[:10]:
        console.print(f"{marker} {_display_url_path(url)}")
    if len(urls) > 10:
        console.print(f"  ... {len(urls) - 10} more")


def _display_url_path(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        path = parsed.path.rstrip("/")
        return escape(path or url)
    return escape(url)


def _first_title(value: Any) -> str:
    if isinstance(value, list) and value:
        return str(value[0])
    if isinstance(value, str):
        return value
    return ""


def _write_native_context_export(
    *,
    run_dir: Path,
    output_dir: Path,
    target: ContextTarget,
    project_name: str,
) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    if target == "cursor":
        result = export_pack(run_dir, format="cursor-rules", output=output_dir / f"{project_name}.mdc")
        artifacts["native"] = Path(result.output_path).relative_to(output_dir).as_posix()
    elif target == "claude":
        result = export_pack(run_dir, format="claude-skill", output=output_dir / project_name)
        artifacts["native"] = Path(result.output_path).relative_to(output_dir).as_posix()
    elif target == "codex":
        result = export_pack(run_dir, format="codex-skill", output=output_dir / project_name)
        artifacts["native"] = Path(result.output_path).relative_to(output_dir).as_posix()
    elif target == "openai":
        result = export_pack(run_dir, format="openai-vector-jsonl", output=output_dir / "openai-vector.jsonl")
        artifacts["native"] = Path(result.output_path).relative_to(output_dir).as_posix()
    elif target == "llamaindex":
        result = export_pack(run_dir, format="llamaindex-jsonl", output=output_dir / "llamaindex.jsonl")
        artifacts["native"] = Path(result.output_path).relative_to(output_dir).as_posix()
    elif target == "langchain":
        result = export_pack(run_dir, format="langchain-jsonl", output=output_dir / "langchain.jsonl")
        artifacts["native"] = Path(result.output_path).relative_to(output_dir).as_posix()
    else:  # pragma: no cover - guarded by parser and validator
        raise ProjectError(f"Unsupported context-pack target: {target}")
    return artifacts


def _context_markdown(
    project_name: str,
    run_id: str,
    chunks: list[dict[str, Any]],
    sources: Any,
) -> str:
    citation_by_url = {
        str(source.get("url")): str(source.get("citation_id"))
        for source in sources
        if isinstance(source, dict) and source.get("url") and source.get("citation_id")
    }
    lines = [
        f"# {project_name} Context Pack",
        "",
        f"Run: `{run_id}`",
        "",
        "## Sources",
        "",
    ]
    for source in sources if isinstance(sources, list) else []:
        if isinstance(source, dict):
            lines.append(
                f"- [{source.get('citation_id')}] "
                f"{source.get('title') or source.get('url')} - {source.get('url')}"
            )
    lines.extend(["", "## Chunks", ""])
    for chunk in chunks:
        url = str(chunk.get("url") or "")
        citation = citation_by_url.get(url, "S?")
        heading = chunk.get("chunk_heading") or chunk.get("title") or url
        lines.append(f"### [{citation}] {heading}")
        lines.append("")
        lines.append(str(chunk.get("content") or "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _eval_changed_urls(run_dir: Path) -> set[str]:
    diff_path = run_dir / "project.diff.json"
    if not diff_path.exists():
        return set()
    payload = _read_json(diff_path, default={})
    urls: set[str] = set()
    for key in ("added_urls", "changed_urls"):
        value = payload.get(key)
        if isinstance(value, list):
            urls.update(str(item) for item in value if isinstance(item, str))
    return urls


def _eval_case(
    record: dict[str, Any],
    citation_by_url: dict[str, str],
    changed_urls: set[str],
) -> dict[str, Any]:
    url = str(record.get("url") or "")
    title = str(record.get("title") or url)
    content = " ".join(str(record.get("content") or "").split())
    return {
        "id": _stable_id("eval", str(record.get("document_id") or ""), str(record.get("content_hash") or "")),
        "source_url": url,
        "question": f"What does the source say about {title}?",
        "answer_hint": content[:500],
        "expected_citation_ids": [citation_by_url[url]] if url in citation_by_url else [],
        "content_hash": record.get("content_hash"),
        "kind": "changed" if url in changed_urls else "document",
    }


def _resolve_diff_run_ids(
    project_root: Path,
    from_run_id: str | None,
    to_run_id: str | None,
) -> tuple[str, str]:
    runs = _run_ids(project_root)
    if from_run_id and to_run_id:
        return _safe_run_id(from_run_id), _safe_run_id(to_run_id)
    if to_run_id and not from_run_id:
        previous = _previous_run_id(project_root, _safe_run_id(to_run_id))
        if not previous:
            raise ProjectError(f"No previous run found before {to_run_id}")
        return previous, _safe_run_id(to_run_id)
    if from_run_id and not to_run_id:
        latest = _latest_run_id(project_root)
        if not latest:
            raise ProjectError("No latest run found")
        return _safe_run_id(from_run_id), latest
    if len(runs) < 2:
        raise ProjectError("Need at least two sync runs to diff")
    return runs[-2], runs[-1]


def _run_ids(project_root: Path) -> list[str]:
    paths = project_paths(project_root)
    if not paths.index.exists():
        return []
    conn = sqlite3.connect(paths.index)
    try:
        rows = conn.execute(
            "SELECT run_id FROM runs WHERE status != 'dry_run' ORDER BY started_at, run_id"
        ).fetchall()
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def _latest_run_id(project_root: Path) -> str | None:
    paths = project_paths(project_root)
    if paths.latest_run.exists():
        text = paths.latest_run.read_text(encoding="utf-8").strip()
        if text:
            return text
    runs = _run_ids(project_root)
    return runs[-1] if runs else None


def _previous_run_id(project_root: Path, run_id: str) -> str | None:
    runs = _run_ids(project_root)
    if run_id not in runs:
        return None
    index = runs.index(run_id)
    if index <= 0:
        return None
    return runs[index - 1]


def _run_payload(run_dir: Path | None) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    path = run_dir / "run.json"
    if not path.exists():
        return None
    value = _read_json(path, default=None)
    return value if isinstance(value, dict) else None


def _latest_diff_payload(run_dir: Path | None) -> dict[str, Any] | None:
    if run_dir is None:
        return None
    path = run_dir / "project.diff.json"
    if not path.exists():
        return None
    value = _read_json(path, default=None)
    return value if isinstance(value, dict) else None


def _records_by_url(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(record.get("url")): record for record in records if record.get("url")}


def _read_json(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ProjectError(f"Invalid JSON in {path}: {err}") from err


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ProjectError(f"Missing required file: {path}")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise ProjectError(f"Invalid JSONL in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise ProjectError(f"Invalid JSONL in {path} line {index}: expected object")
        records.append(value)
    return records


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _new_run_id() -> str:
    return "run_" + utc_now().strftime("%Y%m%dT%H%M%S%fZ")


def _safe_run_id(value: str) -> str:
    run_id = value.strip()
    if not run_id:
        raise ProjectError("run ID must not be empty")
    if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ProjectError("run ID must not contain path separators or dot segments")
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ProjectError("run ID contains unsupported characters")
    return run_id


def _run_dir_for_id(paths: ProjectPaths, run_id: str) -> tuple[str, Path]:
    selected_run_id = _safe_run_id(run_id)
    run_dir = (paths.runs / selected_run_id).resolve()
    runs_root = paths.runs.resolve()
    try:
        run_dir.relative_to(runs_root)
    except ValueError as err:
        raise ProjectError("run ID resolves outside project runs directory") from err
    return selected_run_id, run_dir


def _safe_release_tag(value: str) -> str:
    tag = re.sub(r"[^0-9A-Za-z_.-]+", "-", value.strip()).strip("-")
    if not tag:
        raise ProjectError("release tag must not be empty")
    return tag


def _remote_root(root: Path | None) -> Path:
    start = (root or Path.cwd()).resolve()
    try:
        return find_project_root(start)
    except ProjectError:
        return start


def _validate_remote_api_url(api_url: str, *, allow_insecure_local_http: bool) -> str:
    normalized = api_url.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme == "https" and parsed.netloc:
        return normalized
    if parsed.scheme == "http" and parsed.netloc:
        if allow_insecure_local_http and _is_loopback_remote_host(parsed.hostname or ""):
            return normalized
        raise ProjectError(
            "--api-url must use HTTPS. For local development only, pass "
            "--allow-insecure-local-http with an http://localhost or loopback URL."
        )
    raise ProjectError("--api-url must be an absolute HTTPS URL")


def _is_loopback_remote_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _load_remote_config(root: Path | None = None) -> dict[str, Any]:
    paths = project_paths(_remote_root(root))
    if not paths.remote_config.exists():
        raise ProjectError("No remote configured. Run `docpull remote login --api-url URL --token TOKEN`.")
    value = _read_json(paths.remote_config, default=None)
    if not isinstance(value, dict) or not value.get("api_url") or not value.get("token"):
        raise ProjectError(f"Invalid remote config: {paths.remote_config}")
    return value


def _remote_json_request(
    remote: dict[str, Any],
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> dict[str, Any]:
    api_url = _validate_remote_api_url(
        str(remote["api_url"]),
        allow_insecure_local_http=bool(remote.get("allow_insecure_local_http")),
    )
    token = str(remote["token"])
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        api_url + path,
        data=data,
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "accept": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise ProjectError(f"Remote API error HTTP {err.code}: {detail}") from err
    except urllib.error.URLError as err:
        raise ProjectError(f"Remote API request failed: {err.reason}") from err
    except json.JSONDecodeError as err:
        raise ProjectError("Remote API returned non-JSON response") from err
    return payload if isinstance(payload, dict) else {"data": payload}


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _source_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.hostname:
        return _slug(url.replace(":", "-").replace("@", "-")) or "source"
    host = (parsed.hostname or "source").removeprefix("www.")
    path_bits = [part for part in parsed.path.split("/") if part][:2]
    base = "-".join([host.replace(".", "-"), *path_bits])
    return _slug(base) or "source"


def _unique_source_name(sources: list[ProjectSource], base: str) -> str:
    return _unique_name({source.name for source in sources}, base)


def _unique_name(used: set[str], base: str) -> str:
    candidate = _slug(base) or "source"
    if candidate not in used:
        return candidate
    index = 2
    while f"{candidate}-{index}" in used:
        index += 1
    return f"{candidate}-{index}"


def _source_type(value: str) -> SourceType:
    if value not in SOURCE_TYPES:
        raise ProjectError(f"Unsupported source type: {value}")
    return value  # type: ignore[return-value]


def _semantic_mode(value: str) -> SemanticMode:
    if value not in {"auto", "off", "on"}:
        raise ProjectError(f"Unsupported semantic mode: {value}")
    return value  # type: ignore[return-value]


def _context_target(value: str) -> ContextTarget:
    if value not in CONTEXT_TARGETS:
        raise ProjectError(f"Unsupported context-pack target: {value}")
    return value  # type: ignore[return-value]


def _skip_reason_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{value} B"
