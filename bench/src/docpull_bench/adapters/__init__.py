"""Black-box system adapters."""

from .base import AdapterError, SystemAdapter
from .command import CommandAdapter
from .docpull import DocPullAdapter
from .hosted import (
    ContextCrawlAdapter,
    ContextMarkdownAdapter,
    ExaContentsAdapter,
    ExaFullContentsAdapter,
    ExaSearchAdapter,
    FirecrawlCrawlAdapter,
    FirecrawlScrapeAdapter,
    FirecrawlSearchAdapter,
    ParallelFullExtractAdapter,
    ParallelSearchAdapter,
    TavilyAdvancedExtractAdapter,
    TavilyCrawlAdapter,
    TavilyExtractAdapter,
    TavilyGuidedAdvancedCrawlAdapter,
    TavilySearchAdapter,
)
from .local_baselines import Crawl4AIAdapter, ReadabilityAdapter, TrafilaturaAdapter
from .replay import ReplayAdapter

__all__ = [
    "AdapterError",
    "CommandAdapter",
    "ContextMarkdownAdapter",
    "ContextCrawlAdapter",
    "Crawl4AIAdapter",
    "DocPullAdapter",
    "ExaContentsAdapter",
    "ExaFullContentsAdapter",
    "ExaSearchAdapter",
    "FirecrawlCrawlAdapter",
    "FirecrawlScrapeAdapter",
    "FirecrawlSearchAdapter",
    "ParallelFullExtractAdapter",
    "ParallelSearchAdapter",
    "ReadabilityAdapter",
    "ReplayAdapter",
    "SystemAdapter",
    "TavilyExtractAdapter",
    "TavilyAdvancedExtractAdapter",
    "TavilyCrawlAdapter",
    "TavilyGuidedAdvancedCrawlAdapter",
    "TavilySearchAdapter",
    "TrafilaturaAdapter",
]
