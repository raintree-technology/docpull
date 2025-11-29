"""robots.txt compliance checker."""

import logging
from typing import Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests


class RobotsChecker:
    """
    Checks robots.txt compliance for URLs.

    Implements mandatory robots.txt checking for polite crawling.
    Caches parsed robots.txt files to avoid repeated fetches.

    Example:
        checker = RobotsChecker(user_agent="docpull/2.0")

        if checker.is_allowed("https://example.com/page"):
            fetch_page(...)

        # Get crawl delay if specified
        delay = checker.get_crawl_delay("example.com")
        if delay:
            time.sleep(delay)
    """

    def __init__(
        self,
        user_agent: str = "docpull",
        timeout: float = 10.0,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize the robots.txt checker.

        Args:
            user_agent: User agent string for robots.txt matching
            timeout: Timeout for fetching robots.txt files
            logger: Optional logger for debug messages
        """
        self.user_agent = user_agent
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)

        # Cache: domain -> RobotFileParser (or None if fetch failed)
        self._cache: dict[str, Optional[RobotFileParser]] = {}

    def _get_robots_url(self, url: str) -> str:
        """Get robots.txt URL for a given page URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        return urlparse(url).netloc

    def _fetch_robots(self, domain: str, robots_url: str) -> Optional[RobotFileParser]:
        """
        Fetch and parse robots.txt for a domain.

        Args:
            domain: The domain being checked
            robots_url: Full URL to robots.txt

        Returns:
            RobotFileParser if successful, None if fetch failed
        """
        try:
            response = requests.get(
                robots_url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
            )

            if response.status_code == 200:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                self.logger.debug(f"Loaded robots.txt for {domain}")
                return parser
            elif response.status_code in (404, 403):
                # No robots.txt or forbidden - allow all
                self.logger.debug(f"No robots.txt for {domain} (status {response.status_code})")
                return None
            else:
                self.logger.warning(
                    f"Unexpected status {response.status_code} fetching robots.txt for {domain}"
                )
                return None

        except requests.RequestException as e:
            self.logger.warning(f"Failed to fetch robots.txt for {domain}: {e}")
            return None

    def _get_parser(self, url: str) -> Optional[RobotFileParser]:
        """
        Get or fetch RobotFileParser for a URL's domain.

        Args:
            url: The URL to check

        Returns:
            RobotFileParser if available, None otherwise
        """
        domain = self._get_domain(url)

        if domain not in self._cache:
            robots_url = self._get_robots_url(url)
            self._cache[domain] = self._fetch_robots(domain, robots_url)

        return self._cache[domain]

    def is_allowed(self, url: str) -> bool:
        """
        Check if URL is allowed by robots.txt.

        Args:
            url: The URL to check

        Returns:
            True if allowed (or no robots.txt), False if disallowed
        """
        parser = self._get_parser(url)

        if parser is None:
            # No robots.txt - allow by default
            return True

        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception as e:
            self.logger.warning(f"Error checking robots.txt for {url}: {e}")
            # On error, allow by default (be permissive)
            return True

    def get_crawl_delay(self, url: str) -> Optional[float]:
        """
        Get Crawl-delay directive for a URL's domain.

        Args:
            url: A URL from the domain to check

        Returns:
            Crawl delay in seconds if specified, None otherwise
        """
        parser = self._get_parser(url)

        if parser is None:
            return None

        try:
            delay = parser.crawl_delay(self.user_agent)
            if delay is not None:
                return float(delay)
        except Exception:
            pass

        return None

    def get_sitemaps(self, url: str) -> list[str]:
        """
        Get Sitemap URLs from robots.txt.

        Args:
            url: A URL from the domain to check

        Returns:
            List of sitemap URLs (may be empty)
        """
        parser = self._get_parser(url)

        if parser is None:
            return []

        try:
            sitemaps = parser.site_maps()
            return list(sitemaps) if sitemaps else []
        except Exception:
            return []

    def clear_cache(self) -> None:
        """Clear the robots.txt cache."""
        self._cache.clear()

    def get_stats(self) -> dict:
        """Get cache statistics."""
        return {
            "domains_cached": len(self._cache),
            "domains_with_robots": sum(1 for p in self._cache.values() if p is not None),
        }
