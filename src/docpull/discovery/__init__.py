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
