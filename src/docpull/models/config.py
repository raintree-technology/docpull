"""Pydantic configuration models for docpull v2.0."""

from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class ProfileName(str, Enum):
    """Built-in configuration profiles."""

    RAG = "rag"
    MIRROR = "mirror"
    QUICK = "quick"
    CUSTOM = "custom"


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

    max_pages: Optional[int] = Field(None, description="Maximum pages to fetch (None = unlimited)")
    max_depth: int = Field(5, ge=1, description="Maximum crawl depth from starting URL")
    max_concurrent: int = Field(10, ge=1, description="Maximum concurrent requests globally")
    rate_limit: float = Field(0.5, ge=0, description="Minimum seconds between requests to same host")
    per_host_concurrent: int = Field(3, ge=1, description="Maximum concurrent requests per host")
    include_paths: list[str] = Field(default_factory=list, description="URL path patterns to include")
    exclude_paths: list[str] = Field(default_factory=list, description="URL path patterns to exclude")
    javascript: bool = Field(False, description="Enable JavaScript rendering via Playwright")

    model_config = {"extra": "forbid"}


class ContentFilterConfig(BaseModel):
    """Configuration for content filtering and deduplication."""

    language: Optional[str] = Field(
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
    max_file_size: Optional[ByteSize] = Field(
        None,
        description="Maximum size per file (e.g., '200kb', '1mb')",
    )
    max_total_size: Optional[ByteSize] = Field(
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


class NetworkConfig(BaseModel):
    """Configuration for HTTP client and network behavior."""

    proxy: Optional[str] = Field(None, description="HTTP/HTTPS proxy URL")
    user_agent: Optional[str] = Field(None, description="Custom User-Agent header")
    max_retries: int = Field(3, ge=0, description="Maximum retry attempts for failed requests")
    connect_timeout: int = Field(10, ge=1, description="Connection timeout in seconds")
    read_timeout: int = Field(30, ge=5, description="Read timeout in seconds")

    model_config = {"extra": "forbid"}


class PerformanceConfig(BaseModel):
    """Configuration for performance tuning."""

    cpu_workers: int = Field(
        4,
        ge=1,
        description="Thread pool workers for CPU-bound operations (metadata extraction)",
    )
    browser_contexts: int = Field(
        5,
        ge=1,
        description="Maximum browser contexts for JS rendering",
    )

    model_config = {"extra": "forbid"}


class IntegrationConfig(BaseModel):
    """Configuration for external integrations."""

    git_commit: bool = Field(False, description="Auto-commit changes to git")
    git_message: str = Field(
        "Update docs - {date}",
        description="Git commit message template",
    )
    archive: bool = Field(False, description="Create archive after fetch")
    archive_format: Literal["tar.gz", "tar.bz2", "tar.xz", "zip"] = Field(
        "tar.gz",
        description="Archive format",
    )
    post_process_hook: Optional[Path] = Field(
        None,
        description="Path to post-processing hook script",
    )

    model_config = {"extra": "forbid"}


class CacheConfig(BaseModel):
    """Configuration for caching and incremental updates."""

    enabled: bool = Field(False, description="Enable caching for incremental updates")
    directory: Path = Field(Path(".docpull-cache"), description="Cache directory")
    ttl_days: Optional[int] = Field(
        30,
        ge=1,
        description="Days before cache entries expire (None = no expiry)",
    )
    skip_unchanged: bool = Field(
        True,
        description="Skip pages with unchanged ETag/Last-Modified/content hash",
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
    url: Optional[str] = Field(None, description="Target URL to fetch")

    # Nested configuration sections
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    content_filter: ContentFilterConfig = Field(default_factory=ContentFilterConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    performance: PerformanceConfig = Field(default_factory=PerformanceConfig)
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        "INFO",
        description="Logging level",
    )
    log_file: Optional[Path] = Field(None, description="Log file path")
    dry_run: bool = Field(False, description="Simulate without writing files")

    model_config = {"extra": "forbid"}

    def to_yaml(self) -> str:
        """Serialize config to YAML string."""
        import yaml

        return yaml.dump(self.model_dump(mode="json", exclude_none=True), default_flow_style=False)

    @classmethod
    def from_yaml(cls, yaml_str: str) -> "DocpullConfig":
        """Load config from YAML string."""
        import yaml

        data = yaml.safe_load(yaml_str)
        return cls.model_validate(data)

    @classmethod
    def from_yaml_file(cls, path: Path) -> "DocpullConfig":
        """Load config from YAML file."""
        return cls.from_yaml(path.read_text())
