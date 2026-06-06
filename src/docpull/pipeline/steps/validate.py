"""ValidateStep - URL validation pipeline step."""

import logging
from urllib.parse import urlparse

from ...http.rate_limiter import PerHostRateLimiter
from ...models.events import EventType, FetchEvent, SkipReason
from ...security.robots import RobotsChecker
from ...security.url_validator import UrlValidator
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class ValidateStep:
    """
    Pipeline step that validates URLs before fetching.

    Performs three validation checks:
    1. URL security validation (SSRF prevention, scheme checking)
    2. robots.txt compliance (mandatory for polite crawling)
    3. (Optional) Skip URLs whose Markdown output file already exists.

    Check (3) is a coarse "don't redo work" shortcut. When a CacheManager
    is wired through the pipeline, FetchStep handles freshness via
    ``If-None-Match`` / ``If-Modified-Since`` and a 304 response — so this
    step's existence check is automatically suppressed in that mode.

    Sets ctx.should_skip if:
        - URL fails security validation
        - URL is disallowed by robots.txt
        - (Without caching) Output file already exists.
    """

    name = "validate"

    def __init__(
        self,
        url_validator: UrlValidator,
        robots_checker: RobotsChecker,
        rate_limiter: PerHostRateLimiter | None = None,
        check_existing: bool = True,
        cache_enabled: bool = False,
    ) -> None:
        """
        Initialize the validation step.

        Args:
            url_validator: UrlValidator instance for security checks
            robots_checker: RobotsChecker instance for robots.txt compliance
            rate_limiter: Optional rate limiter to update from Crawl-delay
            check_existing: If True AND ``cache_enabled`` is False, skip
                URLs where the output file already exists. When caching is
                enabled, freshness is owned by FetchStep's conditional GET.
            cache_enabled: Suppresses ``check_existing`` when True. The
                cache manifest is the source of truth for whether a URL
                needs re-fetching, not on-disk file existence.
        """
        self._url_validator = url_validator
        self._robots_checker = robots_checker
        self._rate_limiter = rate_limiter
        self._check_existing = check_existing and not cache_enabled

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        """
        Execute the validation step.

        Args:
            ctx: Page context with URL to validate
            emit: Optional callback to emit events

        Returns:
            PageContext (may have should_skip=True if validation fails)
        """
        url = ctx.url

        # 1. URL security validation
        validation_result = self._url_validator.validate(url)
        if not validation_result.is_valid:
            ctx.should_skip = True
            ctx.skip_reason = f"URL validation failed: {validation_result.rejection_reason}"
            ctx.skip_code = SkipReason.URL_VALIDATION_FAILED
            logger.debug(f"Skipping {url}: {validation_result.rejection_reason}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        message=f"URL validation failed: {validation_result.rejection_reason}",
                        skip_reason=SkipReason.URL_VALIDATION_FAILED,
                    )
                )
            return ctx

        # 2. robots.txt compliance
        if not self._robots_checker.is_allowed(url):
            ctx.should_skip = True
            ctx.skip_reason = "Blocked by robots.txt"
            ctx.skip_code = SkipReason.ROBOTS_DISALLOWED
            logger.debug(f"Skipping {url}: blocked by robots.txt")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        message="Blocked by robots.txt",
                        skip_reason=SkipReason.ROBOTS_DISALLOWED,
                    )
                )
            return ctx

        if self._rate_limiter is not None:
            crawl_delay = self._robots_checker.get_crawl_delay(url)
            hostname = urlparse(url).hostname
            if crawl_delay is not None and hostname:
                self._rate_limiter.update_host_config(hostname, delay=crawl_delay)

        # 3. Check if output file already exists
        if self._check_existing and ctx.output_path.exists():
            ctx.should_skip = True
            ctx.skip_reason = "Output file already exists"
            ctx.skip_code = SkipReason.FILE_EXISTS
            logger.debug(f"Skipping {url}: output file exists at {ctx.output_path}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        output_path=ctx.output_path,
                        message="Output file already exists",
                        skip_reason=SkipReason.FILE_EXISTS,
                    )
                )
            return ctx

        logger.debug(f"Validated {url}")
        return ctx

    def get_crawl_delay(self, url: str) -> float | None:
        """
        Get Crawl-delay for a URL's domain.

        Convenience method to check if robots.txt specifies a crawl delay.

        Args:
            url: URL to check

        Returns:
            Crawl delay in seconds if specified, None otherwise
        """
        return self._robots_checker.get_crawl_delay(url)
