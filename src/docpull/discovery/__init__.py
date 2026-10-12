"""URL discovery, filtering, and crawling."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    "CompositeDiscoverer": (".composite", "CompositeDiscoverer"),
    "LinkCrawler": (".crawler", "LinkCrawler"),
    **{
        name: (".filters", name)
        for name in ("CompositeFilter", "DomainFilter", "PatternFilter", "SeenUrlTracker", "normalize_url")
    },
    **{
        name: (".link_extractors", name)
        for name in ("EnhancedLinkExtractor", "LinkExtractor", "StaticLinkExtractor")
    },
    **{name: (".protocols", name) for name in ("UrlDiscoverer", "UrlFilter")},
    "SitemapDiscoverer": (".sitemap", "SitemapDiscoverer"),
}

__all__ = [
    "UrlDiscoverer",
    "UrlFilter",
    "LinkExtractor",
    "CompositeDiscoverer",
    "LinkCrawler",
    "SitemapDiscoverer",
    "StaticLinkExtractor",
    "EnhancedLinkExtractor",
    "CompositeFilter",
    "DomainFilter",
    "PatternFilter",
    "SeenUrlTracker",
    "normalize_url",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .composite import CompositeDiscoverer
    from .crawler import LinkCrawler
    from .filters import CompositeFilter, DomainFilter, PatternFilter, SeenUrlTracker, normalize_url
    from .link_extractors import EnhancedLinkExtractor, LinkExtractor, StaticLinkExtractor
    from .protocols import UrlDiscoverer, UrlFilter
    from .sitemap import SitemapDiscoverer


assert set(_LAZY_EXPORTS) == set(__all__)
