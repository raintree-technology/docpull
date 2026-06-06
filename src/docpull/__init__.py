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

__version__ = "4.0.1"

from .cache import CacheManager, StreamingDeduplicator
from .conversion.chunking import Chunk, TokenCounter, chunk_markdown
from .core.fetcher import Fetcher, fetch_blocking, fetch_one
from .models.config import (
    CacheConfig,
    ContentFilterConfig,
    CrawlConfig,
    DocpullConfig,
    NetworkConfig,
    OutputConfig,
    PerformanceConfig,
    ProfileName,
)
from .models.events import EventType, FetchEvent, FetchStats
from .pipeline.base import PageContext

__all__ = [
    "__version__",
    "Fetcher",
    "fetch_blocking",
    "fetch_one",
    "PageContext",
    "DocpullConfig",
    "ProfileName",
    "CrawlConfig",
    "ContentFilterConfig",
    "OutputConfig",
    "NetworkConfig",
    "PerformanceConfig",
    "CacheConfig",
    "EventType",
    "FetchEvent",
    "FetchStats",
    "CacheManager",
    "StreamingDeduplicator",
    "Chunk",
    "TokenCounter",
    "chunk_markdown",
]
