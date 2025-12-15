"""URL validation for security and policy compliance."""

from __future__ import annotations

import ipaddress
import logging
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
        hostname = parsed.netloc.split(":")[0].lower()

        # Check allowed domains
        if self.allowed_domains is not None and hostname not in self.allowed_domains:
            return UrlValidationResult.invalid(f"Domain '{hostname}' not in allowed list")

        # Check for localhost
        if hostname in self.LOCALHOST_NAMES:
            return UrlValidationResult.invalid("Localhost URLs not allowed")

        # Check for internal domain suffixes
        for suffix in self.INTERNAL_SUFFIXES:
            if hostname.endswith(suffix):
                return UrlValidationResult.invalid(f"Internal domain suffix '{suffix}' not allowed")

        # Check for private/internal IPs
        if self.block_private_ips:
            ip_result = self._check_ip_address(hostname)
            if ip_result is not None:
                return ip_result

        return UrlValidationResult.valid()

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

            if ip.is_private:
                return UrlValidationResult.invalid(f"Private IP address '{hostname}' not allowed")
            if ip.is_loopback:
                return UrlValidationResult.invalid(f"Loopback IP address '{hostname}' not allowed")
            if ip.is_link_local:
                return UrlValidationResult.invalid(f"Link-local IP address '{hostname}' not allowed")
            if ip.is_reserved:
                return UrlValidationResult.invalid(f"Reserved IP address '{hostname}' not allowed")

            # Check for IPv6 special addresses
            if isinstance(ip, ipaddress.IPv6Address) and ip.is_site_local:
                return UrlValidationResult.invalid(f"Site-local IPv6 address '{hostname}' not allowed")

            return None  # IP is allowed

        except ValueError:
            # Not an IP address (it's a domain name) - this is fine
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
