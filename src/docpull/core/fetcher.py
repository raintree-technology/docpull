"""Main Fetcher class with streaming event API."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import TracebackType
from typing import Callable
from urllib.parse import urlparse

from ..cache import CacheManager, StreamingDeduplicator
from ..concurrency import PLAYWRIGHT_AVAILABLE, BrowserFetcher
from ..discovery import CompositeDiscoverer, LinkCrawler, PatternFilter, SitemapDiscoverer
from ..discovery.link_extractors import StaticLinkExtractor
from ..http import AdaptiveRateLimiter, AsyncHttpClient, PerHostRateLimiter
from ..models.config import DocpullConfig
from ..models.events import EventType, FetchEvent, FetchStats, SkipReason
from ..models.profiles import apply_profile
from ..pipeline.base import FetchPipeline
from ..pipeline.base import FetchStep as FetchStepProtocol
from ..pipeline.steps import (
    ConvertStep,
    DedupStep,
    FetchStep,
    JsonSaveStep,
    MetadataStep,
    SaveStep,
    SqliteSaveStep,
    ValidateStep,
)
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator


def _url_to_filename(url: str, base_url: str | None = None) -> str:
    """
    Convert URL to a safe filename.

    Args:
        url: The URL to convert
        base_url: Optional base URL to strip from path

    Returns:
        Safe filename string
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")

    # Remove base URL prefix if provided
    if base_url:
        base_path = urlparse(base_url).path.strip("/")
        if path.startswith(base_path):
            path = path[len(base_path) :].strip("/")

    # Convert path to filename
    if not path or path == "/":
        filename = "index"
    else:
        # Replace path separators with underscores
        filename = path.replace("/", "_")
        # Remove file extension if present
        if filename.endswith(".html") or filename.endswith(".htm"):
            filename = filename.rsplit(".", 1)[0]

    # Clean up filename
    filename = re.sub(r"[^\w\-]", "_", filename)
    filename = re.sub(r"_+", "_", filename)
    filename = filename.strip("_")

    return filename + ".md"


