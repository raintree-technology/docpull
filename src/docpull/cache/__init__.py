"""Caching, durable crawl frontier, and streaming deduplication."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    **{name: (".frontier", name) for name in ("FrontierEntry", "FrontierState", "FrontierStore")},
    **{
        name: (".manager", name)
        for name in ("DEFAULT_TTL_DAYS", "CacheManager", "CacheState", "ManifestEntry")
    },
    "StreamingDeduplicator": (".streaming_dedup", "StreamingDeduplicator"),
}

__all__ = [
    "CacheManager",
    "CacheState",
    "ManifestEntry",
    "FrontierEntry",
    "FrontierState",
    "FrontierStore",
    "StreamingDeduplicator",
    "DEFAULT_TTL_DAYS",
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
    from .frontier import FrontierEntry, FrontierState, FrontierStore
    from .manager import DEFAULT_TTL_DAYS, CacheManager, CacheState, ManifestEntry
    from .streaming_dedup import StreamingDeduplicator


assert set(_LAZY_EXPORTS) == set(__all__)
