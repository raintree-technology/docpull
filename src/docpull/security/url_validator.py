"""URL validation for security and policy compliance."""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class UrlValidationResult:
    """Result of URL validation."""

    is_valid: bool
    rejection_reason: str | None = None

    @staticmethod
    def valid() -> UrlValidationResult:
        """Create a valid result."""
        return UrlValidationResult(is_valid=True)

    @staticmethod
    def invalid(reason: str) -> UrlValidationResult:
        """Create an invalid result with reason."""
        return UrlValidationResult(is_valid=False, rejection_reason=reason)


class UrlValidator:
    """
    Validates URLs for security and policy compliance.

    Prevents SSRF (Server-Side Request Forgery) attacks by blocking:
    - Non-HTTPS URLs (by default)
    - Private/internal IP addresses
    - Localhost and internal domain suffixes
    - URLs not in the allowed domains list (if configured)

    Example:
        validator = UrlValidator(allowed_schemes={"https"})
        result = validator.validate("https://example.com/page")
        if not result.is_valid:
            print(f"Rejected: {result.rejection_reason}")
    """

    # Default security settings
    DEFAULT_ALLOWED_SCHEMES = {"https"}
    INTERNAL_SUFFIXES = {".internal", ".local", ".localhost", ".localdomain"}
    LOCALHOST_NAMES = {"localhost", "localhost.localdomain"}
    # RFC 6598 carrier-grade NAT / shared address space. Python's ``ipaddress``
    # does not flag 100.64.0.0/10 as private, but it is non-globally-routable and
    # is used as internal address space by many cloud and Kubernetes networks, so
    # we block it the same way the TypeScript MCP gate does (``isCGNAT()``).
    _CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")

    def __init__(
        self,
        allowed_schemes: set[str] | None = None,
        allowed_domains: set[str] | None = None,
        block_private_ips: bool = True,
        logger: logging.Logger | None = None,
        resolver: Callable[[str], list[str]] | None = None,
    ):
        """
        Initialize the URL validator.

        Args:
            allowed_schemes: Set of allowed URL schemes (default: {"https"})
            allowed_domains: If set, only these domains are allowed
            block_private_ips: Whether to block private/internal IPs (default: True)
            logger: Optional logger for validation messages
        """
        self.allowed_schemes = allowed_schemes or self.DEFAULT_ALLOWED_SCHEMES
        self.allowed_domains = allowed_domains
        self.block_private_ips = block_private_ips
        self.logger = logger or logging.getLogger(__name__)
        self._resolver = resolver or self._resolve_hostname

    def validate(self, url: str) -> UrlValidationResult:
        """
        Validate a URL for security and policy compliance.

        Args:
            url: The URL to validate

        Returns:
            UrlValidationResult with is_valid and optional rejection_reason
        """
        try:
            parsed = urlparse(url)
        except Exception:
            return UrlValidationResult.invalid("Invalid URL format")

        # Check scheme
        if parsed.scheme not in self.allowed_schemes:
            return UrlValidationResult.invalid(
                f"Scheme '{parsed.scheme}' not allowed (allowed: {self.allowed_schemes})"
            )

        # Check for missing domain
        if not parsed.netloc:
            return UrlValidationResult.invalid("URL has no domain")

        # Extract hostname (remove port if present)
        hostname = parsed.hostname
        if hostname is None:
            return UrlValidationResult.invalid("URL has no hostname")

        return self.validate_hostname(hostname)

    @staticmethod
    def _normalize_hostname(hostname: str) -> str:
        """Lowercase and strip the trailing DNS root dot before policy checks.

        ``urlparse``/WHATWG preserve the trailing dot (``localhost.`` stays
        ``localhost.``), which would otherwise slip past the localhost and
        internal-suffix comparisons and reach an internal host.
        """
        return hostname.lower().rstrip(".")

    def _check_static_policy(self, normalized: str) -> UrlValidationResult | None:
        """Run the DNS-free policy checks (allow-list, localhost, suffixes)."""
        # Check allowed domains
        if self.allowed_domains is not None and normalized not in self.allowed_domains:
            return UrlValidationResult.invalid(f"Domain '{normalized}' not in allowed list")

        # Check for localhost
        if normalized in self.LOCALHOST_NAMES:
            return UrlValidationResult.invalid("Localhost URLs not allowed")

        # Check for internal domain suffixes
        for suffix in self.INTERNAL_SUFFIXES:
            if normalized.endswith(suffix):
                return UrlValidationResult.invalid(f"Internal domain suffix '{suffix}' not allowed")

        return None

    def validate_hostname(self, hostname: str) -> UrlValidationResult:
        """Validate a hostname against domain and IP safety rules."""
        normalized = self._normalize_hostname(hostname)

        static_result = self._check_static_policy(normalized)
        if static_result is not None:
            return static_result

        # Check for private/internal IPs
        if self.block_private_ips:
            ip_result = self._check_ip_address(normalized)
            if ip_result is not None:
                return ip_result
            resolved_ip_result = self._check_resolved_addresses(normalized)
            if resolved_ip_result is not None:
                return resolved_ip_result

        return UrlValidationResult.valid()

    def resolve_allowed_addresses(self, hostname: str) -> list[str]:
        """
        Resolve a hostname to transport-safe IP addresses.

        Resolution happens exactly once: the addresses screened against the
        private-network policy are the *same* addresses returned to (and dialed
        by) the caller. Resolving a second time would reopen a DNS-rebinding
        TOCTOU in which a hostile resolver returns a public IP to the policy
        check and an internal IP to the connect path.
        """
        normalized = self._normalize_hostname(hostname)

        static_result = self._check_static_policy(normalized)
        if static_result is not None:
            raise ValueError(static_result.rejection_reason or "Hostname failed validation")

        is_literal_ip = True
        try:
            ipaddress.ip_address(normalized)
        except ValueError:
            is_literal_ip = False

        if is_literal_ip:
            if self.block_private_ips:
                ip_result = self._check_ip_address(normalized)
                if ip_result is not None:
                    raise ValueError(ip_result.rejection_reason or "Blocked IP address")
            return [normalized]

        if not self.block_private_ips:
            addresses = self._resolver(normalized)
            if not addresses:
                raise ValueError(f"No addresses found for {normalized}")
            return addresses

        addresses, rejection = self._resolve_and_screen(normalized)
        if rejection is not None:
            raise ValueError(rejection.rejection_reason or "Hostname failed validation")
        return addresses

    def _resolve_hostname(self, hostname: str) -> list[str]:
        """Resolve hostname to a deduplicated list of IP addresses."""
        addresses: set[str] = set()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
            if family in {socket.AF_INET, socket.AF_INET6}:
                addresses.add(str(sockaddr[0]))

        if not addresses:
            raise socket.gaierror(f"No addresses found for {hostname}")

        return sorted(addresses)

    def _blocked_ip_reason(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
        """Return a rejection reason for disallowed IP addresses."""
        # Unwrap IPv4-mapped IPv6 (``::ffff:a.b.c.d``) so the full IPv4 policy —
        # including CGNAT below — applies. Python's ``is_private`` does not catch
        # the mapped form of every blocked range (e.g. ``::ffff:100.64.0.1``).
        if isinstance(ip, ipaddress.IPv6Address):
            mapped = ip.ipv4_mapped
            if mapped is not None:
                return self._blocked_ip_reason(mapped)

        if ip.is_private:
            return "Private IP address"
        if ip.is_loopback:
            return "Loopback IP address"
        if ip.is_link_local:
            return "Link-local IP address"
        if ip.is_reserved:
            return "Reserved IP address"
        if ip.is_multicast:
            return "Multicast IP address"
        if ip.is_unspecified:
            return "Unspecified IP address"
        if isinstance(ip, ipaddress.IPv4Address) and ip in self._CGNAT_NETWORK:
            return "Carrier-grade NAT IP address"
        if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
            return "Site-local IPv6 address"
        return None

    def _check_ip_address(self, hostname: str) -> UrlValidationResult | None:
        """
        Check if hostname is a private/internal IP address.

        Args:
            hostname: The hostname to check

        Returns:
            UrlValidationResult if IP is blocked, None if hostname is not an IP
        """
        try:
            ip = ipaddress.ip_address(hostname)
            blocked_reason = self._blocked_ip_reason(ip)
            if blocked_reason is not None:
                return UrlValidationResult.invalid(f"{blocked_reason} '{hostname}' not allowed")
            return None  # IP is allowed

        except ValueError:
            # Not an IP address (it's a domain name) - this is fine
            return None

    def _check_resolved_addresses(self, hostname: str) -> UrlValidationResult | None:
        """
        Resolve a hostname and reject it if any answer points to a blocked IP.

        This closes the gap where attacker-controlled DNS maps a public-looking
        hostname to a private or loopback address.
        """
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return None  # Literal IPs are screened by _check_ip_address.

        _addresses, rejection = self._resolve_and_screen(hostname)
        return rejection

    def _resolve_and_screen(self, hostname: str) -> tuple[list[str], UrlValidationResult | None]:
        """Resolve ``hostname`` once and screen every answer against the policy.

        Returns ``(addresses, None)`` when all resolved addresses are allowed,
        or ``([], rejection)`` on the first failed/blocked/invalid answer. A
        hostname that resolves to no addresses is rejected (fail-closed) rather
        than treated as safe.
        """
        try:
            addresses = self._resolver(hostname)
        except OSError as err:
            return [], UrlValidationResult.invalid(f"Hostname '{hostname}' could not be resolved: {err}")

        if not addresses:
            return [], UrlValidationResult.invalid(f"Hostname '{hostname}' did not resolve to any address")

        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                return [], UrlValidationResult.invalid(
                    f"Hostname '{hostname}' resolved to invalid address '{address}'"
                )

            blocked_reason = self._blocked_ip_reason(ip)
            if blocked_reason is not None:
                return [], UrlValidationResult.invalid(
                    "Hostname "
                    f"'{hostname}' resolves to blocked address '{address}' "
                    f"({blocked_reason.lower()})"
                )

        return addresses, None

    def is_valid(self, url: str) -> bool:
        """
        Quick check if URL is valid.

        Args:
            url: The URL to check

        Returns:
            True if valid, False otherwise
        """
        return self.validate(url).is_valid
