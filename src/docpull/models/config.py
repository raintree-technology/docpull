"""Pydantic configuration models for docpull v2.0."""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ProfileName(str, Enum):
    """Built-in configuration profiles."""

    RAG = "rag"
    MIRROR = "mirror"
    QUICK = "quick"
    CUSTOM = "custom"


class AuthType(str, Enum):
    """Authentication types for protected documentation sites."""

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
    def __get_pydantic_core_schema__(cls, source: Any, handler: Any) -> Any:
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
            except ValueError:
                pass
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

    model_config = {"extra": "forbid"}


class ContentFilterConfig(BaseModel):
    """Configuration for content filtering and deduplication."""

    language: str | None = Field(
        None,
        pattern=r"^[a-z]{2}$",
        description="ISO 639-1 language code to keep (e.g., 'en')",
    )
    exclude_languages: list[str] = Field(
        default_factory=list,
        description="ISO 639-1 language codes to exclude",
    )
    deduplicate: bool = Field(False, description="Remove duplicate content in post-processing")
    streaming_dedup: bool = Field(
        False,
        description="Enable real-time deduplication during fetch (more efficient)",
    )
    max_file_size: ByteSize | None = Field(
        None,
        description="Maximum size per file (e.g., '200kb', '1mb')",
    )
    max_total_size: ByteSize | None = Field(
        None,
        description="Maximum total download size (e.g., '100mb', '1gb')",
    )
    exclude_sections: list[str] = Field(
        default_factory=list,
        description="Header patterns to exclude from output",
    )

    model_config = {"extra": "forbid"}


class OutputConfig(BaseModel):
    """Configuration for output formatting and file saving."""

    directory: Path = Field(Path("./docs"), description="Output directory for fetched content")
    format: Literal["markdown", "json", "sqlite"] = Field(
        "markdown",
        description="Output format",
    )
    naming_strategy: Literal["full", "short", "flat", "hierarchical"] = Field(
        "full",
        description="File naming strategy for URL-to-path conversion",
    )
    create_index: bool = Field(False, description="Generate INDEX.md with navigation")
    rich_metadata: bool = Field(
        False,
        description="Extract Open Graph, JSON-LD, and microdata metadata",
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
    """Configuration for authentication to protected documentation sites.

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

    model_config = {"extra": "forbid"}

    @field_validator("header_name", "header_value", mode="before")
    @classmethod
    def _reject_crlf_in_headers(cls, v: str | None, info: Any) -> str | None:
        return _reject_header_injection(v, info.field_name)

    def model_post_init(self, __context: object) -> None:
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

    proxy: str | None = Field(None, description="HTTP/HTTPS proxy URL")
    user_agent: str | None = Field(None, description="Custom User-Agent header")
    insecure_tls: bool = Field(
        False,
        description="Deprecated insecure option; docpull always verifies TLS certificates",
    )
    max_retries: int = Field(3, ge=0, description="Maximum retry attempts for failed requests")
    connect_timeout: int = Field(10, ge=1, description="Connection timeout in seconds")
    read_timeout: int = Field(30, ge=5, description="Read timeout in seconds")

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

    @classmethod
    def from_yaml_file(cls, path: Path) -> DocpullConfig:
        """Load config from YAML file."""
        return cls.from_yaml(path.read_text())
