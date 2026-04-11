"""Link extraction strategies for URL discovery."""

from .enhanced import EnhancedLinkExtractor
from .protocols import LinkExtractor
from .static import StaticLinkExtractor

__all__ = [
    "LinkExtractor",
    "StaticLinkExtractor",
    "EnhancedLinkExtractor",
]
