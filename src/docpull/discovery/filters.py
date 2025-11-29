"""URL filtering utilities for discovery."""

import fnmatch
import logging
from typing import Optional
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)

# Optional URL normalization library
try:
    from url_normalize import url_normalize

    URL_NORMALIZE_AVAILABLE = True
except ImportError:
    URL_NORMALIZE_AVAILABLE = False


def normalize_url(url: str) -> str:
    """
    Normalize a URL for consistent comparison.

    Removes fragments, normalizes case, and optionally uses url_normalize library.

    Args:
        url: The URL to normalize

    Returns:
        Normalized URL string
    """
    # Use url_normalize library if available
    if URL_NORMALIZE_AVAILABLE:
        try:
            result: str = url_normalize(url)
            return result
        except Exception:
            pass

    # Basic normalization
    parsed = urlparse(url)

    # Remove fragment
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.params,
            parsed.query,
            "",  # Remove fragment
        )
    )

    return normalized


class PatternFilter:
    """
    Filter URLs based on include/exclude patterns.

    Uses glob-style patterns (*, ?, [seq], [!seq]).

    Example:
        filter = PatternFilter(
            include_patterns=["/docs/*", "/api/*"],
            exclude_patterns=["/docs/internal/*"]
        )
        if filter.should_include("https://example.com/docs/guide"):
            process_url(...)
    """

    def __init__(
        self,
        include_patterns: Optional[list[str]] = None,
        exclude_patterns: Optional[list[str]] = None,
    ):
        """
        Initialize the pattern filter.

        Args:
            include_patterns: Patterns that URLs must match (any)
            exclude_patterns: Patterns that URLs must NOT match (any)
        """
        self.include_patterns = include_patterns or []
        self.exclude_patterns = exclude_patterns or []

    def should_include(self, url: str) -> bool:
        """
        Check if URL should be included based on patterns.

        Args:
            url: The URL to check

        Returns:
            True if URL should be included
        """
        parsed = urlparse(url)
        path = parsed.path

        # If include patterns specified, URL must match at least one
        if self.include_patterns and not any(fnmatch.fnmatch(path, p) for p in self.include_patterns):
            return False

        # If exclude patterns specified, URL must NOT match any
        return not (self.exclude_patterns and any(fnmatch.fnmatch(path, p) for p in self.exclude_patterns))


class DomainFilter:
    """
    Filter URLs based on allowed domains.

    Example:
        filter = DomainFilter(
            base_url="https://docs.example.com",
            allow_subdomains=True
        )
        filter.should_include("https://docs.example.com/page")  # True
        filter.should_include("https://example.com/page")  # False (different subdomain)
    """

    def __init__(
        self,
        base_url: str,
        allow_subdomains: bool = False,
        additional_domains: Optional[set[str]] = None,
    ):
        """
        Initialize the domain filter.

        Args:
            base_url: The starting URL (its domain is always allowed)
            allow_subdomains: Whether to allow subdomains of the base domain
            additional_domains: Additional domains to allow
        """
        parsed = urlparse(base_url)
        self.base_domain = parsed.netloc.lower()
        self.allow_subdomains = allow_subdomains
        self.additional_domains = {d.lower() for d in (additional_domains or set())}

    def should_include(self, url: str) -> bool:
        """
        Check if URL's domain is allowed.

        Args:
            url: The URL to check

        Returns:
            True if domain is allowed
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Exact match
        if domain == self.base_domain:
            return True

        # Additional domains
        if domain in self.additional_domains:
            return True

        # Subdomain check
        return bool(self.allow_subdomains and domain.endswith("." + self.base_domain))


class CompositeFilter:
    """
    Combine multiple filters with AND logic.

    All filters must approve a URL for it to be included.
    """

    def __init__(self, filters: list):
        """
        Initialize with a list of filters.

        Args:
            filters: List of objects implementing should_include(url) -> bool
        """
        self.filters = filters

    def should_include(self, url: str) -> bool:
        """
        Check if URL passes all filters.

        Args:
            url: The URL to check

        Returns:
            True if all filters approve
        """
        return all(f.should_include(url) for f in self.filters)


class SeenUrlTracker:
    """
    Track seen URLs to prevent duplicates during discovery.

    Uses URL normalization for consistent comparison.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def add(self, url: str) -> bool:
        """
        Add a URL to the tracker.

        Args:
            url: The URL to add

        Returns:
            True if URL was new, False if already seen
        """
        normalized = normalize_url(url)
        if normalized in self._seen:
            return False
        self._seen.add(normalized)
        return True

    def __contains__(self, url: str) -> bool:
        """Check if URL has been seen."""
        return normalize_url(url) in self._seen

    def __len__(self) -> int:
        """Return number of unique URLs seen."""
        return len(self._seen)

    def clear(self) -> None:
        """Clear all seen URLs."""
        self._seen.clear()
