"""Link extraction strategies for URL discovery."""

from .enhanced import EnhancedLinkExtractor
from .protocols import LinkExtractor
from .static import StaticLinkExtractor

# BrowserLinkExtractor requires Playwright - import conditionally
try:
    from .browser import BrowserLinkExtractor

    __all__ = [
        "LinkExtractor",
        "StaticLinkExtractor",
        "EnhancedLinkExtractor",
        "BrowserLinkExtractor",
    ]
except ImportError:
    __all__ = [
        "LinkExtractor",
        "StaticLinkExtractor",
        "EnhancedLinkExtractor",
    ]
