"""
docpull - Fetch and convert documentation from any URL to markdown.

Usage:
    from docpull import Fetcher, DocpullConfig, ProfileName

    config = DocpullConfig(
        url="https://docs.example.com",
        profile=ProfileName.RAG,
    )

    async with Fetcher(config) as fetcher:
        async for event in fetcher.run():
            print(event)
"""

__version__ = "2.2.0"

from .cache import CacheManager, StreamingDeduplicator
from .core.fetcher import Fetcher, fetch_blocking
from .models.config import (
    CacheConfig,
    ContentFilterConfig,
    CrawlConfig,
    DocpullConfig,
    IntegrationConfig,
    NetworkConfig,
    OutputConfig,
    PerformanceConfig,
    ProfileName,
)
from .models.events import EventType, FetchEvent, FetchStats

__all__ = [
    "__version__",
    # Core
    "Fetcher",
    "fetch_blocking",
    # Config
    "DocpullConfig",
    "ProfileName",
    "CrawlConfig",
    "ContentFilterConfig",
    "OutputConfig",
    "NetworkConfig",
    "PerformanceConfig",
    "IntegrationConfig",
    "CacheConfig",
    # Events
    "EventType",
    "FetchEvent",
    "FetchStats",
    # Cache
    "CacheManager",
    "StreamingDeduplicator",
]
