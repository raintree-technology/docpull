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

    def validate_hostname(self, hostname: str) -> UrlValidationResult:
        """Validate a hostname against domain and IP safety rules."""
        normalized = hostname.lower()

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

        The returned addresses have already been checked against the same
        private-network policy enforced by validate().
        """
        normalized = hostname.lower()
        validation = self.validate_hostname(normalized)
        if not validation.is_valid:
            reason = validation.rejection_reason or "Hostname failed validation"
            raise ValueError(reason)

        try:
            ipaddress.ip_address(normalized)
            return [normalized]
        except ValueError:
            pass

        return self._resolver(normalized)

    def _resolve_hostname(self, hostname: str) -> list[str]:
        """Resolve hostname to a deduplicated list of IP addresses."""
        addresses: set[str] = set()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM):
            if family in {socket.AF_INET, socket.AF_INET6}:
                addresses.add(sockaddr[0])

        if not addresses:
            raise socket.gaierror(f"No addresses found for {hostname}")

        return sorted(addresses)

    def _blocked_ip_reason(self, ip: ipaddress._BaseAddress) -> str | None:
        """Return a rejection reason for disallowed IP addresses."""
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
            return None
        except ValueError:
            pass

        try:
            addresses = self._resolver(hostname)
        except OSError as err:
            return UrlValidationResult.invalid(f"Hostname '{hostname}' could not be resolved: {err}")

        for address in addresses:
            try:
                ip = ipaddress.ip_address(address)
            except ValueError:
                return UrlValidationResult.invalid(
                    f"Hostname '{hostname}' resolved to invalid address '{address}'"
                )

            blocked_reason = self._blocked_ip_reason(ip)
            if blocked_reason is not None:
                return UrlValidationResult.invalid(
                    "Hostname "
                    f"'{hostname}' resolves to blocked address '{address}' "
                    f"({blocked_reason.lower()})"
                )

        return None

    def is_valid(self, url: str) -> bool:
        """
        Quick check if URL is valid.

        Args:
            url: The URL to check

        Returns:
            True if valid, False otherwise
        """
        return self.validate(url).is_valid

    def get_rejection_reason(self, url: str) -> str | None:
        """
        Get rejection reason for a URL.

        Args:
            url: The URL to check

        Returns:
            Rejection reason string if invalid, None if valid
        """
        return self.validate(url).rejection_reason
