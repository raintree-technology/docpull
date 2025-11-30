"""URL discovery for docpull (sitemap parsing, link crawling)."""

from .composite import CompositeDiscoverer
from .crawler import LinkCrawler
from .filters import (
    CompositeFilter,
    DomainFilter,
    PatternFilter,
    SeenUrlTracker,
    normalize_url,
)
from .link_extractors import EnhancedLinkExtractor, LinkExtractor, StaticLinkExtractor
from .protocols import UrlDiscoverer, UrlFilter
from .sitemap import SitemapDiscoverer

# BrowserLinkExtractor requires Playwright
try:
    from .link_extractors import BrowserLinkExtractor  # noqa: F401

    _browser_extractor_available = True
except ImportError:
    _browser_extractor_available = False
    BrowserLinkExtractor = None  # type: ignore[misc,assignment]

__all__ = [
    # Protocols
    "UrlDiscoverer",
    "UrlFilter",
    "LinkExtractor",
    # Discoverers
    "CompositeDiscoverer",
    "LinkCrawler",
    "SitemapDiscoverer",
    # Link Extractors
    "StaticLinkExtractor",
    "EnhancedLinkExtractor",
    # Filters
    "CompositeFilter",
    "DomainFilter",
    "PatternFilter",
    "SeenUrlTracker",
    "normalize_url",
]

if _browser_extractor_available:
    __all__.append("BrowserLinkExtractor")
