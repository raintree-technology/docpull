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
    ParallelFullExtractAdapter,
    ParallelSearchAdapter,
    TavilyAdvancedExtractAdapter,
    TavilyCrawlAdapter,
    TavilyExtractAdapter,
    TavilyGuidedAdvancedCrawlAdapter,
    TavilySearchAdapter,
)
from .replay import ReplayAdapter

__all__ = [
    "AdapterError",
    "CommandAdapter",
    "ContextMarkdownAdapter",
    "ContextCrawlAdapter",
    "DocPullAdapter",
    "ExaContentsAdapter",
    "ExaFullContentsAdapter",
    "ExaSearchAdapter",
    "ParallelFullExtractAdapter",
    "ParallelSearchAdapter",
    "ReplayAdapter",
    "SystemAdapter",
    "TavilyExtractAdapter",
    "TavilyAdvancedExtractAdapter",
    "TavilyCrawlAdapter",
    "TavilyGuidedAdvancedCrawlAdapter",
    "TavilySearchAdapter",
]
