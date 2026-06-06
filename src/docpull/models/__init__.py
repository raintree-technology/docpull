"""Docpull configuration and event models."""

from .config import (
    AuthConfig,
    AuthType,
    ByteSize,
    CacheConfig,
    ContentFilterConfig,
    CrawlConfig,
    DocpullConfig,
    NetworkConfig,
    OutputConfig,
    PerformanceConfig,
    ProfileName,
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

__all__ = [
    # Config
    "AuthConfig",
    "AuthType",
    "ByteSize",
    "CacheConfig",
    "CrawlConfig",
    "ContentFilterConfig",
    "DocpullConfig",
    "NetworkConfig",
    "OutputConfig",
    "PerformanceConfig",
    "ProfileName",
    "RunIdentity",
    "DocumentRecord",
    "RUN_IDENTITY_SCHEMA_VERSION",
    "DOCUMENT_RECORD_SCHEMA_VERSION",
    "FRONTIER_SCHEMA_VERSION",
    "MCP_META_SCHEMA_VERSION",
    "PROGRESS_EVENT_SCHEMA_VERSION",
    # Events
    "EventType",
    "FetchEvent",
    "FetchStats",
    "SkipReason",
    # Profiles
    "PROFILES",
    "apply_profile",
]
