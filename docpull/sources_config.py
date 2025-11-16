"""Multi-source configuration system for batch fetching."""

import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class SourceConfig:
    """Configuration for a single documentation source."""

    # Required
    url: str
    output: str

    # Fetching options
    max_pages: Optional[int] = None
    max_depth: Optional[int] = 5
    max_concurrent: Optional[int] = 10
    rate_limit: Optional[float] = 0.5
    javascript: bool = False

    # Filtering
    include_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    language: Optional[str] = None
    exclude_languages: list[str] = field(default_factory=list)

    # Processing
    deduplicate: bool = False
    keep_variant: Optional[str] = None
    max_file_size: Optional[str] = None
    max_total_size: Optional[str] = None
    exclude_sections: list[str] = field(default_factory=list)

    # Output
    output_format: str = "markdown"
    naming_strategy: str = "full"
    create_index: bool = False

    # Advanced
    cache_enabled: bool = True
    update_only_changed: bool = False
    hooks: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Config as dict
        """
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceConfig":
        """Create from dictionary.

        Args:
            data: Config dict

        Returns:
            SourceConfig instance
        """
        # Filter out unknown keys
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}

        return cls(**filtered_data)


@dataclass
class SourcesConfiguration:
    """Configuration for multiple documentation sources."""

    sources: dict[str, SourceConfig] = field(default_factory=dict)

    # Global settings (apply to all sources unless overridden)
    global_settings: dict[str, Any] = field(default_factory=dict)

    # Post-processing
    git_commit: bool = False
    git_message: str = "Update docs - {date}"
    archive: bool = False
    archive_format: str = "tar.gz"

    def add_source(self, name: str, config: SourceConfig):
        """Add a source configuration.

        Args:
            name: Source name
            config: Source configuration
        """
        self.sources[name] = config

    def get_source(self, name: str) -> Optional[SourceConfig]:
        """Get a source configuration by name.

        Args:
            name: Source name

        Returns:
            SourceConfig or None
        """
        return self.sources.get(name)

    def apply_global_settings(self):
        """Apply global settings to all sources that don't override them."""
        for source_config in self.sources.values():
            for key, value in self.global_settings.items():
                if hasattr(source_config, key):
                    current_value = getattr(source_config, key)

                    # Only apply if source doesn't override
                    # (check if it's the default value)
                    field_info = source_config.__dataclass_fields__[key]
                    if current_value == field_info.default:
                        setattr(source_config, key, value)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary.

        Returns:
            Config as dict
        """
        return {
            "sources": {name: config.to_dict() for name, config in self.sources.items()},
            "global_settings": self.global_settings,
            "git_commit": self.git_commit,
            "git_message": self.git_message,
            "archive": self.archive,
            "archive_format": self.archive_format,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourcesConfiguration":
        """Create from dictionary.

        Args:
            data: Config dict

        Returns:
            SourcesConfiguration instance
        """
        sources = {}

        for name, source_data in data.get("sources", {}).items():
            sources[name] = SourceConfig.from_dict(source_data)

        return cls(
            sources=sources,
            global_settings=data.get("global_settings", {}),
            git_commit=data.get("git_commit", False),
            git_message=data.get("git_message", "Update docs - {date}"),
            archive=data.get("archive", False),
            archive_format=data.get("archive_format", "tar.gz"),
        )

    def save(self, file_path: Path):
        """Save configuration to YAML file.

        Args:
            file_path: Output file path
        """
        file_path = Path(file_path)

        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

        logger.info(f"Saved sources configuration to {file_path}")

    @classmethod
    def load(cls, file_path: Path) -> "SourcesConfiguration":
        """Load configuration from YAML file.

        Args:
            file_path: Config file path

        Returns:
            SourcesConfiguration instance
        """
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Config file not found: {file_path}")

        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        config = cls.from_dict(data)
        config.apply_global_settings()

        logger.info(f"Loaded {len(config.sources)} sources from {file_path}")

        return config


def create_example_config(output_file: Path):
    """Create an example sources configuration file.

    Args:
        output_file: Path to save example config
    """
    config = SourcesConfiguration()

    # Add example sources
    config.add_source(
        "anthropic",
        SourceConfig(
            url="https://docs.anthropic.com",
            output="./docs/anthropic",
            language="en",
            max_total_size="20mb",
            create_index=True,
            exclude_sections=["Changelog"],
        ),
    )

    config.add_source(
        "claude-code",
        SourceConfig(
            url="https://code.claude.com/docs",
            output="./docs/claude-code",
            language="en",
            format="markdown",
            create_index=True,
        ),
    )

    config.add_source(
        "aptos",
        SourceConfig(
            url="https://aptos.dev",
            output="./docs/aptos",
            deduplicate=True,
            keep_variant="mainnet",
            max_file_size="200kb",
            include_paths=["build/*"],
            exclude_paths=["*/archive/*", "*/deprecated/*"],
        ),
    )

    # Global settings
    config.global_settings = {
        "rate_limit": 0.5,
        "max_concurrent": 10,
    }

    # Post-processing
    config.git_commit = True
    config.git_message = "Update docs - {date}"
    config.archive = False

    config.save(output_file)

    logger.info(f"Created example config at {output_file}")
