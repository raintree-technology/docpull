"""URL discovery for docpull (sitemap parsing, link crawling)."""

from .composite import CompositeDiscoverer
from .contracts import (
    CandidateSourceRecord,
    DiscoveryError,
    normalize_provider_response,
    read_candidate_records,
    records_from_site_scan,
    records_from_sitemap_file,
    records_from_url_file,
    select_candidate_records,
    write_discovery_pack,
    write_selected_sources,
)
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
    "UrlDiscoverer",
    "UrlFilter",
    "CandidateSourceRecord",
    "LinkExtractor",
    "CompositeDiscoverer",
    "DiscoveryError",
    "LinkCrawler",
    "SitemapDiscoverer",
    "StaticLinkExtractor",
    "EnhancedLinkExtractor",
    "CompositeFilter",
    "DomainFilter",
    "PatternFilter",
    "SeenUrlTracker",
    "normalize_provider_response",
    "normalize_url",
    "read_candidate_records",
    "records_from_site_scan",
    "records_from_sitemap_file",
    "records_from_url_file",
    "select_candidate_records",
    "write_discovery_pack",
    "write_selected_sources",
]
