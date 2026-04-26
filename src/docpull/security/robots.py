"""robots.txt compliance checker."""

from __future__ import annotations

import http.client
import logging
import socket
import ssl
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from .url_validator import UrlValidator


class _CaseInsensitiveHeaders(dict):
    """HTTP headers with case-insensitive key lookup."""

    def __init__(self, pairs: list[tuple[str, str]] | None = None) -> None:
        super().__init__()
        self._lower: dict[str, str] = {}
        if pairs:
            for k, v in pairs:
                self[k] = v

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._lower[key.lower()] = value

    def get(self, key: str, default: str | None = None) -> str | None:  # type: ignore[override]
        return self._lower.get(key.lower(), default)


@dataclass
class _RobotsResponse:
    status_code: int
    headers: _CaseInsensitiveHeaders
    text: str


@dataclass(frozen=True)
class _RobotsCacheEntry:
    parser: RobotFileParser | None
    status: str


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials a validated IP but preserves host-based TLS."""

    def __init__(
        self,
        host: str,
        *,
        ip_address: str,
        port: int,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host=host, port=port, timeout=timeout, context=context)
        self._ip_address = ip_address

    def connect(self) -> None:
        # http.client.HTTPConnection sets source_address / _tunnel_host /
        # _tunnel / _context but typeshed doesn't expose them on the subclass
        # we get back through MRO. Accessing them by name is the supported way
        # to subclass HTTPSConnection (used by urllib3 and aiohttp the same way).
        sock = socket.create_connection(
            (self._ip_address, self.port),
            self.timeout,
            self.source_address,  # type: ignore[attr-defined]
        )
        if self._tunnel_host:  # type: ignore[attr-defined]
            self.sock = sock
            self._tunnel()  # type: ignore[attr-defined]
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # type: ignore[attr-defined]


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
        logger: logging.Logger | None = None,
        url_validator: UrlValidator | None = None,
        allow_insecure_tls: bool = False,
        max_redirects: int = 5,
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
        self._url_validator = url_validator
        self._max_redirects = max_redirects

        if allow_insecure_tls:
            raise ValueError("Insecure TLS is not supported; certificate verification is always enforced")

        # Cache: domain -> parsed robots, missing robots, or error state
        self._cache: dict[str, _RobotsCacheEntry] = {}

    def _validate_url(self, url: str) -> bool:
        """Validate robots URLs before requesting them or following redirects."""
        if self._url_validator is None:
            return True

        result = self._url_validator.validate(url)
        if result.is_valid:
            return True

        self.logger.warning(f"Blocked unsafe robots.txt URL {url}: {result.rejection_reason}")
        return False

    def _get_robots_url(self, url: str) -> str:
        """Get robots.txt URL for a given page URL."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}/robots.txt"

    def _get_domain(self, url: str) -> str:
        """Extract domain from URL."""
        return urlparse(url).netloc

    def _fetch_robots(self, domain: str, robots_url: str) -> _RobotsCacheEntry:
        """
        Fetch and parse robots.txt for a domain.

        Args:
            domain: The domain being checked
            robots_url: Full URL to robots.txt

        Returns:
            Cache entry describing parsed, missing, or error state
        """
        current_url = robots_url

        for redirect_count in range(self._max_redirects + 1):
            if not self._validate_url(current_url):
                return _RobotsCacheEntry(parser=None, status="error")

            try:
                response = self._fetch_url(current_url)
            except (OSError, ValueError, ssl.SSLError, http.client.HTTPException) as e:
                self.logger.warning(f"Failed to fetch robots.txt for {domain}: {e}")
                return _RobotsCacheEntry(parser=None, status="error")

            location = response.headers.get("Location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                if redirect_count >= self._max_redirects:
                    self.logger.warning(f"Too many redirects fetching robots.txt for {domain}")
                    return _RobotsCacheEntry(parser=None, status="error")
                current_url = urljoin(current_url, location)
                continue

            if response.status_code == 200:
                try:
                    parser = RobotFileParser()
                    parser.parse(response.text.splitlines())
                except Exception as e:
                    self.logger.warning(f"Failed to parse robots.txt for {domain}: {e}")
                    return _RobotsCacheEntry(parser=None, status="error")

                self.logger.debug(f"Loaded robots.txt for {domain}")
                return _RobotsCacheEntry(parser=parser, status="present")

            # RFC 9309 §2.3.1.3: treat 4xx as "no robots.txt" (allow).
            # 5xx is treated conservatively as "error" (block) since the site
            # is misbehaving and we don't know what its policy actually is.
            if 400 <= response.status_code < 500:
                self.logger.debug(
                    f"No robots.txt for {domain} (status {response.status_code}, treated as allow)"
                )
                return _RobotsCacheEntry(parser=None, status="missing")

            self.logger.warning(f"Unexpected status {response.status_code} fetching robots.txt for {domain}")
            return _RobotsCacheEntry(parser=None, status="error")

        return _RobotsCacheEntry(parser=None, status="error")

    def _resolve_addresses(self, hostname: str) -> list[str]:
        """Resolve hostnames through the validator so the connect path stays pinned."""
        if self._url_validator is not None:
            return self._url_validator.resolve_allowed_addresses(hostname)

        addresses: set[str] = set()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
            if family in {socket.AF_INET, socket.AF_INET6}:
                addresses.add(str(sockaddr[0]))

        if not addresses:
            raise OSError(f"No addresses found for {hostname}")

        return sorted(addresses)

    def _build_ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context()

    def _decode_body(self, body: bytes, content_type: str) -> str:
        """Decode robots.txt using declared charset when available."""
        encoding = "utf-8"
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                encoding = part.split("=", 1)[1].strip().strip("\"'")
                break

        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            return body.decode("utf-8", errors="replace")

    def _fetch_url(self, url: str) -> _RobotsResponse:
        """Fetch a robots.txt URL through a pinned HTTPS connection."""
        parsed = urlparse(url)
        if parsed.scheme.lower() != "https":
            raise ValueError(f"Unsupported robots.txt scheme: {parsed.scheme}")

        hostname = parsed.hostname
        if hostname is None:
            raise ValueError("Robots URL has no hostname")

        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        headers = {
            "User-Agent": self.user_agent,
            "Host": parsed.netloc,
            "Accept-Encoding": "identity",
        }
        addresses = self._resolve_addresses(hostname)
        ssl_context = self._build_ssl_context()
        port = parsed.port or 443

        last_error: Exception | None = None
        for address in addresses:
            conn = _PinnedHTTPSConnection(
                hostname,
                ip_address=address,
                port=port,
                timeout=self.timeout,
                context=ssl_context,
            )
            try:
                conn.request("GET", path, headers=headers)
                response = conn.getresponse()
                body = response.read()
                return _RobotsResponse(
                    status_code=response.status,
                    headers=_CaseInsensitiveHeaders(list(response.getheaders())),
                    text=self._decode_body(body, response.getheader("Content-Type", "")),
                )
            except (OSError, ssl.SSLError, http.client.HTTPException) as err:
                last_error = err
            finally:
                conn.close()

        if last_error is not None:
            raise last_error

        raise OSError(f"Unable to fetch robots.txt from {url}")

    def _get_entry(self, url: str) -> _RobotsCacheEntry:
        """
        Get or fetch cached robots.txt state for a URL's domain.

        Args:
            url: The URL to check

        Returns:
            Cached robots state for the domain
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
        entry = self._get_entry(url)
        parser = entry.parser

        if entry.status == "missing":
            # No robots.txt - allow by default
            return True

        if parser is None:
            self.logger.warning(f"Blocking {url}: robots.txt state is {entry.status}")
            return False

        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception as e:
            self.logger.warning(f"Error checking robots.txt for {url}: {e}")
            return False

    def get_crawl_delay(self, url: str) -> float | None:
        """
        Get Crawl-delay directive for a URL's domain.

        Args:
            url: A URL from the domain to check

        Returns:
            Crawl delay in seconds if specified, None otherwise
        """
        entry = self._get_entry(url)
        parser = entry.parser

        if parser is None:
            return None

        try:
            delay = parser.crawl_delay(self.user_agent)
            if delay is not None:
                return float(delay)
        except (TypeError, ValueError):
            return None

        return None

    def get_sitemaps(self, url: str) -> list[str]:
        """
        Get Sitemap URLs from robots.txt.

        Args:
            url: A URL from the domain to check

        Returns:
            List of sitemap URLs (may be empty)
        """
        entry = self._get_entry(url)
        parser = entry.parser

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
            "domains_with_robots": sum(1 for entry in self._cache.values() if entry.status == "present"),
            "domains_with_errors": sum(1 for entry in self._cache.values() if entry.status == "error"),
        }
