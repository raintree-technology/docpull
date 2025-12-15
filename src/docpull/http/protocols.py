"""Protocol definitions for HTTP client abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HttpResponse:
    """
    Immutable HTTP response returned by HttpClient.

    Attributes:
        status_code: HTTP status code (200, 404, etc.)
        content: Raw response content as bytes
        content_type: Content-Type header value
        headers: All response headers
        url: Final URL after any redirects
    """

    status_code: int
    content: bytes
    content_type: str
    headers: dict[str, str]
    url: str


class HttpClient(Protocol):
    """
    Protocol for HTTP clients.

    This abstraction allows for:
    - Mock implementations in tests
    - Different backends (aiohttp, httpx, etc.)
    - Consistent interface across the codebase
    """

    async def get(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """
        Perform an HTTP GET request.

        Args:
            url: The URL to fetch
            timeout: Request timeout in seconds
            headers: Optional additional headers

        Returns:
            HttpResponse with status, content, and headers

        Raises:
            Exception on network errors (after retries exhausted)
        """
        ...

    async def head(
        self,
        url: str,
        *,
        timeout: float = 10.0,
    ) -> HttpResponse:
        """
        Perform an HTTP HEAD request.

        Args:
            url: The URL to check
            timeout: Request timeout in seconds

        Returns:
            HttpResponse (content will be empty bytes)
        """
        ...
