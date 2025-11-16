import json
from pathlib import Path
from typing import Any, Optional

try:
    import yaml  # type: ignore
except ImportError:
    yaml = None  # type: ignore


class FetcherConfig:
    """Configuration for documentation fetchers."""

    def __init__(
        self,
        output_dir: str = "./docs",
        rate_limit: float = 0.5,
        skip_existing: bool = True,
        log_level: str = "INFO",
        log_file: Optional[str] = None,
        sources: Optional[list[str]] = None,
        dry_run: bool = False,
        # v1.2.0 features
        language: Optional[str] = None,
        exclude_languages: Optional[list[str]] = None,
        deduplicate: bool = False,
        keep_variant: Optional[str] = None,
        max_file_size: Optional[str] = None,
        max_total_size: Optional[str] = None,
        exclude_sections: Optional[list[str]] = None,
        include_paths: Optional[list[str]] = None,
        exclude_paths: Optional[list[str]] = None,
        output_format: str = "markdown",
        naming_strategy: str = "full",
        create_index: bool = False,
        extract_metadata: bool = False,
        update_only_changed: bool = False,
        incremental: bool = False,
        cache_dir: str = ".docpull-cache",
        git_commit: bool = False,
        git_message: str = "Update docs - {date}",
        archive: bool = False,
        archive_format: str = "tar.gz",
        post_process_hook: Optional[str] = None,
    ):
        """
        Initialize configuration.

        Args:
            output_dir: Directory to save documentation
            rate_limit: Seconds between requests
            skip_existing: Skip existing files
            log_level: Logging level
            log_file: Optional log file path
            sources: List of sources to fetch (e.g., ['stripe', 'plaid'])
            dry_run: Dry run mode (don't download files)
            language: Include only this language (e.g., 'en')
            exclude_languages: Exclude these languages
            deduplicate: Remove duplicate files
            keep_variant: Keep files matching this pattern when deduplicating
            max_file_size: Maximum file size (e.g., '200kb')
            max_total_size: Maximum total download size
            exclude_sections: Remove sections with these header names
            include_paths: Only crawl URLs matching these patterns
            exclude_paths: Skip URLs matching these patterns
            format: Output format (markdown, toon, json, sqlite)
            naming_strategy: File naming strategy (full, short, flat, hierarchical)
            create_index: Create INDEX.md with navigation
            extract_metadata: Extract metadata to metadata.json
            update_only_changed: Only download changed files
            incremental: Enable incremental mode
            cache_dir: Cache directory for update detection
            git_commit: Automatically commit changes
            git_message: Commit message template
            archive: Create compressed archive
            archive_format: Archive format (tar.gz, tar.bz2, tar.xz, zip)
            post_process_hook: Path to post-processing hook script
        """
        self.output_dir = Path(output_dir)
        self.rate_limit = rate_limit
        self.skip_existing = skip_existing
        self.log_level = log_level
        self.log_file = log_file
        self.sources = sources or ["plaid", "stripe"]
        self.dry_run = dry_run

        # v1.2.0 features
        self.language = language
        self.exclude_languages = exclude_languages or []
        self.deduplicate = deduplicate
        self.keep_variant = keep_variant
        self.max_file_size = max_file_size
        self.max_total_size = max_total_size
        self.exclude_sections = exclude_sections or []
        self.include_paths = include_paths or []
        self.exclude_paths = exclude_paths or []
        self.output_format = output_format
        self.naming_strategy = naming_strategy
        self.create_index = create_index
        self.extract_metadata = extract_metadata
        self.update_only_changed = update_only_changed
        self.incremental = incremental
        self.cache_dir = Path(cache_dir)
        self.git_commit = git_commit
        self.git_message = git_message
        self.archive = archive
        self.archive_format = archive_format
        self.post_process_hook = post_process_hook

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "FetcherConfig":
        """
        Create configuration from dictionary.

        Args:
            config_dict: Configuration dictionary

        Returns:
            FetcherConfig instance

        Raises:
            ValueError: If configuration values are invalid
        """
        # Validate output_dir doesn't contain path traversal
        output_dir = str(config_dict.get("output_dir", "./docs"))
        if ".." in output_dir or output_dir.startswith("/etc") or output_dir.startswith("/sys"):
            raise ValueError("Invalid output directory path")

        # Validate rate_limit is reasonable
        rate_limit = config_dict.get("rate_limit", 0.5)
        if not isinstance(rate_limit, (int, float)) or rate_limit < 0 or rate_limit > 60:
            raise ValueError("rate_limit must be between 0 and 60")

        # Validate sources
        valid_sources = {"bun", "d3", "nextjs", "plaid", "react", "stripe", "tailwind", "turborepo"}
        sources = config_dict.get("sources", ["plaid", "stripe"])
        if not all(s in valid_sources for s in sources):
            raise ValueError(f"Invalid sources. Must be from: {valid_sources}")

        # Validate log_level
        log_level = config_dict.get("log_level", "INFO")
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if log_level.upper() not in valid_log_levels:
            raise ValueError(f"Invalid log_level. Must be one of: {valid_log_levels}")

        return cls(
            output_dir=output_dir,
            rate_limit=rate_limit,
            skip_existing=config_dict.get("skip_existing", True),
            log_level=log_level,
            log_file=config_dict.get("log_file"),
            sources=sources,
            dry_run=config_dict.get("dry_run", False),
        )

    @classmethod
    def from_yaml(cls, yaml_path: Path) -> "FetcherConfig":
        """
        Load configuration from YAML file.

        Args:
            yaml_path: Path to YAML config file

        Returns:
            FetcherConfig instance

        Raises:
            ImportError: If pyyaml is not installed
            FileNotFoundError: If config file doesn't exist
        """
        if yaml is None:
            raise ImportError("PyYAML is required for YAML config. Install with: pip install pyyaml")

        if not yaml_path.exists():
            raise FileNotFoundError(f"Config file not found: {yaml_path}")

        with open(yaml_path) as f:
            config_dict = yaml.safe_load(f)

        return cls.from_dict(config_dict)

    @classmethod
    def from_json(cls, json_path: Path) -> "FetcherConfig":
        """
        Load configuration from JSON file.

        Args:
            json_path: Path to JSON config file

        Returns:
            FetcherConfig instance

        Raises:
            FileNotFoundError: If config file doesn't exist
        """
        if not json_path.exists():
            raise FileNotFoundError(f"Config file not found: {json_path}")

        with open(json_path) as f:
            config_dict = json.load(f)

        return cls.from_dict(config_dict)

    @classmethod
    def from_file(cls, config_path: Path) -> "FetcherConfig":
        """
        Load configuration from file (auto-detect format).

        Args:
            config_path: Path to config file

        Returns:
            FetcherConfig instance
        """
        suffix = config_path.suffix.lower()

        if suffix in [".yaml", ".yml"]:
            return cls.from_yaml(config_path)
        elif suffix == ".json":
            return cls.from_json(config_path)
        else:
            raise ValueError(f"Unsupported config file format: {suffix}")

    def to_dict(self) -> dict[str, Any]:
        """
        Convert configuration to dictionary.

        Returns:
            Configuration as dictionary
        """
        config = {
            "output_dir": str(self.output_dir),
            "rate_limit": self.rate_limit,
            "skip_existing": self.skip_existing,
            "log_level": self.log_level,
            "log_file": self.log_file,
            "sources": self.sources,
            "dry_run": self.dry_run,
        }

        # Add v1.2.0 fields if set
        if self.language:
            config["language"] = self.language
        if self.exclude_languages:
            config["exclude_languages"] = self.exclude_languages
        if self.deduplicate:
            config["deduplicate"] = self.deduplicate
        if self.keep_variant:
            config["keep_variant"] = self.keep_variant
        if self.max_file_size:
            config["max_file_size"] = self.max_file_size
        if self.max_total_size:
            config["max_total_size"] = self.max_total_size
        if self.exclude_sections:
            config["exclude_sections"] = self.exclude_sections
        if self.include_paths:
            config["include_paths"] = self.include_paths
        if self.exclude_paths:
            config["exclude_paths"] = self.exclude_paths
        if self.output_format != "markdown":
            config["format"] = self.output_format
        if self.naming_strategy != "full":
            config["naming_strategy"] = self.naming_strategy
        if self.create_index:
            config["create_index"] = self.create_index
        if self.extract_metadata:
            config["extract_metadata"] = self.extract_metadata
        if self.update_only_changed:
            config["update_only_changed"] = self.update_only_changed
        if self.incremental:
            config["incremental"] = self.incremental
        if str(self.cache_dir) != ".docpull-cache":
            config["cache_dir"] = str(self.cache_dir)
        if self.git_commit:
            config["git_commit"] = self.git_commit
        if self.git_message != "Update docs - {date}":
            config["git_message"] = self.git_message
        if self.archive:
            config["archive"] = self.archive
        if self.archive_format != "tar.gz":
            config["archive_format"] = self.archive_format
        if self.post_process_hook:
            config["post_process_hook"] = self.post_process_hook

        return config

    def save_yaml(self, yaml_path: Path) -> None:
        """
        Save configuration to YAML file.

        Args:
            yaml_path: Path to save YAML config

        Raises:
            ImportError: If pyyaml is not installed
        """
        if yaml is None:
            raise ImportError("PyYAML is required for YAML config. Install with: pip install pyyaml")

        with open(yaml_path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)

    def save_json(self, json_path: Path) -> None:
        """
        Save configuration to JSON file.

        Args:
            json_path: Path to save JSON config
        """
        with open(json_path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)
