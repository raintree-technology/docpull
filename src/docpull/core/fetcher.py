"""Main Fetcher class with streaming event API."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from types import TracebackType
from urllib.parse import urlparse

from ..cache import CacheManager, StreamingDeduplicator
from ..discovery import CompositeDiscoverer, LinkCrawler, PatternFilter, SitemapDiscoverer
from ..discovery.link_extractors import StaticLinkExtractor
from ..http import AdaptiveRateLimiter, AsyncHttpClient, PerHostRateLimiter
from ..models.config import DocpullConfig
from ..models.events import EventType, FetchEvent, FetchStats, SkipReason
from ..models.profiles import apply_profile
from ..pipeline.base import FetchPipeline, PageContext
from ..pipeline.base import FetchStep as FetchStepProtocol
from ..pipeline.steps import (
    ChunkStep,
    ConvertStep,
    DedupStep,
    FetchStep,
    JsonSaveStep,
    MetadataStep,
    NdjsonSaveStep,
    SaveStep,
    SqliteSaveStep,
    ValidateStep,
)
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator


def _url_to_filename(url: str, base_url: str | None = None) -> str:
    """
    Convert URL to a safe flattened filename (e.g. ``api_auth_oauth2.md``).

    Used by the ``full`` / ``flat`` / ``short`` naming strategies. For the
    ``hierarchical`` strategy, see :func:`_url_to_path_parts`.

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


_PATH_SAFE_RE = re.compile(r"[^\w\-.]")


def _sanitize_path_segment(segment: str) -> str:
    """Make a single URL path segment safe for use as a filesystem name.

    Strips characters outside ``[\\w\\-.]``, collapses runs of underscores,
    and refuses traversal sequences. Returns ``index`` for an empty result so
    the segment never disappears.
    """
    cleaned = _PATH_SAFE_RE.sub("_", segment)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        return "index"
    return cleaned


