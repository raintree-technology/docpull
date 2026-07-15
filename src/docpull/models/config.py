"""Pydantic configuration models for docpull."""

from __future__ import annotations

import re
import tempfile
from enum import Enum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

DEFAULT_CLOUD_ARTIFACT_PATH = str(Path(tempfile.gettempdir()) / "docpull-render-result.json")


class ProfileName(str, Enum):
    """Built-in configuration profiles."""

    RAG = "rag"
    MIRROR = "mirror"
    QUICK = "quick"
    LLM = "llm"
    OKF = "okf"
    SEC_FILING = "sec-filing"
    CUSTOM = "custom"


class AuthType(str, Enum):
    """Authentication types for protected web sources."""

    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"
    COOKIE = "cookie"
    HEADER = "header"


class ByteSize(int):
    """
    Custom type that parses human-readable byte sizes.

    Accepts:
        - Integers (bytes)
        - Strings like '200kb', '1mb', '5gb'

    Examples:
        >>> ByteSize._parse('200kb')
        204800
        >>> ByteSize._parse('1mb')
        1048576
        >>> ByteSize._parse(1024)
        1024
    """

    @classmethod
    def __get_pydantic_core_schema__(cls, _source: Any, _handler: Any) -> Any:
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(cls._parse)

    @classmethod
    def _parse(cls, v: Any) -> int:
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            v = v.lower().strip()
            # Order matters: check longer suffixes first
            units = [("gb", 1024**3), ("mb", 1024**2), ("kb", 1024), ("b", 1)]
            for unit, mult in units:
                if v.endswith(unit):
                    num_str = v[: -len(unit)].strip()
                    try:
                        return int(float(num_str) * mult)
                    except ValueError as err:
                        raise ValueError(f"Invalid number in byte size: {v}") from err
            # Try parsing as plain number
            try:
                return int(v)
            except ValueError as err:
                raise ValueError(
                    f"Invalid byte size: {v}. Use format like '200kb', '1mb', or integer bytes."
                ) from err
        raise ValueError(f"Invalid byte size: {v}. Use format like '200kb', '1mb', or integer bytes.")


class CrawlConfig(BaseModel):
    """Configuration for URL discovery and crawling behavior."""

    max_pages: int | None = Field(None, description="Maximum pages to fetch (None = unlimited)")
    max_depth: int = Field(5, ge=1, description="Maximum crawl depth from starting URL")
    max_concurrent: int = Field(10, ge=1, description="Maximum concurrent requests globally")
    rate_limit: float = Field(0.5, ge=0, description="Minimum seconds between requests to same host")
    per_host_concurrent: int = Field(3, ge=1, description="Maximum concurrent requests per host")
    include_paths: list[str] = Field(default_factory=list, description="URL path patterns to include")
    exclude_paths: list[str] = Field(default_factory=list, description="URL path patterns to exclude")
    adaptive_rate_limit: bool = Field(
        False,
        description="Automatically adjust rate limits based on server responses (429s)",
    )
    streaming_discovery: bool = Field(
        True,
        description=(
            "Pipe URLs from the discoverer directly into a worker pool, "
            "instead of collecting the full list before fetching. Brings "
            "first PAGE_SAVED forward on large crawl-driven sites. "
            "Set False to fall back to discover-all-then-fetch."
        ),
    )

    model_config = {"extra": "forbid"}


class ContentFilterConfig(BaseModel):
    """Configuration for content filtering and deduplication."""

    streaming_dedup: bool = Field(
        False,
        description="Enable real-time deduplication during fetch (more efficient)",
    )
    max_file_size: ByteSize | None = Field(
        None,
        description="Maximum size per response in bytes (e.g., '200kb', '1mb'). Caps the per-page download.",
    )
    extractor: Literal["default", "trafilatura", "ensemble"] = Field(
        "default",
        description="Content extractor to use (trafilatura/ensemble can use optional trafilatura)",
    )
    enable_special_cases: bool = Field(
        True,
        description=(
            "Run framework-specific fast extractors (Next.js, OpenAPI, etc.) before the generic extractor"
        ),
    )
    strict_js_required: bool = Field(
        False,
        description="Error (instead of silently skipping) when a page appears to require JavaScript",
    )
    clean_inline_xbrl: bool = Field(
        False,
        description="Remove hidden Inline XBRL boilerplate before content extraction",
    )
    remote_documents: Literal["off", "pdf"] = Field(
        "off",
        description=(
            "Explicitly allow selected remote document types to be downloaded and parsed locally. "
            "Off by default; never enables browser or cloud parsing."
        ),
    )
    remote_document_backend: Literal["auto", "pypdf", "markitdown", "unstructured"] = Field(
        "auto",
        description="Local parser backend used when remote_documents is enabled",
    )
    remote_document_timeout_seconds: int = Field(
        60,
        ge=1,
        le=3600,
        description="Wall-time limit for the isolated remote-document parser process",
    )
    remote_document_memory_mib: int = Field(
        1024,
        ge=64,
        description="Address-space limit for the isolated remote-document parser process",
    )

    model_config = {"extra": "forbid"}


