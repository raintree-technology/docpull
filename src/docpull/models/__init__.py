"""Docpull configuration, document, event, profile, and run models.

Public model exports are resolved lazily so importing a lightweight submodule
such as :mod:`docpull.models.run` does not build every Pydantic configuration
schema in the package.
"""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    **{
        name: (".config", name)
        for name in (
            "AuthConfig",
            "AuthType",
            "BudgetConfig",
            "ByteSize",
            "CacheConfig",
            "ContentFilterConfig",
            "CrawlConfig",
            "DocpullConfig",
            "NetworkConfig",
            "OutputConfig",
            "PerformanceConfig",
            "ProfileName",
            "RenderActionPolicy",
            "RenderConfig",
            "RenderViewport",
        )
    },
    "DocumentRecord": (".document", "DocumentRecord"),
    **{name: (".events", name) for name in ("EventType", "FetchEvent", "FetchStats", "SkipReason")},
    **{name: (".profiles", name) for name in ("PROFILES", "apply_profile")},
    **{
        name: (".run", name)
        for name in (
            "DOCUMENT_RECORD_SCHEMA_VERSION",
            "FRONTIER_SCHEMA_VERSION",
            "MCP_META_SCHEMA_VERSION",
            "PROGRESS_EVENT_SCHEMA_VERSION",
            "RUN_IDENTITY_SCHEMA_VERSION",
            "RunIdentity",
        )
    },
}

__all__ = [
    "AuthConfig",
    "AuthType",
    "BudgetConfig",
    "ByteSize",
    "CacheConfig",
    "CrawlConfig",
    "ContentFilterConfig",
    "DocpullConfig",
    "NetworkConfig",
    "OutputConfig",
    "PerformanceConfig",
    "ProfileName",
    "RenderActionPolicy",
    "RenderConfig",
    "RenderViewport",
    "RunIdentity",
    "DocumentRecord",
    "RUN_IDENTITY_SCHEMA_VERSION",
    "DOCUMENT_RECORD_SCHEMA_VERSION",
    "FRONTIER_SCHEMA_VERSION",
    "MCP_META_SCHEMA_VERSION",
    "PROGRESS_EVENT_SCHEMA_VERSION",
    "EventType",
    "FetchEvent",
    "FetchStats",
    "SkipReason",
    "PROFILES",
    "apply_profile",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .config import (
        AuthConfig,
        AuthType,
        BudgetConfig,
        ByteSize,
        CacheConfig,
        ContentFilterConfig,
        CrawlConfig,
        DocpullConfig,
        NetworkConfig,
        OutputConfig,
        PerformanceConfig,
        ProfileName,
        RenderActionPolicy,
        RenderConfig,
        RenderViewport,
    )
    from .document import DocumentRecord
    from .events import EventType, FetchEvent, FetchStats, SkipReason
    from .profiles import PROFILES, apply_profile
    from .run import (
        DOCUMENT_RECORD_SCHEMA_VERSION,
        FRONTIER_SCHEMA_VERSION,
        MCP_META_SCHEMA_VERSION,
        PROGRESS_EVENT_SCHEMA_VERSION,
        RUN_IDENTITY_SCHEMA_VERSION,
        RunIdentity,
    )


assert set(_LAZY_EXPORTS) == set(__all__)
