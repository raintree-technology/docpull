"""FetchStep - HTTP fetching pipeline step."""

import logging
from typing import TYPE_CHECKING

from ...http.protocols import HttpClient
from ...models.events import EventType, FetchEvent, SkipReason
from ..base import EventEmitter, PageContext

if TYPE_CHECKING:
    from ...cache import CacheManager

logger = logging.getLogger(__name__)


def _header_get(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup against a plain dict."""
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None

# Allowed content types for HTML documents and structured feeds.
# JSON and plain text are allowed so downstream special-case extractors can
# handle OpenAPI specs, raw Markdown, and similar sources.
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/xml",
        "application/xml",
        "application/atom+xml",
        "application/rss+xml",
        "application/json",
        "application/ld+json",
        "text/plain",
        "text/markdown",
        "text/x-markdown",
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
        cache_manager: "CacheManager | None" = None,
        skip_unchanged: bool = True,
    ) -> None:
        """
        Initialize the fetch step.

        Args:
            http_client: HTTP client implementing HttpClient protocol
            validate_content_type: If True, skip non-HTML content types
            cache_manager: Optional cache manager. When provided AND
                ``skip_unchanged`` is True, the step sets ``If-None-Match``
                / ``If-Modified-Since`` request headers from the cache
                manifest and treats a ``304 Not Modified`` response as a
                successful skip.
            skip_unchanged: When False, the conditional headers are not
                attached even if the cache has a manifest entry. Lets users
                force a re-fetch via ``--no-skip-unchanged``.
        """
        self._client = http_client
        self._validate_content_type = validate_content_type
        self._cache_manager = cache_manager
        self._skip_unchanged = skip_unchanged

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

    def _conditional_headers(self, url: str, output_path_exists: bool) -> dict[str, str]:
        """Build ``If-None-Match`` / ``If-Modified-Since`` from the cache.

        Returns an empty dict when the cache has nothing to validate, when
        ``skip_unchanged`` is disabled, or when the on-disk output is missing
        for a previously-fetched URL — in that last case we DO NOT send
        conditional headers, because a 304 would skip the page and leave us
        with no Markdown on disk. A fresh full fetch is the right answer.
        """
        if self._cache_manager is None or not self._skip_unchanged:
            return {}
        entry = self._cache_manager.manifest.get(url)
        if not entry:
            return {}
        # Force a fresh body when the cache has us on record but the file
        # is missing. Otherwise a 304 would short-circuit to skip and we'd
        # never write the file the user expects.
        if not output_path_exists:
            return {}
        headers: dict[str, str] = {}
        etag = entry.get("etag")
        if etag:
            headers["If-None-Match"] = etag
        last_modified = entry.get("last_modified")
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
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
            conditional = self._conditional_headers(
                url, output_path_exists=ctx.output_path.exists()
            )
            response = await self._client.get(
                url,
                headers=conditional or None,
            )

            ctx.status_code = response.status_code
            ctx.content_type = response.content_type
            ctx.bytes_downloaded = len(response.content)

            # 304 Not Modified: cached copy is still valid. Skip with a
            # distinct reason so the CLI summary can count "unchanged" hits
            # separately from "blocked by robots" or "JS-only SPA."
            if response.status_code == 304:
                ctx.should_skip = True
                ctx.skip_reason = "Not modified (304)"
                logger.debug(f"304 Not Modified: {url}")
                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=url,
                            status_code=304,
                            message="Not modified (304)",
                            skip_reason=SkipReason.CACHE_UNCHANGED,
                        )
                    )
                return ctx

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

            # Extract caching headers. aiohttp's response.headers is a
            # case-insensitive multidict, but `dict(...)` flattens it to a
            # plain dict whose key casing depends on the aiohttp version.
            # Look up by canonical lowercase to stay robust.
            ctx.etag = _header_get(response.headers, "etag")
            ctx.last_modified = _header_get(response.headers, "last-modified")

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
