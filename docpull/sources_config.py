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

    # Output (optional, defaults to source name)
    output: Optional[str] = None

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
    incremental: bool = False
    extract_metadata: bool = False
    hooks: Optional[str] = None

    def __getitem__(self, key: str) -> Any:
        """Allow dict-style access for backward compatibility.

        Args:
            key: Attribute name

        Returns:
            Attribute value
        """
        return getattr(self, key)

    @property
    def output_dir(self) -> Optional[str]:
        """Alias for output field for backward compatibility.

        Returns:
            Output directory path
        """
        return self.output

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

        Raises:
            ValueError: If required url field is missing
        """
        # Validate required url field
        if "url" not in data:
            raise ValueError("Source configuration must contain 'url' field")

        # Handle output_dir alias (map to output field)
        data = data.copy()  # Don't modify original
        if "output_dir" in data:
            data["output"] = data.pop("output_dir")

        # Filter out unknown keys
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in valid_keys}

        return cls(**filtered_data)


class GlobalConfig:
    """Wrapper for global configuration settings with attribute access."""

    def __init__(self, settings: dict[str, Any], parent: Optional["SourcesConfiguration"] = None):
        """Initialize with settings dict and optional parent config.

        Args:
            settings: Global settings dictionary
            parent: Parent SourcesConfiguration instance
        """
        self._settings = settings
        self._parent = parent

    def __getattr__(self, name: str) -> Any:
        """Get setting by attribute name.

        Args:
            name: Setting name

        Returns:
            Setting value

        Raises:
            AttributeError: If setting not found
        """
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        # Check parent SourcesConfiguration attributes first
        if self._parent is not None and hasattr(self._parent, name):
            return getattr(self._parent, name)

        # Then check global_settings
        if name in self._settings:
            value = self._settings[name]
            # Convert string paths to Path objects
            if name == "output_dir" and isinstance(value, str):
                return Path(value)
            return value
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")


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

    @property
    def global_config(self) -> GlobalConfig:
        """Get global config with attribute access.

        Returns:
            GlobalConfig wrapper
        """
        return GlobalConfig(self.global_settings, parent=self)

    def add_source(self, name: str, config: SourceConfig) -> None:
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

    def apply_global_settings(self) -> None:
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

        Raises:
            ValueError: If sources key is missing or empty
        """
        # Validate sources key exists
        if "sources" not in data:
            raise ValueError("Configuration must contain 'sources' key")

        # Validate sources is not empty
        if not data["sources"]:
            raise ValueError("Sources cannot be empty")

        sources = {}

        for name, source_data in data["sources"].items():
            sources[name] = SourceConfig.from_dict(source_data)

        # Build global settings from explicit global_settings or top-level settings
        global_settings = data.get("global_settings", {}).copy()

        # Known SourcesConfiguration fields that shouldn't go into global_settings
        known_fields = {
            "sources",
            "global_settings",
            "git_commit",
            "git_message",
            "archive",
            "archive_format",
        }

        # Add any top-level settings that aren't known fields to global_settings
        for key, value in data.items():
            if key not in known_fields and key not in global_settings:
                global_settings[key] = value

        return cls(
            sources=sources,
            global_settings=global_settings,
            git_commit=data.get("git_commit", False),
            git_message=data.get("git_message", "Update docs - {date}"),
            archive=data.get("archive", False),
            archive_format=data.get("archive_format", "tar.gz"),
        )

    def save(self, file_path: Path) -> None:
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

    @classmethod
    def from_yaml(cls, file_path: Path) -> "SourcesConfiguration":
        """Load configuration from YAML file (alias for load).

        Args:
            file_path: Config file path

        Returns:
            SourcesConfiguration instance
        """
        return cls.load(file_path)

    @classmethod
    def generate_template(cls, output_file: Path) -> None:
        """Generate an example configuration template.

        Args:
            output_file: Path to save template
        """
        create_example_config(output_file)


def create_example_config(output_file: Path):
    """Create an example sources configuration file.

    Args:
        output_file: Path to save example config
    """
    config = SourcesConfiguration()

    # Add example sources
    config.add_source(
        "example",
        SourceConfig(
            url="https://example.com/docs",
            output="./docs/example",
            language="en",
            create_index=True,
        ),
    )

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
            output_format="markdown",
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
