"""FetchStep - HTTP fetching pipeline step."""

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ...http.protocols import HttpClient
from ...models.events import EventType, FetchEvent, SkipReason
from ...security.download_policy import (
    ALLOWED_DOCUMENT_CONTENT_TYPES,
    is_allowed_document_content_type,
)
from ...security.optout import evaluate_optout, parse_x_robots_tag
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


# Allowed content types for HTML documents and structured feeds. Kept as an
# alias for tests and users that import it directly from this module.
ALLOWED_CONTENT_TYPES = ALLOWED_DOCUMENT_CONTENT_TYPES


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
        allowed_remote_document_types: set[str] | None = None,
        capture_raw: bool = False,
        respect_ai_optout: bool = True,
        respect_noindex: bool = False,
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
            capture_raw: When True, snapshot the raw response body and full
                header map on the context for WARC output. Off by default so
                normal runs don't hold a second copy of every page in memory.
            respect_ai_optout: Honor ``X-Robots-Tag`` AI/TDM opt-out
                directives (``noai``, ``noimageai``) by skipping the page.
            respect_noindex: Also treat ``noindex`` / ``none`` as opt-outs
                (stricter than the default; those directives govern search
                indexing, not reuse).
        """
        self._client = http_client
        self._validate_content_type = validate_content_type
        self._cache_manager = cache_manager
        self._skip_unchanged = skip_unchanged
        self._allowed_remote_document_types = frozenset(allowed_remote_document_types or set())
        self._capture_raw = capture_raw
        self._respect_ai_optout = respect_ai_optout
        self._respect_noindex = respect_noindex

    def _is_valid_content_type(self, content_type: str) -> bool:
        """
        Check if content type is allowed.

        Args:
            content_type: Content-Type header value

        Returns:
            True if content type is allowed, False otherwise
        """
        base_type = content_type.split(";", 1)[0].strip().casefold()
        return (
            is_allowed_document_content_type(content_type) or base_type in self._allowed_remote_document_types
        )

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
        persisted_file = entry.get("file_path")
        persisted_exists = isinstance(persisted_file, str) and Path(persisted_file).exists()
        # Force a fresh body when the cache has us on record but the file
        # is missing. Otherwise a 304 would short-circuit to skip and we'd
        # never write the file the user expects.
        if not output_path_exists and not persisted_exists:
            return {}
        headers: dict[str, str] = {}
        etag = self._sanitize_validator(entry.get("etag"))
        if etag:
            headers["If-None-Match"] = etag
        last_modified = self._sanitize_validator(entry.get("last_modified"))
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    @staticmethod
    def _sanitize_validator(value: str | None) -> str | None:
        """Strip CR/LF/NUL from cached validators before they become request headers.

        ``ETag`` / ``Last-Modified`` are echoed back from the remote (untrusted)
        server and persisted in the cache manifest. Re-sending them verbatim as
        ``If-None-Match`` / ``If-Modified-Since`` would let a malicious server
        smuggle CRLF into an outbound request header on the next incremental run.
        A mangled validator simply misses and triggers a full re-fetch.
        """
        if not value:
            return value
        return value.replace("\r", "").replace("\n", "").replace("\x00", "")

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
            conditional = self._conditional_headers(url, output_path_exists=ctx.output_path.exists())
            response = await self._client.get(
                url,
                headers=conditional or None,
            )

            ctx.status_code = response.status_code
            ctx.http_attempts = 1
            ctx.content_type = response.content_type
            ctx.bytes_downloaded = len(response.content)
            retry_after = _header_get(response.headers, "retry-after")
            if retry_after:
                try:
                    ctx.retry_after_seconds = max(0.0, float(retry_after))
                except ValueError:
                    ctx.retry_after_seconds = None

            # 304 Not Modified: cached copy is still valid. Skip with a
            # distinct reason so the CLI summary can count "unchanged" hits
            # separately from "blocked by robots" or "JS-only SPA."
            if response.status_code == 304:
                ctx.mark_skipped("Not modified (304)", SkipReason.CACHE_UNCHANGED)
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

            if 400 <= response.status_code < 500:
                ctx.mark_skipped(f"HTTP {response.status_code}", SkipReason.HTTP_ERROR)
                logger.debug(f"Skipping {url}: HTTP {response.status_code}")

                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=url,
                            status_code=response.status_code,
                            message=f"Skipped: HTTP {response.status_code}",
                            skip_reason=SkipReason.HTTP_ERROR,
                        )
                    )
                return ctx

            # Validate content type
            if self._validate_content_type and not self._is_valid_content_type(response.content_type):
                ctx.mark_skipped(
                    f"Invalid content type: {response.content_type}",
                    SkipReason.INVALID_CONTENT_TYPE,
                )
                logger.debug(f"Skipping {url}: invalid content type {response.content_type}")

                if emit:
                    emit(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=url,
                            content_type=response.content_type,
                            message="Skipped: invalid content type",
                            skip_reason=SkipReason.INVALID_CONTENT_TYPE,
                        )
                    )
                return ctx

            # Honor machine-readable AI/TDM opt-out signals before keeping
            # any content. `dict(...)`-flattened headers may carry arbitrary
            # key casing, so collect every X-Robots-Tag variant.
            if self._respect_ai_optout or self._respect_noindex:
                directives: set[str] = set()
                for header_name, header_value in response.headers.items():
                    if header_name.lower() == "x-robots-tag":
                        directives |= parse_x_robots_tag(header_value)
                decision = evaluate_optout(
                    directives,
                    respect_noai=self._respect_ai_optout,
                    respect_noindex=self._respect_noindex,
                    source="x-robots-tag",
                )
                if decision.blocked:
                    reason = f"AI/TDM opt-out (x-robots-tag: {', '.join(decision.matched)})"
                    ctx.mark_skipped(reason, SkipReason.AI_OPTOUT)
                    logger.info(f"Skipping {url}: {reason}")
                    if emit:
                        emit(
                            FetchEvent(
                                type=EventType.FETCH_SKIPPED,
                                url=url,
                                status_code=response.status_code,
                                message=f"Skipped: {reason}",
                                skip_reason=SkipReason.AI_OPTOUT,
                            )
                        )
                    return ctx

            # Store content
            ctx.html = response.content

            if self._capture_raw:
                # Snapshot the exact fetched bytes and full header map now:
                # later steps (XBRL cleanup, rendering) may replace ctx.html.
                ctx.raw_content = response.content
                ctx.raw_response_headers = dict(response.headers)

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

            status_value = getattr(e, "status", None)
            if isinstance(status_value, int) and 100 <= status_value <= 599:
                ctx.status_code = status_value
            ctx.http_attempts = int(getattr(self._client, "_max_retries", 0)) + 1
            headers = getattr(e, "headers", None)
            retry_after = headers.get("Retry-After") if headers is not None else None
            if isinstance(retry_after, str):
                try:
                    ctx.retry_after_seconds = max(0.0, float(retry_after))
                except ValueError:
                    ctx.retry_after_seconds = None
            retryable = ctx.status_code in {408, 425, 429, 500, 502, 503, 504}

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=url,
                        error=str(e),
                        message=f"Fetch failed: {e}",
                        status_code=ctx.status_code,
                        attempts=ctx.http_attempts,
                        retry_after_seconds=ctx.retry_after_seconds,
                        retryable=retryable,
                        failure_code=f"http_{ctx.status_code}" if ctx.status_code else "fetch_error",
                        failure_stage="fetch",
                    )
                )

            # Re-raise to let pipeline handle it
            raise
