"""FetchStep - HTTP fetching pipeline step."""

import logging
from typing import Optional

from ...http.protocols import HttpClient
from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)

# Allowed content types for HTML documents
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
        "application/atom+xml",
        "application/rss+xml",
    }
)


class FetchStep:
    """
    Pipeline step that fetches page content via HTTP.

    Populates:
        ctx.html: Raw HTML content as bytes
        ctx.status_code: HTTP status code
        ctx.content_type: Content-Type header value
        ctx.bytes_downloaded: Size of downloaded content

    Sets ctx.should_skip if:
        - Content type is not in allowed list
        - Response status indicates client error (4xx)

    Raises exception for:
        - Network errors
        - Server errors (5xx) after retries
        - Content size exceeded

    Example:
        http_client = AsyncHttpClient(rate_limiter)
        fetch_step = FetchStep(http_client)

        ctx = await fetch_step.execute(ctx)
        if not ctx.should_skip:
            html_content = ctx.html
    """

    name = "fetch"

    def __init__(
        self,
        http_client: HttpClient,
        validate_content_type: bool = True,
    ) -> None:
        """
        Initialize the fetch step.

        Args:
            http_client: HTTP client implementing HttpClient protocol
            validate_content_type: If True, skip non-HTML content types
        """
        self._client = http_client
        self._validate_content_type = validate_content_type

    def _is_valid_content_type(self, content_type: str) -> bool:
        """
        Check if content type is allowed.

        Args:
            content_type: Content-Type header value

        Returns:
            True if content type is allowed, False otherwise
        """
        if not content_type:
            return True  # Allow if not specified

        # Extract base content type (ignore charset, etc.)
        base_type = content_type.lower().split(";")[0].strip()
        return base_type in ALLOWED_CONTENT_TYPES

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Execute the fetch step.

        Args:
            ctx: Page context with URL to fetch
            emit: Optional callback to emit events

        Returns:
            PageContext with html, status_code, content_type populated
        """
        url = ctx.url

        # Emit start event
        if emit:
            emit(
                FetchEvent(
                    type=EventType.FETCH_STARTED,
                    url=url,
                    message=f"Fetching {url}",
                )
            )

        try:
            response = await self._client.get(url)

            ctx.status_code = response.status_code
            ctx.content_type = response.content_type
            ctx.bytes_downloaded = len(response.content)

            # Check for client errors (skip, don't fail)
            if 400 <= response.status_code < 500:
                ctx.should_skip = True
                ctx.skip_reason = f"HTTP {response.status_code}"
                logger.debug(f"Skipping {url}: HTTP {response.status_code}")

                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=url,
                            status_code=response.status_code,
                            message=f"Skipped: HTTP {response.status_code}",
                        )
                    )
                return ctx

            # Validate content type
            if self._validate_content_type and not self._is_valid_content_type(response.content_type):
                ctx.should_skip = True
                ctx.skip_reason = f"Invalid content type: {response.content_type}"
                logger.debug(f"Skipping {url}: invalid content type {response.content_type}")

                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=url,
                            content_type=response.content_type,
                            message="Skipped: invalid content type",
                        )
                    )
                return ctx

            # Store content
            ctx.html = response.content

            # Extract caching headers
            ctx.etag = response.headers.get("etag") or response.headers.get("ETag")
            ctx.last_modified = response.headers.get("last-modified") or response.headers.get("Last-Modified")

            logger.debug(f"Fetched {url}: {len(response.content)} bytes")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_COMPLETED,
                        url=url,
                        status_code=response.status_code,
                        bytes_downloaded=len(response.content),
                        content_type=response.content_type,
                        message=f"Fetched {len(response.content)} bytes",
                    )
                )

            return ctx

        except Exception as e:
            logger.error(f"Fetch error for {url}: {e}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=url,
                        error=str(e),
                        message=f"Fetch failed: {e}",
                    )
                )

            # Re-raise to let pipeline handle it
            raise