class Fetcher:
    """
    Primary API for docpull v2.0 - streaming events.

    The Fetcher provides an async iterator interface that yields events
    as the fetch operation progresses. This enables:
    - Real-time progress tracking
    - Early termination via cancel()
    - Integration with RAG pipelines
    - Custom event handling

    Example:
        config = DocpullConfig(
            url="https://docs.example.com",
            profile=ProfileName.RAG,
        )

        async with Fetcher(config) as fetcher:
            async for event in fetcher.run():
                if event.type == EventType.FETCH_PROGRESS:
                    print(f"Progress: {event.current}/{event.total}")
                elif event.type == EventType.FETCH_FAILED:
                    print(f"Error: {event.url} - {event.error}")

        print(f"Stats: {fetcher.stats.to_dict()}")
    """

    def __init__(self, config: DocpullConfig):
        """
        Initialize the Fetcher.

        Args:
            config: Configuration for the fetch operation.
                    Profile defaults will be applied automatically.
        """
        self.config = apply_profile(config)
        self._cancelled = False
        self._stats = FetchStats()
        self._start_time: float | None = None

        # Components (initialized in __aenter__)
        self._rate_limiter: PerHostRateLimiter | None = None
        self._http_client: AsyncHttpClient | None = None
        self._url_validator: UrlValidator | None = None
        self._robots_checker: RobotsChecker | None = None
        self._pipeline: FetchPipeline | None = None
        self._discoverer: CompositeDiscoverer | None = None
        self._streaming_dedup: StreamingDeduplicator | None = None
        self._cache_manager: CacheManager | None = None
        self._browser_fetcher: BrowserFetcher | None = None
        self._json_saver: JsonSaveStep | None = None
        self._sqlite_saver: SqliteSaveStep | None = None

    @property
    def stats(self) -> FetchStats:
        """Get current fetch statistics."""
        return self._stats

    def cancel(self) -> None:
        """
        Request graceful cancellation of the fetch operation.

        The fetch will complete the current page and then stop.
        A CANCELLED event will be emitted.
        """
        self._cancelled = True

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers from config."""
        import base64

        from ..models.config import AuthType

        headers: dict[str, str] = {}
        auth = self.config.auth

        if auth.type == AuthType.NONE:
            return headers

        if auth.type == AuthType.BEARER:
            if auth.token:
                headers["Authorization"] = f"Bearer {auth.token}"

        elif auth.type == AuthType.BASIC:
            if auth.username and auth.password:
                credentials = f"{auth.username}:{auth.password}"
                encoded = base64.b64encode(credentials.encode()).decode()
                headers["Authorization"] = f"Basic {encoded}"

        elif auth.type == AuthType.COOKIE:
            if auth.cookie:
                headers["Cookie"] = auth.cookie

        elif auth.type == AuthType.HEADER and auth.header_name and auth.header_value:
            headers[auth.header_name] = auth.header_value

        return headers

    async def __aenter__(self) -> Fetcher:
        """Enter async context and initialize components."""
        # Create rate limiter (adaptive if configured)
        if self.config.crawl.adaptive_rate_limit:
            self._rate_limiter = AdaptiveRateLimiter(
                default_delay=self.config.crawl.rate_limit,
                default_concurrent=self.config.crawl.per_host_concurrent,
            )
        else:
            self._rate_limiter = PerHostRateLimiter(
                default_delay=self.config.crawl.rate_limit,
                default_concurrent=self.config.crawl.per_host_concurrent,
            )

        # Build authentication headers from config
        auth_headers = self._build_auth_headers()

        # Create HTTP client
        self._http_client = AsyncHttpClient(
            rate_limiter=self._rate_limiter,
            max_retries=self.config.network.max_retries,
            user_agent=self.config.network.user_agent,
            proxy=self.config.network.proxy,
            default_timeout=float(self.config.network.read_timeout),
            auth_headers=auth_headers,
        )
        await self._http_client.__aenter__()

        # Create security components
        self._url_validator = UrlValidator(allowed_schemes={"https"})
        self._robots_checker = RobotsChecker(
            user_agent=self.config.network.user_agent or "docpull/2.0",
        )

        # Build pipeline
        output_dir = self.config.output.directory.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize cache manager if caching is enabled
        if self.config.cache.enabled:
            cache_dir = self.config.cache.directory.resolve()
            self._cache_manager = CacheManager(
                cache_dir=cache_dir,
                ttl_days=self.config.cache.ttl_days,
            )
            # Evict expired entries on startup
            self._cache_manager.evict_expired()
            self._cache_manager.start_session()

        # Create streaming deduplicator if enabled
        if self.config.content_filter.streaming_dedup:
            self._streaming_dedup = StreamingDeduplicator()

        # Initialize browser fetcher if JavaScript rendering is enabled
        if self.config.crawl.javascript:
            if not PLAYWRIGHT_AVAILABLE:
                raise ImportError(
                    "JavaScript rendering requires Playwright. Install with: pip install docpull[js]"
                )
            self._browser_fetcher = BrowserFetcher(
                max_contexts=self.config.performance.browser_contexts,
                user_agent=self.config.network.user_agent,
                timeout=float(self.config.network.read_timeout),
            )
            await self._browser_fetcher.__aenter__()

        # Build pipeline steps
        steps: list[FetchStepProtocol] = [
            ValidateStep(
                url_validator=self._url_validator,
                robots_checker=self._robots_checker,
                check_existing=True,  # Skip existing files
            ),
        ]

        # Use browser or HTTP fetch based on config
        if self._browser_fetcher:
            from ..pipeline.steps.browser_fetch import BrowserFetchStep

            steps.append(BrowserFetchStep(browser_fetcher=self._browser_fetcher))
        else:
            steps.append(FetchStep(http_client=self._http_client))

        steps.append(MetadataStep(extract_rich=self.config.output.rich_metadata))

        # Add conversion step - all formats need markdown content
        # Only add frontmatter for markdown file output
        add_frontmatter = self.config.output.format == "markdown"
        steps.append(ConvertStep(add_frontmatter=add_frontmatter))

        # Add dedup step if streaming dedup is enabled
        if self._streaming_dedup:
            steps.append(DedupStep(deduplicator=self._streaming_dedup))

        # Add appropriate save step based on output format
        if self.config.output.format == "json":
            self._json_saver = JsonSaveStep(base_output_dir=output_dir)
            steps.append(self._json_saver)
        elif self.config.output.format == "sqlite":
            self._sqlite_saver = SqliteSaveStep(base_output_dir=output_dir)
            steps.append(self._sqlite_saver)
        else:
            # Default to markdown file output
            steps.append(SaveStep(base_output_dir=output_dir))

        self._pipeline = FetchPipeline(steps=steps)

        # Build pattern filter from config
        pattern_filter = None
        if self.config.crawl.include_paths or self.config.crawl.exclude_paths:
            pattern_filter = PatternFilter(
                include_patterns=self.config.crawl.include_paths or None,
                exclude_patterns=self.config.crawl.exclude_paths or None,
            )

        # Build discoverers
        # Pass robots_checker for sitemap discovery from robots.txt
        sitemap_discoverer = SitemapDiscoverer(
            http_client=self._http_client,
            url_validator=self._url_validator,
            pattern_filter=pattern_filter,
            robots_checker=self._robots_checker,
        )

        # Create link extractor based on --js flag
        # Use Any type since BrowserLinkExtractor and StaticLinkExtractor both satisfy LinkExtractor protocol
        from typing import Any

        link_extractor: Any = None
        if self._browser_fetcher:
            # Use browser-based extraction for JS-heavy sites
            from ..discovery.link_extractors.browser import BrowserLinkExtractor

            link_extractor = BrowserLinkExtractor(
                browser_pool=self._browser_fetcher._pool,
                intercept_requests=True,
                scroll_for_lazy_load=True,
            )
        else:
            # Use standard HTTP-based extraction
            link_extractor = StaticLinkExtractor(http_client=self._http_client)

        link_crawler = LinkCrawler(
            http_client=self._http_client,
            url_validator=self._url_validator,
            robots_checker=self._robots_checker,
            max_depth=self.config.crawl.max_depth,
            pattern_filter=pattern_filter,
            stay_on_domain=True,
            link_extractor=link_extractor,
        )

        self._discoverer = CompositeDiscoverer(
            sitemap_discoverer=sitemap_discoverer,
            link_crawler=link_crawler,
            fallback_threshold=5,
        )

        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context and cleanup resources."""
        if self._browser_fetcher:
            await self._browser_fetcher.__aexit__(exc_type, exc_val, exc_tb)
            self._browser_fetcher = None

        if self._http_client:
            await self._http_client.__aexit__(exc_type, exc_val, exc_tb)
            self._http_client = None

        # Finalize JSON/SQLite savers
        if self._json_saver:
            self._json_saver.finalize()
            self._json_saver = None

        if self._sqlite_saver:
            self._sqlite_saver.close()
            self._sqlite_saver = None

        # Flush cache to disk
        if self._cache_manager:
            self._cache_manager.flush()
            self._cache_manager = None

    async def discover(self) -> list[str]:
        """
        Run discovery phase only, returning list of discovered URLs.

        This is useful for previewing what URLs would be fetched without
        actually fetching them.

        Returns:
            List of discovered URLs

        Example:
            async with Fetcher(config) as fetcher:
                urls = await fetcher.discover()
                for url in urls:
                    print(url)
        """
        if self._discoverer is None:
            raise RuntimeError("Fetcher not initialized. Use 'async with' context manager.")

        urls: list[str] = []
        if self.config.url:
            async for url in self._discoverer.discover(
                self.config.url,
                max_urls=self.config.crawl.max_pages,
            ):
                urls.append(url)

        self._stats.urls_discovered = len(urls)
        return urls

    def _compute_output_path(self, url: str) -> Path:
        """
        Compute output path for a URL.

        Args:
            url: The URL to compute path for

        Returns:
            Path where the file should be saved
        """
        output_dir = self.config.output.directory.resolve()
        filename = _url_to_filename(url, self.config.url)
        return output_dir / filename

    async def run(self) -> AsyncIterator[FetchEvent]:
        """
        Execute the fetch operation, yielding events.

        This is the main entry point for the streaming API.
        Events are yielded as they occur during:
        - URL discovery (sitemaps, crawling)
        - Page fetching
        - Content processing

        Yields:
            FetchEvent objects for each significant operation

        Example:
            async for event in fetcher.run():
                match event.type:
                    case EventType.STARTED:
                        print("Starting fetch...")
                    case EventType.FETCH_PROGRESS:
                        print(f"{event.current}/{event.total} pages")
                    case EventType.COMPLETED:
                        print("Done!")
        """
        if self._pipeline is None or self._discoverer is None:
            raise RuntimeError("Fetcher not initialized. Use 'async with' context manager.")

        self._start_time = time.monotonic()

        # Emit start event
        yield FetchEvent(
            type=EventType.STARTED,
            message=f"Starting fetch of {self.config.url}",
        )

        try:
            # Phase 1: Discovery (or resume from previous run)
            discovered_urls: list[str] = []
            resumed = False

            # Check for resume capability
            if (
                self.config.cache.enabled
                and self.config.cache.resume
                and self._cache_manager
                and self.config.url
            ):
                pending_urls = self._cache_manager.get_pending_urls(self.config.url)
                if pending_urls is not None:
                    # Respect current max_pages setting even when resuming
                    max_pages = self.config.crawl.max_pages
                    if max_pages and len(pending_urls) > max_pages:
                        pending_urls = pending_urls[:max_pages]
                    discovered_urls = pending_urls
                    resumed = True
                    yield FetchEvent(
                        type=EventType.RESUMED,
                        total=len(discovered_urls),
                        message=f"Resuming with {len(discovered_urls)} pending URLs",
                    )

            # If not resuming, run discovery
            if not resumed and self.config.url:
                yield FetchEvent(
                    type=EventType.DISCOVERY_STARTED,
                    message=f"Discovering URLs from {self.config.url}",
                )

                async for url in self._discoverer.discover(
                    self.config.url,
                    max_urls=self.config.crawl.max_pages,
                ):
                    discovered_urls.append(url)

                    # Check for cancellation during discovery
                    if self._cancelled:
                        yield FetchEvent(type=EventType.CANCELLED, message="Fetch cancelled during discovery")
                        return

                # Save discovered URLs for resume capability (before fetching)
                if self.config.cache.enabled and self._cache_manager:
                    self._cache_manager.save_discovered_urls(discovered_urls, self.config.url)

            if not resumed:
                yield FetchEvent(
                    type=EventType.DISCOVERY_COMPLETE,
                    total=len(discovered_urls),
                    message=f"Discovered {len(discovered_urls)} URLs",
                )

            self._stats.urls_discovered = len(discovered_urls)

            # Check for cancellation
            if self._cancelled:
                yield FetchEvent(type=EventType.CANCELLED, message="Fetch cancelled by user")
                return

            # Phase 2: Fetch pages
            # Collect events from the pipeline
            collected_events: list[FetchEvent] = []

            def collect_event(event: FetchEvent) -> None:
                collected_events.append(event)

            for i, url in enumerate(discovered_urls):
                if self._cancelled:
                    yield FetchEvent(type=EventType.CANCELLED, message="Fetch cancelled by user")
                    return

                # Emit progress event
                yield FetchEvent(
                    type=EventType.FETCH_PROGRESS,
                    url=url,
                    current=i + 1,
                    total=len(discovered_urls),
                    message=f"Processing {i + 1}/{len(discovered_urls)}: {url}",
                )

                # Compute output path
                output_path = self._compute_output_path(url)

                # Execute pipeline
                collected_events.clear()

                if self.config.dry_run:
                    # Dry run - just emit what would happen
                    yield FetchEvent(
                        type=EventType.FETCH_SKIPPED,
                        url=url,
                        output_path=output_path,
                        message=f"[dry-run] Would save to {output_path}",
                        skip_reason=SkipReason.DRY_RUN,
                    )
                    self._stats.pages_skipped += 1
                    continue

                ctx = await self._pipeline.execute(url, output_path, emit=collect_event)

                # Yield collected events
                for event in collected_events:
                    yield event

                # Update stats and cache based on result
                if ctx.error:
                    self._stats.pages_failed += 1
                    if self._cache_manager:
                        self._cache_manager.mark_failed(url)
                elif ctx.should_skip:
                    self._stats.pages_skipped += 1
                else:
                    self._stats.pages_fetched += 1
                    self._stats.bytes_downloaded += ctx.bytes_downloaded
                    self._stats.files_saved += 1

                    # Update cache with successful fetch
                    if self._cache_manager and ctx.markdown:
                        self._cache_manager.update_cache(
                            url=url,
                            content=ctx.markdown,
                            file_path=output_path,
                            etag=ctx.etag,
                            last_modified=ctx.last_modified,
                        )
                        self._cache_manager.mark_fetched(url)

            # Calculate duration
            self._stats.duration_seconds = time.monotonic() - self._start_time

            # Clear discovered URLs on successful completion (no failures)
            if self._cache_manager and self._stats.pages_failed == 0:
                self._cache_manager.clear_discovered_urls()

            # Emit completion event
            yield FetchEvent(
                type=EventType.COMPLETED,
                message=(
                    f"Fetch completed: {self._stats.pages_fetched} saved, "
                    f"{self._stats.pages_skipped} skipped, "
                    f"{self._stats.pages_failed} failed"
                ),
            )

        except Exception as e:
            self._stats.duration_seconds = time.monotonic() - self._start_time
            yield FetchEvent(
                type=EventType.FAILED,
                error=str(e),
                message=f"Fetch failed: {e}",
            )
            raise


