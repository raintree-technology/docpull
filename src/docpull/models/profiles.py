"""Built-in configuration profiles for common use cases."""

from __future__ import annotations

from typing import Any

from .config import DocpullConfig, ProfileName

PROFILES: dict[ProfileName, dict[str, Any]] = {
    ProfileName.RAG: {
        # Optimized for retrieval-augmented generation / LLM training data
        "content_filter": {
            "streaming_dedup": True,
        },
        "output": {
            "rich_metadata": True,
        },
        "crawl": {
            "max_concurrent": 20,
        },
    },
    ProfileName.MIRROR: {
        # Full site mirror for offline archive
        "crawl": {
            "max_depth": 10,
            "max_concurrent": 5,  # Be polite
        },
        "output": {
            "naming_strategy": "hierarchical",
        },
        "cache": {
            "enabled": True,
            "skip_unchanged": True,  # Conditional GET via If-None-Match
        },
    },
    ProfileName.QUICK: {
        # Fast sampling for exploration
        "crawl": {
            "max_pages": 50,
            "max_depth": 2,
            "max_concurrent": 20,
        },
    },
    ProfileName.LLM: {
        # Token-aware output, streaming NDJSON, fail-loud on JS-only pages.
        # This is what "AI-ready Markdown" should actually mean: predictable
        # chunk sizes, stable hashes, one-record-per-line streaming.
        "crawl": {
            "max_concurrent": 20,
        },
        "content_filter": {
            "streaming_dedup": True,
            "strict_js_required": False,
            "enable_special_cases": True,
        },
        "output": {
            "format": "ndjson",
            "rich_metadata": True,
            "max_tokens_per_file": 4000,
            "emit_chunks": True,
        },
    },
    ProfileName.CUSTOM: {
        # No overrides - use explicit config
    },
}


def apply_profile(config: DocpullConfig) -> DocpullConfig:
    """
    Apply profile defaults to config, preserving user overrides.

    Profile values override Pydantic defaults, but explicit user values
    take precedence over profile values.

    Args:
        config: The configuration with a profile specified

    Returns:
        A new DocpullConfig with profile defaults applied

    Example:
        >>> config = DocpullConfig(profile=ProfileName.RAG)
        >>> applied = apply_profile(config)
        >>> applied.content_filter.streaming_dedup
        True
        >>> applied.output.rich_metadata
        True
    """
    if config.profile == ProfileName.CUSTOM:
        return config

    profile_overrides = PROFILES.get(config.profile, {})
    if not profile_overrides:
        return config

    # Get current config as dict
    config_dict = config.model_dump()

    def deep_update(base: dict, overrides: dict) -> dict:
        """
        Deep update base dict with overrides.

        For nested dicts, recursively merge. For other values, override.
        """
        result = base.copy()
        for key, override_value in overrides.items():
            if key in result and isinstance(result[key], dict) and isinstance(override_value, dict):
                result[key] = deep_update(result[key], override_value)
            else:
                result[key] = override_value
        return result

    # Apply profile overrides on top of config
    merged = deep_update(config_dict, profile_overrides)
    return DocpullConfig.model_validate(merged)
