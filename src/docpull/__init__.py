"""
docpull - Fetch and convert static/server-rendered documentation to markdown.

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

__version__ = "4.3.0"

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
from .pipeline.steps import SqliteSearchResult, search_sqlite_documents
from .scraper import (
    Scraper,
    ScrapeResult,
    ScrapeRunResult,
    scrape_one,
    scrape_one_blocking,
    scrape_site,
)

__all__ = [
    "__version__",
    "Fetcher",
    "fetch_blocking",
    "fetch_one",
    "ScrapeResult",
    "ScrapeRunResult",
    "Scraper",
    "scrape_one",
    "scrape_one_blocking",
    "scrape_site",
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
    "SqliteSearchResult",
    "search_sqlite_documents",
    "CacheManager",
    "StreamingDeduplicator",
    "Chunk",
    "TokenCounter",
    "chunk_markdown",
]