def fetch_blocking(
    url: str,
    on_event: Callable[[FetchEvent], None] | None = None,
    **kwargs: object,
) -> Path:
    """
    Blocking fetch with optional event callback.

    This is a convenience wrapper for sync code that can't use async/await.
    For async code, use the Fetcher class directly.

    WARNING: Do not call from within an existing event loop (e.g., Jupyter,
    asyncio-based frameworks). Use the async Fetcher API instead.

    Args:
        url: The URL to fetch
        on_event: Optional callback for events (for progress tracking)
        **kwargs: Additional config options passed to DocpullConfig

    Returns:
        Path to the output directory

    Example:
        def print_progress(event):
            if event.type == EventType.FETCH_PROGRESS:
                print(f"{event.current}/{event.total}")

        output_dir = fetch_blocking(
            "https://docs.example.com",
            on_event=print_progress,
            profile=ProfileName.RAG,
        )
    """
    # Detect if we're already in an async context
    try:
        asyncio.get_running_loop()
        raise RuntimeError("fetch_blocking() called from async context. Use 'async with Fetcher()' instead.")
    except RuntimeError as e:
        if "no running event loop" not in str(e).lower():
            raise

    config = DocpullConfig(url=url, **kwargs)  # type: ignore[arg-type]

    async def _run() -> Path:
        async with Fetcher(config) as fetcher:
            async for event in fetcher.run():
                if on_event:
                    on_event(event)
        return config.output.directory

    return asyncio.run(_run())
