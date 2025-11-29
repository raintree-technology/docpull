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
from .protocols import UrlDiscoverer, UrlFilter
from .sitemap import SitemapDiscoverer

__all__ = [
    # Protocols
    "UrlDiscoverer",
    "UrlFilter",
    # Discoverers
    "CompositeDiscoverer",
    "LinkCrawler",
    "SitemapDiscoverer",
    # Filters
    "CompositeFilter",
    "DomainFilter",
    "PatternFilter",
    "SeenUrlTracker",
    "normalize_url",
]
