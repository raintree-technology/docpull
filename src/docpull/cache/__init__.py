"""Caching and deduplication for docpull."""

from .frontier import FrontierEntry, FrontierState, FrontierStore
from .manager import DEFAULT_TTL_DAYS, CacheManager, CacheState, ManifestEntry
from .streaming_dedup import StreamingDeduplicator

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
