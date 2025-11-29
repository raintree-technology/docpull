"""ValidateStep - URL validation pipeline step."""

import logging
from typing import Optional

from ...models.events import EventType, FetchEvent
from ...security.robots import RobotsChecker
from ...security.url_validator import UrlValidator
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class ValidateStep:
    """
    Pipeline step that validates URLs before fetching.

    Performs two validation checks:
    1. URL security validation (SSRF prevention, scheme checking)
    2. robots.txt compliance (mandatory for polite crawling)

    Sets ctx.should_skip if:
        - URL fails security validation
        - URL is disallowed by robots.txt

    Also checks for Crawl-delay directive and can update rate limiter.

    Example:
        url_validator = UrlValidator(allowed_schemes={"https"})
        robots_checker = RobotsChecker(user_agent="docpull/2.0")
        validate_step = ValidateStep(url_validator, robots_checker)

        ctx = await validate_step.execute(ctx)
        if ctx.should_skip:
            print(f"Skipped: {ctx.skip_reason}")
    """

    name = "validate"

    def __init__(
        self,
        url_validator: UrlValidator,
        robots_checker: RobotsChecker,
        check_existing: bool = True,
    ) -> None:
        """
        Initialize the validation step.

        Args:
            url_validator: UrlValidator instance for security checks
            robots_checker: RobotsChecker instance for robots.txt compliance
            check_existing: If True, skip URLs where output file exists
        """
        self._url_validator = url_validator
        self._robots_checker = robots_checker
        self._check_existing = check_existing

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
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
            logger.debug(f"Skipping {url}: {validation_result.rejection_reason}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        message=f"URL validation failed: {validation_result.rejection_reason}",
                    )
                )
            return ctx

        # 2. robots.txt compliance
        if not self._robots_checker.is_allowed(url):
            ctx.should_skip = True
            ctx.skip_reason = "Blocked by robots.txt"
            logger.debug(f"Skipping {url}: blocked by robots.txt")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        message="Blocked by robots.txt",
                    )
                )
            return ctx

        # 3. Check if output file already exists
        if self._check_existing and ctx.output_path.exists():
            ctx.should_skip = True
            ctx.skip_reason = "Output file already exists"
            logger.debug(f"Skipping {url}: output file exists at {ctx.output_path}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        output_path=ctx.output_path,
                        message="Output file already exists",
                    )
                )
            return ctx

        logger.debug(f"Validated {url}")
        return ctx

    def get_crawl_delay(self, url: str) -> Optional[float]:
        """
        Get Crawl-delay for a URL's domain.

        Convenience method to check if robots.txt specifies a crawl delay.

        Args:
            url: URL to check

        Returns:
            Crawl delay in seconds if specified, None otherwise
        """
        return self._robots_checker.get_crawl_delay(url)
