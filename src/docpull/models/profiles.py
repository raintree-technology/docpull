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
        # Full site mirror for offline archive.
        #
        # NOTE: hierarchical naming is intentionally NOT in this profile
        # in 2.x to preserve existing users' output paths. They opt in via
        # `--naming-strategy hierarchical` or `output.naming_strategy:
        # hierarchical` in YAML. In 3.0 the Mirror default flips to
        # hierarchical (per the SemVer plan).
        "crawl": {
            "max_depth": 10,
            "max_concurrent": 5,  # Be polite
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

    # Two views of the user's config:
    #   - `defaults_dict` contains Pydantic defaults (and user values).
    #   - `explicit` contains ONLY the fields the user actually set.
    # We merge profile values UNDER the explicit user values so the user
    # always wins on collision, while profile values still override
    # Pydantic defaults. This honors the docstring contract.
    defaults_dict = config.model_dump()
    explicit = _explicit_fields(config)

    def merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
        """``over`` wins over ``base`` per key, recursing into dicts."""
        out = base.copy()
        for key, value in over.items():
            if key in out and isinstance(out[key], dict) and isinstance(value, dict):
                out[key] = merge(out[key], value)
            else:
                out[key] = value
        return out

    # Layering: defaults < profile < explicit user values.
    layered = merge(merge(defaults_dict, profile_overrides), explicit)
    return DocpullConfig.model_validate(layered)


def _explicit_fields(model: Any) -> dict[str, Any]:
    """Recursively dump only fields a Pydantic model actually had set.

    Walks ``model_fields_set`` so we never spuriously override a profile
    default with a Pydantic default that the user never asked for.
    """
    if not hasattr(model, "model_fields_set"):
        return {}
    out: dict[str, Any] = {}
    for name in model.model_fields_set:
        value = getattr(model, name)
        if hasattr(value, "model_fields_set"):
            out[name] = _explicit_fields(value)
        else:
            # For container types Pydantic dumps via model_dump too; the
            # simple cases (str, int, list, Path) round-trip fine here.
            out[name] = value
    return out