def _normalize_render_domain(value: str) -> str:
    """Normalize a render allow-list entry to a lower-case host name."""
    raw = value.strip().lower()
    if not raw:
        raise ValueError("allowed_domains entries must not be empty")

    parsed = urlparse(raw) if "://" in raw else urlparse(f"//{raw}", scheme="https")

    host = parsed.hostname or raw.split("/", 1)[0].split(":", 1)[0]
    host = host.strip().rstrip(".")
    if not host or "/" in host:
        raise ValueError(f"Invalid render allowed domain: {value}")
    return host


class RenderViewport(BaseModel):
    """Browser viewport used by the optional renderer."""

    width: int = Field(1280, ge=1, description="Viewport width in CSS pixels")
    height: int = Field(720, ge=1, description="Viewport height in CSS pixels")

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _parse_viewport(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.lower().strip()
            if "x" not in text:
                raise ValueError("viewport must use WIDTHxHEIGHT, for example 1280x720")
            width, height = text.split("x", 1)
            try:
                return {"width": int(width), "height": int(height)}
            except ValueError as err:
                raise ValueError("viewport must use integer WIDTHxHEIGHT values") from err
        return value


class RenderActionPolicy(BaseModel):
    """Safety switches for browser rendering.

    Defaults are intentionally restrictive and recorded in render artifacts so
    a run can be audited later.
    """

    allow_eval: bool = Field(False, description="Allow renderer-driven JavaScript eval")
    allow_upload: bool = Field(False, description="Allow file uploads")
    allow_download: bool = Field(False, description="Allow file downloads")
    allow_clipboard: bool = Field(False, description="Allow clipboard access")
    allow_profile_reuse: bool = Field(False, description="Allow broad browser profile reuse")
    allow_proxy: bool = Field(False, description="Allow arbitrary proxy configuration")

    model_config = {"extra": "forbid"}


class RenderConfig(BaseModel):
    """Configuration for explicit local browser rendering.

    Rendering is off by default. ``mode="agent-browser"`` renders every target
    URL through the configured backend. ``mode="fallback"`` keeps the normal
    HTTP fetch path and renders only pages that look like JS-only shells.
    """

    mode: Literal["off", "agent-browser", "fallback"] = Field(
        "off",
        description="Render mode: off, agent-browser, or fallback",
    )
    backend: Literal["agent-browser", "vercel-sandbox", "e2b-sandbox"] = Field(
        "agent-browser",
        description="Renderer backend to use when rendering is enabled",
    )
    timeout_seconds: float = Field(30.0, ge=1, description="Renderer timeout per page")
    wait_for: Literal["load", "domcontentloaded", "networkidle"] = Field(
        "load",
        description="Load state to wait for before reading HTML",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Target domains accepted before invoking the renderer. When omitted, "
            "docpull derives a narrow one-host allow-list from the target URL."
        ),
    )
    action_policy: RenderActionPolicy = Field(default_factory=RenderActionPolicy)
    viewport: RenderViewport = Field(default_factory=RenderViewport)
    max_html_bytes: ByteSize = Field(
        ByteSize(10 * 1024 * 1024),
        description="Maximum rendered HTML bytes accepted from the backend",
    )
    cloud_agent_browser_install: Literal["auto", "skip"] = Field(
        "skip",
        description=(
            "Install agent-browser inside cloud sandboxes before rendering, "
            "or skip when using a prebuilt sandbox/template. Prebuilt templates are recommended."
        ),
    )
    cloud_result_transport: Literal["auto", "stdout", "file"] = Field(
        "auto",
        description=(
            "How cloud sandboxes return the render payload. E2B can use file transport; "
            "Vercel CLI currently uses stdout."
        ),
    )
    cloud_max_estimated_cost_usd: float | None = Field(
        None,
        ge=0,
        description="Optional estimated per-render cloud spend cap in USD.",
    )
    cloud_artifact_path: str = Field(
        DEFAULT_CLOUD_ARTIFACT_PATH,
        description="Sandbox-local JSON result path used by file-capable cloud backends.",
    )
    cloud_agent_browser_binary: str = Field(
        "agent-browser",
        description="agent-browser executable name/path inside the selected cloud runtime.",
    )
    e2b_template: str | None = Field(
        None,
        description="Optional E2B template name with agent-browser preinstalled.",
    )
    vercel_runtime: Literal["node22"] = Field(
        "node22",
        description="Vercel Sandbox runtime used for cloud rendering.",
    )

    model_config = {"extra": "forbid"}

    @model_validator(mode="before")
    @classmethod
    def _parse_shorthand(cls, value: Any) -> Any:
        if isinstance(value, str):
            runtime_key = value.strip()
            runtime_to_backend = {
                "local": "agent-browser",
                "vercel": "vercel-sandbox",
                "e2b": "e2b-sandbox",
            }
            if runtime_key in runtime_to_backend:
                return {"mode": "agent-browser", "backend": runtime_to_backend[runtime_key]}
            return {"mode": value}
        if isinstance(value, dict):
            normalized = dict(value)
            runtime = normalized.pop("runtime", None)
            if runtime is not None:
                runtime_to_backend = {
                    "local": "agent-browser",
                    "vercel": "vercel-sandbox",
                    "e2b": "e2b-sandbox",
                }
                runtime_key = str(runtime).strip()
                try:
                    runtime_backend = runtime_to_backend[runtime_key]
                except KeyError as err:
                    raise ValueError("runtime must be local, vercel, or e2b") from err
                existing_backend = normalized.get("backend")
                if existing_backend is not None and existing_backend != runtime_backend:
                    raise ValueError("runtime and backend select different render backends")
                normalized["backend"] = runtime_backend
            return normalized
        return value

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _coerce_allowed_domains(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value

    @field_validator("allowed_domains")
    @classmethod
    def _normalize_allowed_domains(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for item in value:
            domain = _normalize_render_domain(item)
            if domain not in seen:
                normalized.append(domain)
                seen.add(domain)
        return normalized

    @field_validator("cloud_artifact_path", mode="before")
    @classmethod
    def _coerce_cloud_artifact_path(cls, value: Any) -> str:
        if value is None:
            return DEFAULT_CLOUD_ARTIFACT_PATH
        return str(value)

    @field_validator("cloud_artifact_path")
    @classmethod
    def _validate_cloud_artifact_path(cls, value: str) -> str:
        path = value.strip()
        if not path.startswith("/"):
            raise ValueError("cloud_artifact_path must be an absolute sandbox path")
        if "\x00" in path or "\n" in path or "\r" in path:
            raise ValueError("cloud_artifact_path must not contain control characters")
        return path

    @field_validator("cloud_agent_browser_binary", mode="before")
    @classmethod
    def _coerce_cloud_agent_browser_binary(cls, value: Any) -> str:
        if value is None:
            return "agent-browser"
        return str(value)

    @field_validator("cloud_agent_browser_binary")
    @classmethod
    def _validate_cloud_agent_browser_binary(cls, value: str) -> str:
        binary = value.strip()
        if not binary:
            raise ValueError("cloud_agent_browser_binary must not be empty")
        if "\x00" in binary or "\n" in binary or "\r" in binary:
            raise ValueError("cloud_agent_browser_binary must not contain control characters")
        return binary

    @field_validator("e2b_template", mode="before")
    @classmethod
    def _coerce_e2b_template(cls, value: Any) -> str | None:
        if value is None:
            return None
        template = str(value).strip()
        return template or None

    @field_validator("e2b_template")
    @classmethod
    def _validate_e2b_template(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(char in value for char in "\x00\r\n\t"):
            raise ValueError("e2b_template must not contain control characters")
        return value

    @property
    def enabled(self) -> bool:
        return self.mode != "off"


def _default_skill_agents() -> list[Literal["claude", "codex", "cursor"]]:
    return ["claude"]


class OutputConfig(BaseModel):
    """Configuration for output formatting and file saving."""

    directory: Path = Field(Path("./docs"), description="Output directory for fetched content")
    format: Literal["markdown", "json", "ndjson", "sqlite", "okf"] = Field(
        "markdown",
        description="Output format",
    )
    naming_strategy: Literal["full", "hierarchical"] = Field(
        "full",
        description=(
            "File naming strategy: 'full' (default, flattened with underscores) "
            "or 'hierarchical' (preserve URL path as nested directories)."
        ),
    )
    rich_metadata: bool = Field(
        False,
        description="Extract Open Graph, JSON-LD, and microdata metadata",
    )
    max_tokens_per_file: int | None = Field(
        None,
        ge=100,
        description="If set, split each page's Markdown into chunks of this token budget",
    )
    tokenizer: str = Field(
        "cl100k_base",
        description="Tokenizer encoding used when chunking (requires tiktoken)",
    )
    emit_chunks: bool = Field(
        False,
        description="Write one file/record per chunk instead of per page",
    )
    ndjson_filename: str = Field(
        "documents.ndjson",
        description="Output filename for NDJSON format (use '-' for stdout)",
    )
    skill_name: str | None = Field(
        None,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
        description=(
            "When set, write an agent skill/rule export after the crawl "
            "completes. Pages are saved with hierarchical naming so the "
            "corpus loads as a ready-to-use reference directory. Name must "
            "be a valid skill slug "
            "(lowercase letters, digits, hyphens)."
        ),
    )
    skill_description: str | None = Field(
        None,
        description=(
            "Override for the skill's `description` frontmatter. When None, "
            "docpull derives a description from the first page's "
            "OpenGraph / JSON-LD metadata."
        ),
    )
    skill_agents: list[Literal["claude", "codex", "cursor"]] = Field(
        default_factory=_default_skill_agents,
        description=(
            "Agent integrations to export when skill_name is set. "
            "Claude Code and Codex receive SKILL.md folders; Cursor receives "
            "a .cursor/rules/*.mdc project rule."
        ),
    )
    skill_root_dir: Path | None = Field(
        None,
        description=(
            "Skill root directory for generated manifests. When None, the "
            "output directory itself is treated as the skill root."
        ),
    )
    skill_install_targets: bool = Field(
        False,
        description=(
            "When True, copy/write requested agent integrations into their "
            "default project roots in addition to the configured skill root."
        ),
    )

    model_config = {"extra": "forbid"}


def _expand_env_var(value: str | None) -> str | None:
    """Expand environment variable references in a string.

    Supports $VAR and ${VAR} syntax. Returns original value if
    the env var is not set.
    """
    import os
    import re

    if value is None:
        return None

    # Match $VAR or ${VAR}
    pattern = r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)"

    def replace(match: re.Match) -> str:
        var_name = match.group(1) or match.group(2)
        return os.environ.get(var_name, match.group(0))

    return re.sub(pattern, replace, value)


_HEADER_INJECTION_RE = re.compile(r"[\r\n\x00]")


def _reject_header_injection(value: str | None, field_name: str) -> str | None:
    """Reject values containing CR, LF, or null bytes (HTTP header injection)."""
    if value is not None and _HEADER_INJECTION_RE.search(value):
        raise ValueError(f"{field_name} must not contain CR, LF, or null characters")
    return value


class AuthConfig(BaseModel):
    """Configuration for authentication to protected web sources.

    Supports environment variable expansion in sensitive fields using
    $VAR or ${VAR} syntax. For example:
        --auth-bearer '$GITHUB_TOKEN'
        --auth-basic 'user:${API_PASSWORD}'
    """

    type: AuthType = Field(AuthType.NONE, description="Authentication type")
    token: str | None = Field(None, description="Bearer token or API key")
    username: str | None = Field(None, description="Username for basic auth")
    password: str | None = Field(None, description="Password for basic auth")
    cookie: str | None = Field(None, description="Cookie string for cookie auth")
    header_name: str | None = Field(None, description="Custom header name for header auth")
    header_value: str | None = Field(None, description="Custom header value for header auth")
    policy: Literal["none", "explicit-private", "public-token-only"] = Field(
        "none",
        description=(
            "Authenticated source mode. 'none' is the public/default mode; "
            "'explicit-private' labels the run as private; "
            "'public-token-only' is for public resources that require a token."
        ),
    )

    model_config = {"extra": "forbid"}

    @field_validator("header_name", "header_value", mode="before")
    @classmethod
    def _reject_crlf_in_headers(cls, v: str | None, info: Any) -> str | None:
        return _reject_header_injection(v, info.field_name)

    def model_post_init(self, _context: object) -> None:
        """Expand environment variables in sensitive fields after init."""
        # Use object.__setattr__ to bypass frozen model if needed
        if self.token:
            object.__setattr__(self, "token", _expand_env_var(self.token))
        if self.password:
            object.__setattr__(self, "password", _expand_env_var(self.password))
        if self.cookie:
            object.__setattr__(self, "cookie", _expand_env_var(self.cookie))
        if self.header_value:
            object.__setattr__(self, "header_value", _expand_env_var(self.header_value))
            # Re-check after env var expansion (env vars could introduce CRLF)
            _reject_header_injection(self.header_value, "header_value")
        if self.header_name:
            _reject_header_injection(self.header_name, "header_name")


class NetworkConfig(BaseModel):
    """Configuration for HTTP client and network behavior."""

    proxy: str | None = Field(None, description="HTTP, HTTPS, or SOCKS proxy URL")
    user_agent: str | None = Field(None, description="Custom User-Agent header")
    insecure_tls: bool = Field(
        False,
        description="Deprecated insecure option; docpull always verifies TLS certificates",
    )
    max_retries: int = Field(3, ge=0, description="Maximum retry attempts for failed requests")
    log_retry_warnings: bool = Field(True, description="Log retryable HTTP failures before retrying")
    connect_timeout: int = Field(10, ge=1, description="Connection timeout in seconds")
    read_timeout: int = Field(30, ge=5, description="Read timeout in seconds")
    require_pinned_dns: bool = Field(
        False,
        description=(
            "Refuse configurations where DNS pinning is delegated to a proxy. "
            "When True, supplying a proxy raises a configuration error so an "
            "agent-driven workflow cannot silently fall back to a weaker SSRF "
            "posture. Default False to preserve corporate-proxy use cases."
        ),
    )

    model_config = {"extra": "forbid"}

    @field_validator("insecure_tls")
    @classmethod
    def _reject_insecure_tls(cls, value: bool) -> bool:
        if value:
            raise ValueError("insecure_tls is not supported; TLS certificate verification is mandatory")
        return value

    @field_validator("user_agent", mode="before")
    @classmethod
    def _reject_crlf_in_user_agent(cls, v: str | None, info: Any) -> str | None:
        return _reject_header_injection(v, info.field_name)


class PerformanceConfig(BaseModel):
    """Configuration for performance tuning."""

    cpu_workers: int = Field(
        4,
        ge=1,
        description="Thread pool workers for CPU-bound operations (metadata extraction)",
    )

    model_config = {"extra": "forbid"}


class CacheConfig(BaseModel):
    """Configuration for caching and incremental updates."""

    enabled: bool = Field(False, description="Enable caching for incremental updates")
    directory: Path = Field(Path(".docpull-cache"), description="Cache directory")
    ttl_days: int | None = Field(
        30,
        ge=1,
        description="Days before cache entries expire (None = no expiry)",
    )
    skip_unchanged: bool = Field(
        True,
        description="Skip pages with unchanged ETag/Last-Modified/content hash",
    )
    resume: bool = Field(
        False,
        description="Resume from previous interrupted run (requires caching enabled)",
    )

    model_config = {"extra": "forbid"}


class BudgetConfig(BaseModel):
    """Configuration for paid-capable provider/cloud spend guards."""

    maximum_paid_cost_usd: float | None = Field(
        None,
        ge=0,
        description=(
            "Maximum paid-capable spend allowed for this run. Set 0 to require "
            "zero paid provider/cloud calls."
        ),
    )

    model_config = {"extra": "forbid"}


class DocpullConfig(BaseModel):
    """
    Root configuration model for docpull.

    Example:
        config = DocpullConfig(
            profile=ProfileName.RAG,
            url="https://docs.anthropic.com",
            output=OutputConfig(directory=Path("./docs/anthropic"))
        )

    YAML format:
        profile: rag
        url: https://docs.anthropic.com
        crawl:
          max_pages: 500
        output:
          directory: ./my-docs
    """

    # Core settings
    profile: ProfileName = Field(
        ProfileName.CUSTOM,
        description="Built-in profile to apply (rag, mirror, quick, custom)",
    )
    url: str | None = Field(None, description="Target URL to fetch")

    # Nested configuration sections
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    content_filter: ContentFilterConfig = Field(default_factory=ContentFilterConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        "INFO",
        description="Logging level",
    )
    log_file: Path | None = Field(None, description="Log file path")
    dry_run: bool = Field(False, description="Simulate without writing files")

    model_config = {"extra": "forbid"}

    def to_yaml(self) -> str:
        """Serialize config to YAML string."""
        import yaml

        return yaml.dump(self.model_dump(mode="json", exclude_none=True), default_flow_style=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> DocpullConfig:
        """Load config from YAML string."""
        import yaml

        data = yaml.safe_load(yaml_str)
        return cls.model_validate(data)