def _url_to_path_parts(url: str, base_url: str | None = None) -> list[str]:
    """
    Convert URL to a list of safe path segments for hierarchical naming.

    The final segment is the filename (with ``.md`` extension); preceding
    segments are directories. A trailing slash collapses to ``<...>/index.md``.

    Examples:
        ``https://docs.foo.com/api/auth/oauth2`` →
            ``["api", "auth", "oauth2.md"]``
        ``https://docs.foo.com/api/`` →
            ``["api", "index.md"]``
        ``https://docs.foo.com/`` →
            ``["index.md"]``
    """
    parsed = urlparse(url)
    raw_path = parsed.path

    if base_url:
        base_path = urlparse(base_url).path.strip("/")
        stripped = raw_path.strip("/")
        if base_path and stripped.startswith(base_path):
            stripped = stripped[len(base_path) :]
            raw_path = "/" + stripped + ("/" if raw_path.endswith("/") else "")

    trailing_slash = raw_path.endswith("/")
    parts = [seg for seg in raw_path.split("/") if seg]

    if not parts:
        return ["index.md"]

    sanitized = [_sanitize_path_segment(p) for p in parts]

    last = sanitized[-1]
    if last.endswith(".html") or last.endswith(".htm"):
        last = last.rsplit(".", 1)[0]

    if trailing_slash:
        return [*sanitized[:-1], last, "index.md"]

    return [*sanitized[:-1], last + ".md"]


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
        self._json_saver: JsonSaveStep | None = None
        self._sqlite_saver: SqliteSaveStep | None = None
        self._ndjson_saver: NdjsonSaveStep | None = None
        self._save_step: SaveStep | None = None

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

        # Create security components first so every transport can reuse them
        self._url_validator = UrlValidator(allowed_schemes={"https"})

        # Build authentication headers from config
        auth_headers = self._build_auth_headers()
        auth_scope_hosts: set[str] | None = None
        if auth_headers and self.config.url:
            hostname = urlparse(self.config.url).hostname
            if hostname:
                auth_scope_hosts = {hostname.lower()}

        # Create HTTP client. Per-page download cap: prefer the user-supplied
        # `content_filter.max_file_size`; fall back to AsyncHttpClient's
        # built-in 50 MB ceiling.
        max_content_size_kw: dict[str, int] = {}
        if self.config.content_filter.max_file_size is not None:
            max_content_size_kw["max_content_size"] = int(
                self.config.content_filter.max_file_size
            )
        self._http_client = AsyncHttpClient(
            rate_limiter=self._rate_limiter,
            max_retries=self.config.network.max_retries,
            user_agent=self.config.network.user_agent,
            proxy=self.config.network.proxy,
            default_timeout=float(self.config.network.read_timeout),
            auth_headers=auth_headers,
            url_validator=self._url_validator,
            allow_insecure_tls=self.config.network.insecure_tls,
            auth_scope_hosts=auth_scope_hosts,
            require_pinned_dns=self.config.network.require_pinned_dns,
            **max_content_size_kw,
        )
        await self._http_client.__aenter__()

        # robots.txt checker uses the SAME User-Agent the HTTP client will
        # send. Keeping these aligned means site operators can target docpull
        # via robots.txt User-Agent rules and have their intent honored.
        self._robots_checker = RobotsChecker(
            user_agent=self._http_client.user_agent,
            url_validator=self._url_validator,
            allow_insecure_tls=self.config.network.insecure_tls,
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

        # Build pipeline steps
        cache_enabled = self._cache_manager is not None
        steps: list[FetchStepProtocol] = [
            ValidateStep(
                url_validator=self._url_validator,
                robots_checker=self._robots_checker,
                check_existing=True,
                cache_enabled=cache_enabled,
            ),
        ]

        steps.append(
            FetchStep(
                http_client=self._http_client,
                cache_manager=self._cache_manager,
                skip_unchanged=self.config.cache.skip_unchanged,
            )
        )

        steps.append(MetadataStep(extract_rich=self.config.output.rich_metadata))

        # Add conversion step - all formats need markdown content
        # Only add frontmatter for markdown file output
        add_frontmatter = self.config.output.format == "markdown"
        steps.append(
            ConvertStep(
                add_frontmatter=add_frontmatter,
                enable_special_cases=self.config.content_filter.enable_special_cases,
                use_trafilatura=self.config.content_filter.extractor == "trafilatura",
                strict_js_required=self.config.content_filter.strict_js_required,
            )
        )

        # Add dedup step if streaming dedup is enabled
        if self._streaming_dedup:
            steps.append(DedupStep(deduplicator=self._streaming_dedup))

        # Optional token-aware chunking (runs after dedup so skipped pages
        # don't incur chunking cost).
        if self.config.output.max_tokens_per_file:
            steps.append(
                ChunkStep(
                    max_tokens=self.config.output.max_tokens_per_file,
                    tokenizer=self.config.output.tokenizer,
                )
            )

        # Add appropriate save step based on output format
        if self.config.output.format == "json":
            self._json_saver = JsonSaveStep(base_output_dir=output_dir)
            steps.append(self._json_saver)
        elif self.config.output.format == "ndjson":
            self._ndjson_saver = NdjsonSaveStep(
                base_output_dir=output_dir,
                filename=self.config.output.ndjson_filename,
                emit_chunks=self.config.output.emit_chunks,
            )
            steps.append(self._ndjson_saver)
        elif self.config.output.format == "sqlite":
            self._sqlite_saver = SqliteSaveStep(base_output_dir=output_dir)
            steps.append(self._sqlite_saver)
        else:
            # Default to markdown file output. SaveStep also writes
            # SKILL.md on finalize() when output.skill_name is set.
            self._save_step = SaveStep(
                base_output_dir=output_dir,
                emit_chunks=self.config.output.emit_chunks,
                skill_name=self.config.output.skill_name,
                skill_description=self.config.output.skill_description,
            )
            steps.append(self._save_step)

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
        if self._http_client:
            await self._http_client.__aexit__(exc_type, exc_val, exc_tb)
            self._http_client = None

        # Finalize JSON/NDJSON/SQLite savers
        if self._json_saver:
            self._json_saver.finalize()
            self._json_saver = None

        if self._ndjson_saver:
            self._ndjson_saver.finalize()
            self._ndjson_saver = None

        if self._sqlite_saver:
            self._sqlite_saver.close()
            self._sqlite_saver = None

        # Write SKILL.md when --skill mode produced a manifest-shaped run.
        if self._save_step:
            self._save_step.finalize()
            self._save_step = None

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

    async def fetch_one(self, url: str, *, save: bool = True) -> PageContext:
        """Fetch a single URL, bypassing discovery.

        Designed for AI-agent tool loops where each call wants one page back
        as fast as possible. Skips sitemap parsing and link crawling.

        Args:
            url: The URL to fetch.
            save: When False, skip writing to disk and return the context with
                ``ctx.markdown`` populated. Useful for agents that want the
                Markdown in-memory without side effects.

        Returns:
            ``PageContext`` with ``markdown`` (and ``chunks`` if chunking is
            enabled) populated. ``ctx.error`` holds the error message on
            failure; ``ctx.should_skip`` indicates SPAs or dedup hits.
        """
        if self._pipeline is None:
            raise RuntimeError("Fetcher not initialized. Use 'async with' context manager.")
        output_path = self._compute_output_path(url)

        steps = self._pipeline.steps
        if not save:
            steps = [
                s
                for s in steps
                if s.name not in {"save", "save_json", "save_ndjson", "save_sqlite"}
            ]
        pipeline = type(self._pipeline)(steps=steps)
        ctx = await pipeline.execute(url, output_path)
        if ctx.error:
            self._stats.pages_failed += 1
        elif ctx.should_skip:
            self._stats.pages_skipped += 1
        else:
            self._stats.pages_fetched += 1
            self._stats.bytes_downloaded += ctx.bytes_downloaded
            if save:
                self._stats.files_saved += 1
        return ctx

    def _compute_output_path(self, url: str) -> Path:
        """
        Compute output path for a URL using the configured naming strategy.

        - ``full`` / ``flat`` / ``short``: a single flattened filename
          (URL path joined with underscores).
        - ``hierarchical``: URL path preserved as nested directories,
          terminating in ``<segment>.md`` or ``index.md`` for trailing
          slashes. The leaf is `_validate_output_path`-safe — every segment
          is ``[\\w\\-.]+``.
        """
        output_dir = self.config.output.directory.resolve()
        strategy = self.config.output.naming_strategy

        if strategy == "hierarchical":
            parts = _url_to_path_parts(url, self.config.url)
            return output_dir.joinpath(*parts)

        # full / flat / short: aliased to full until 3.0
        filename = _url_to_filename(url, self.config.url)
        return output_dir / filename

    async def run(self) -> AsyncIterator[FetchEvent]:
        """
        Execute the fetch operation, yielding events.

        Two execution modes share this entry point. The default is the
        streaming-discovery pipeline (URLs flow through a worker pool as
        the discoverer yields them, so the first page can save before
        discovery completes). Setting ``crawl.streaming_discovery=False``
        falls back to the legacy "discover all, then fetch sequentially"
        path — useful as a backstop if a queue-backpressure regression
        appears in 2.x.

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
        yield FetchEvent(
            type=EventType.STARTED,
            message=f"Starting fetch of {self.config.url}",
        )

        # Resume short-circuits both modes — the URL list is already known.
        resume_urls = self._resume_urls()
        if resume_urls is not None:
            yield FetchEvent(
                type=EventType.RESUMED,
                total=len(resume_urls),
                message=f"Resuming with {len(resume_urls)} pending URLs",
            )
            async for event in self._fetch_collected(resume_urls):
                yield event
            return

        try:
            if self.config.crawl.streaming_discovery:
                async for event in self._run_streaming():
                    yield event
            else:
                async for event in self._run_collected():
                    yield event
        except Exception as e:
            self._stats.duration_seconds = time.monotonic() - self._start_time
            yield FetchEvent(
                type=EventType.FAILED,
                error=str(e),
                message=f"Fetch failed: {e}",
            )
            raise

    def _resume_urls(self) -> list[str] | None:
        """Pending-URL list when caching+resume is active and matches start_url."""
        if (
            not self.config.cache.enabled
            or not self.config.cache.resume
            or self._cache_manager is None
            or not self.config.url
        ):
            return None
        pending = self._cache_manager.get_pending_urls(self.config.url)
        if pending is None:
            return None
        max_pages = self.config.crawl.max_pages
        return pending[:max_pages] if max_pages and len(pending) > max_pages else pending

    async def _run_collected(self) -> AsyncIterator[FetchEvent]:
        """Legacy mode: discover everything, then fetch sequentially."""
        if self.config.url is None:
            return
        assert self._discoverer is not None
        start_url = self.config.url
        yield FetchEvent(
            type=EventType.DISCOVERY_STARTED,
            message=f"Discovering URLs from {start_url}",
        )

        discovered: list[str] = []
        async for url in self._discoverer.discover(
            start_url, max_urls=self.config.crawl.max_pages
        ):
            discovered.append(url)
            if self._cancelled:
                yield FetchEvent(
                    type=EventType.CANCELLED,
                    message="Fetch cancelled during discovery",
                )
                return

        if self.config.cache.enabled and self._cache_manager:
            self._cache_manager.save_discovered_urls(discovered, start_url)

        yield FetchEvent(
            type=EventType.DISCOVERY_COMPLETE,
            total=len(discovered),
            message=f"Discovered {len(discovered)} URLs",
        )
        self._stats.urls_discovered = len(discovered)

        if self._cancelled:
            yield FetchEvent(type=EventType.CANCELLED, message="Fetch cancelled by user")
            return

        async for event in self._fetch_collected(discovered):
            yield event

    async def _fetch_collected(self, urls: list[str]) -> AsyncIterator[FetchEvent]:
        """Process a known URL list sequentially. Used by legacy mode and resume."""
        assert self._pipeline is not None
        assert self._start_time is not None
        self._stats.urls_discovered = len(urls)

        collected_events: list[FetchEvent] = []

        def collect(event: FetchEvent) -> None:
            collected_events.append(event)

        for i, url in enumerate(urls):
            if self._cancelled:
                yield FetchEvent(type=EventType.CANCELLED, message="Fetch cancelled by user")
                return

            yield FetchEvent(
                type=EventType.FETCH_PROGRESS,
                url=url,
                current=i + 1,
                total=len(urls),
                message=f"Processing {i + 1}/{len(urls)}: {url}",
            )

            if self.config.dry_run:
                yield FetchEvent(
                    type=EventType.FETCH_SKIPPED,
                    url=url,
                    output_path=self._compute_output_path(url),
                    message=f"[dry-run] Would save to {self._compute_output_path(url)}",
                    skip_reason=SkipReason.DRY_RUN,
                )
                self._stats.pages_skipped += 1
                continue

            output_path = self._compute_output_path(url)
            collected_events.clear()
            ctx = await self._pipeline.execute(url, output_path, emit=collect)
            for ev in collected_events:
                yield ev
            self._record_result(url, output_path, ctx)

        self._stats.duration_seconds = time.monotonic() - self._start_time
        if self._cache_manager and self._stats.pages_failed == 0:
            self._cache_manager.clear_discovered_urls()
        yield FetchEvent(
            type=EventType.COMPLETED,
            message=(
                f"Fetch completed: {self._stats.pages_fetched} saved, "
                f"{self._stats.pages_skipped} skipped, "
                f"{self._stats.pages_failed} failed"
            ),
        )

    async def _run_streaming(self) -> AsyncIterator[FetchEvent]:
        """Producer-consumer mode: discoverer feeds a queue; N workers
        drain it through the pipeline as URLs arrive.

        Backpressure: ``url_queue`` is bounded so a runaway discoverer
        can't pile up unbounded RAM. The discoverer awaits ``put`` when
        the queue is full, which naturally gates discovery rate to fetch
        rate.
        """
        if self.config.url is None:
            return
        assert self._discoverer is not None
        assert self._pipeline is not None
        assert self._start_time is not None
        # Local references so the inner closures don't have to re-narrow
        # `Optional[X]` against the same instance attributes — mypy can't
        # prove non-None across closure boundaries otherwise.
        discoverer = self._discoverer
        pipeline = self._pipeline
        start_url = self.config.url

        worker_count = max(1, self.config.crawl.max_concurrent)
        url_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=worker_count * 4)
        event_queue: asyncio.Queue[FetchEvent | None] = asyncio.Queue()

        progress_counter = {"saved": 0}

        async def discover_into_queue() -> None:
            discovered_for_resume: list[str] = []
            await event_queue.put(
                FetchEvent(
                    type=EventType.DISCOVERY_STARTED,
                    message=f"Discovering URLs from {start_url}",
                )
            )
            try:
                async for url in discoverer.discover(
                    start_url, max_urls=self.config.crawl.max_pages
                ):
                    if self._cancelled:
                        break
                    await url_queue.put(url)
                    discovered_for_resume.append(url)
                    # Periodically flush the resume snapshot so a crash
                    # mid-discovery doesn't lose every URL we've found.
                    if (
                        self.config.cache.enabled
                        and self._cache_manager
                        and len(discovered_for_resume) % 200 == 0
                    ):
                        self._cache_manager.save_discovered_urls(
                            list(discovered_for_resume), start_url
                        )
            finally:
                if self.config.cache.enabled and self._cache_manager:
                    self._cache_manager.save_discovered_urls(
                        discovered_for_resume, start_url
                    )
                self._stats.urls_discovered = len(discovered_for_resume)
                await event_queue.put(
                    FetchEvent(
                        type=EventType.DISCOVERY_COMPLETE,
                        total=len(discovered_for_resume),
                        message=f"Discovered {len(discovered_for_resume)} URLs",
                    )
                )
                # Send one sentinel per worker so each consumer can exit.
                for _ in range(worker_count):
                    await url_queue.put(None)

        async def worker() -> None:
            while True:
                url = await url_queue.get()
                if url is None or self._cancelled:
                    return
                output_path = self._compute_output_path(url)
                if self.config.dry_run:
                    await event_queue.put(
                        FetchEvent(
                            type=EventType.FETCH_SKIPPED,
                            url=url,
                            output_path=output_path,
                            message=f"[dry-run] Would save to {output_path}",
                            skip_reason=SkipReason.DRY_RUN,
                        )
                    )
                    self._stats.pages_skipped += 1
                    continue

                local_events: list[FetchEvent] = []
                # Bind the per-iteration list as a default arg so ruff B023
                # is happy. Closure is consumed synchronously by execute()
                # before the next iteration anyway, so capture order is safe.
                def emit(ev: FetchEvent, _sink: list[FetchEvent] = local_events) -> None:
                    _sink.append(ev)

                try:
                    ctx = await pipeline.execute(url, output_path, emit=emit)
                except Exception as err:  # noqa: BLE001
                    await event_queue.put(
                        FetchEvent(
                            type=EventType.FETCH_FAILED,
                            url=url,
                            error=str(err),
                            message=f"Pipeline error: {err}",
                        )
                    )
                    self._stats.pages_failed += 1
                    continue

                for ev in local_events:
                    await event_queue.put(ev)

                self._record_result(url, output_path, ctx)
                if ctx.markdown and not ctx.error and not ctx.should_skip:
                    progress_counter["saved"] += 1
                    # Synthesize a progress event so the CLI bar moves;
                    # `total` may still be unknown, so report what we know.
                    total_so_far = self._stats.urls_discovered or progress_counter["saved"]
                    await event_queue.put(
                        FetchEvent(
                            type=EventType.FETCH_PROGRESS,
                            url=url,
                            current=progress_counter["saved"],
                            total=total_so_far,
                            message=f"Saved {progress_counter['saved']}/{total_so_far}: {url}",
                        )
                    )

        discover_task = asyncio.create_task(discover_into_queue())
        worker_tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]

        async def wait_for_drain() -> None:
            await discover_task
            await asyncio.gather(*worker_tasks, return_exceptions=True)
            await event_queue.put(None)

        drain_task = asyncio.create_task(wait_for_drain())

        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                yield event
        finally:
            await drain_task

        self._stats.duration_seconds = time.monotonic() - self._start_time
        if self._cache_manager and self._stats.pages_failed == 0:
            self._cache_manager.clear_discovered_urls()
        if self._cancelled:
            yield FetchEvent(
                type=EventType.CANCELLED,
                message="Fetch cancelled by user",
            )
            return
        yield FetchEvent(
            type=EventType.COMPLETED,
            message=(
                f"Fetch completed: {self._stats.pages_fetched} saved, "
                f"{self._stats.pages_skipped} skipped, "
                f"{self._stats.pages_failed} failed"
            ),
        )

    def _record_result(self, url: str, output_path: Path, ctx: PageContext) -> None:
        """Update stats + cache from a finished pipeline run.

        Called from both modes; safe in single-threaded asyncio because
        worker tasks don't preempt mid-statement.
        """
        if ctx.error:
            self._stats.pages_failed += 1
            if self._cache_manager:
                self._cache_manager.mark_failed(url)
            return
        if ctx.should_skip:
            self._stats.pages_skipped += 1
            return
        self._stats.pages_fetched += 1
        self._stats.bytes_downloaded += ctx.bytes_downloaded
        self._stats.files_saved += 1
        if self._cache_manager and ctx.markdown:
            self._cache_manager.update_cache(
                url=url,
                content=ctx.markdown,
                file_path=output_path,
                etag=ctx.etag,
                last_modified=ctx.last_modified,
            )
            self._cache_manager.mark_fetched(url)


def fetch_one(url: str, **kwargs: object) -> PageContext:
    """Fetch a single URL synchronously and return the parsed page context.

    Convenience wrapper for AI-agent tool loops that need one page's Markdown
    in-memory as fast as possible. Skips discovery entirely, does not write
    to disk by default, and runs in the current thread.

    Example:
        >>> ctx = fetch_one("https://docs.python.org/3/library/asyncio.html")
        >>> print(ctx.markdown[:200])

    Args:
        url: The URL to fetch.
        **kwargs: Extra fields passed through to :class:`DocpullConfig`
            (e.g. ``content_filter={"extractor": "trafilatura"}``).

    Returns:
        ``PageContext`` with ``markdown`` populated on success or ``error``
        populated on failure.
    """
    try:
        asyncio.get_running_loop()
        raise RuntimeError(
            "fetch_one() called from async context. Use Fetcher.fetch_one() instead."
        )
    except RuntimeError as exc:
        if "no running event loop" not in str(exc).lower():
            raise

    config = DocpullConfig(url=url, **kwargs)  # type: ignore[arg-type]

    async def _run() -> PageContext:
        async with Fetcher(config) as fetcher:
            return await fetcher.fetch_one(url, save=False)

    return asyncio.run(_run())


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
