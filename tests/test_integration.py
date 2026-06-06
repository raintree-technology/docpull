"""Integration tests for the docpull API."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from docpull import (
    DocpullConfig,
    EventType,
    Fetcher,
    FetchEvent,
    FetchStats,
    ProfileName,
)
from docpull.core import fetch_blocking as core_fetch_blocking
from docpull.core import fetch_one as core_fetch_one
from docpull.core.fetcher import _url_to_filename
from docpull.models.events import SkipReason
from docpull.pipeline.base import FetchPipeline, PageContext, PipelineResult, PipelineStatus


class _PipelineStub:
    def __init__(self, execute):
        self.steps = []
        self._execute = execute

    async def execute(self, url: str, output_path: Path, emit=None):
        return await self._execute(url, output_path, emit=emit)

    async def execute_result(self, url: str, output_path: Path, emit=None):
        ctx = await self.execute(url, output_path, emit=emit)
        status = PipelineStatus.SKIPPED if ctx.should_skip else PipelineStatus.SUCCEEDED
        return PipelineResult(ctx=ctx, status=status)


class TestDocpullConfig:
    """Tests for DocpullConfig."""

    def test_default_config(self):
        """Test creating default config."""
        config = DocpullConfig(url="https://example.com")
        assert config.url == "https://example.com"
        assert config.profile == ProfileName.CUSTOM
        assert config.crawl.max_depth == 5
        assert config.crawl.rate_limit == 0.5
        assert config.output.format == "markdown"

    def test_rag_profile(self):
        """Test RAG profile config."""
        config = DocpullConfig(url="https://docs.example.com", profile=ProfileName.RAG)
        assert config.profile == ProfileName.RAG

    def test_mirror_profile(self):
        """Test mirror profile config."""
        config = DocpullConfig(url="https://docs.example.com", profile=ProfileName.MIRROR)
        assert config.profile == ProfileName.MIRROR

    def test_quick_profile(self):
        """Test quick profile config."""
        config = DocpullConfig(url="https://docs.example.com", profile=ProfileName.QUICK)
        assert config.profile == ProfileName.QUICK

    def test_config_with_crawl_settings(self):
        """Test config with custom crawl settings."""
        config = DocpullConfig(
            url="https://example.com",
            crawl={"max_pages": 100, "max_depth": 3, "rate_limit": 1.0},
        )
        assert config.crawl.max_pages == 100
        assert config.crawl.max_depth == 3
        assert config.crawl.rate_limit == 1.0

    def test_config_with_output_settings(self):
        """Test config with custom output settings."""
        config = DocpullConfig(
            url="https://example.com",
            output={"directory": Path("/tmp/docs"), "format": "json"},
        )
        assert config.output.directory == Path("/tmp/docs")
        assert config.output.format == "json"

    def test_config_with_network_settings(self):
        """Test config with network settings."""
        config = DocpullConfig(
            url="https://example.com",
            network={"proxy": "http://proxy:8080", "max_retries": 5},
        )
        assert config.network.proxy == "http://proxy:8080"
        assert config.network.max_retries == 5
        assert config.network.insecure_tls is False

    def test_config_rejects_insecure_tls(self):
        """Test config rejects disabling TLS verification."""
        with pytest.raises(ValueError, match="TLS certificate verification is mandatory"):
            DocpullConfig(
                url="https://example.com",
                network={"insecure_tls": True},
            )

    def test_config_rejects_proxy_with_require_pinned_dns(self):
        """Model validation should reject proxy mode when pinned DNS is required."""
        with pytest.raises(ValueError, match="require_pinned_dns"):
            DocpullConfig(
                url="https://example.com",
                network={"proxy": "http://proxy:8080", "require_pinned_dns": True},
            )

    def test_config_dry_run(self):
        """Test config with dry run enabled."""
        config = DocpullConfig(url="https://example.com", dry_run=True)
        assert config.dry_run is True

    def test_config_to_yaml(self):
        """Test config serialization to YAML."""
        config = DocpullConfig(url="https://example.com", profile=ProfileName.RAG)
        yaml_str = config.to_yaml()
        assert "url: https://example.com" in yaml_str
        assert "profile: rag" in yaml_str

    def test_config_from_yaml(self):
        """Test config loading from YAML."""
        yaml_str = """
url: https://docs.example.com
profile: mirror
crawl:
  max_pages: 50
"""
        config = DocpullConfig.from_yaml(yaml_str)
        assert config.url == "https://docs.example.com"
        assert config.profile == ProfileName.MIRROR
        assert config.crawl.max_pages == 50

    def test_config_rejects_removed_browser_settings(self):
        """Test deprecated browser crawl settings are rejected."""
        with pytest.raises(ValidationError):
            DocpullConfig(
                url="https://example.com",
                crawl={"javascript": True},
            )

        with pytest.raises(ValidationError):
            DocpullConfig(
                url="https://example.com",
                performance={"browser_contexts": 2},
            )

    def test_emit_chunks_requires_chunking(self):
        """Chunk emission is invalid unless chunking is configured."""
        with pytest.raises(ValueError, match="emit_chunks requires max_tokens_per_file"):
            DocpullConfig(
                url="https://example.com",
                output={"emit_chunks": True},
            )

    def test_skill_mode_forces_hierarchical_naming(self):
        """Skill output should always normalize to hierarchical naming."""
        config = DocpullConfig(
            url="https://example.com",
            output={"skill_name": "example-skill", "naming_strategy": "full"},
        )
        assert config.output.naming_strategy == "hierarchical"

    def test_resume_requires_cache_enabled(self):
        """Resume mode should be rejected unless the cache is enabled."""
        with pytest.raises(ValueError, match="cache.resume requires cache.enabled=True"):
            DocpullConfig(
                url="https://example.com",
                cache={"resume": True},
            )

    def test_auth_type_requires_matching_payload(self):
        """Typed auth modes should reject missing required fields."""
        with pytest.raises(ValueError, match="requires token"):
            DocpullConfig(url="https://example.com", auth={"type": "bearer"})

        with pytest.raises(ValueError, match="requires both username and password"):
            DocpullConfig(url="https://example.com", auth={"type": "basic", "username": "u"})

        with pytest.raises(ValueError, match="requires cookie"):
            DocpullConfig(url="https://example.com", auth={"type": "cookie"})

        with pytest.raises(ValueError, match="requires both header_name and header_value"):
            DocpullConfig(url="https://example.com", auth={"type": "header", "header_name": "X-Test"})

    def test_auth_fields_require_non_none_type(self):
        """Auth payload should not silently no-op under auth.type=none."""
        with pytest.raises(ValueError, match="auth.type is 'none'"):
            DocpullConfig(
                url="https://example.com",
                auth={"token": "secret-token"},
            )


class TestUrlToFilename:
    """Tests for URL to filename conversion."""

    def test_simple_path(self):
        """Test simple path conversion."""
        result = _url_to_filename("https://example.com/docs/intro")
        assert result == "docs_intro.md"

    def test_index_page(self):
        """Test index page conversion."""
        result = _url_to_filename("https://example.com/")
        assert result == "index.md"

    def test_html_extension_stripped(self):
        """Test that .html extension is stripped."""
        result = _url_to_filename("https://example.com/page.html")
        assert result == "page.md"

    def test_base_url_stripped(self):
        """Test that base URL path is stripped."""
        result = _url_to_filename(
            "https://example.com/docs/api/v1/endpoint", base_url="https://example.com/docs"
        )
        assert result == "api_v1_endpoint.md"

    def test_special_chars_sanitized(self):
        """Test that special characters are sanitized."""
        result = _url_to_filename("https://example.com/page?foo=bar")
        assert "?" not in result


class TestFetchEvent:
    """Tests for FetchEvent."""

    def test_create_started_event(self):
        """Test creating a started event."""
        event = FetchEvent(type=EventType.STARTED, message="Starting fetch")
        assert event.type == EventType.STARTED
        assert event.message == "Starting fetch"

    def test_create_progress_event(self):
        """Test creating a progress event."""
        event = FetchEvent(
            type=EventType.FETCH_PROGRESS,
            url="https://example.com/page",
            current=5,
            total=10,
            processed_count=5,
            saved_count=3,
            skipped_count=1,
            failed_count=1,
            message="Fetching 5/10",
        )
        assert event.type == EventType.FETCH_PROGRESS
        assert event.url == "https://example.com/page"
        assert event.current == 5
        assert event.total == 10
        assert event.processed_count == 5
        assert event.saved_count == 3
        assert event.skipped_count == 1
        assert event.failed_count == 1

    def test_create_completed_event(self):
        """Test creating a completed event."""
        event = FetchEvent(type=EventType.COMPLETED, message="Done")
        assert event.type == EventType.COMPLETED


class TestFetchStats:
    """Tests for FetchStats."""

    def test_default_stats(self):
        """Test default stats values."""
        stats = FetchStats()
        assert stats.urls_discovered == 0
        assert stats.pages_fetched == 0
        assert stats.pages_skipped == 0
        assert stats.pages_failed == 0
        assert stats.bytes_downloaded == 0

    def test_stats_to_dict(self):
        """Test stats serialization."""
        stats = FetchStats()
        stats.urls_discovered = 10
        stats.pages_fetched = 8
        stats.pages_skipped = 1
        stats.pages_failed = 1
        stats_dict = stats.to_dict()
        assert stats_dict["urls_discovered"] == 10
        assert stats_dict["pages_fetched"] == 8


class TestFetcherMocked:
    """Tests for Fetcher with mocked HTTP."""

    @pytest.fixture
    def mock_http_response(self):
        """Create mock HTTP response."""
        response = MagicMock()
        response.status_code = 200
        response.content = b"<html><body><h1>Test</h1><p>Content</p></body></html>"
        response.content_type = "text/html"
        return response

    @pytest.fixture
    def mock_config(self, tmp_path):
        """Create test config."""
        return DocpullConfig(
            url="https://docs.example.com",
            profile=ProfileName.QUICK,
            output={"directory": tmp_path},
            crawl={"max_pages": 5},
        )

    @pytest.mark.asyncio
    async def test_fetcher_initialization(self, mock_config):
        """Test Fetcher initialization."""
        # Test that config is applied
        with patch("docpull.core.fetcher.AsyncHttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            fetcher = Fetcher(mock_config)
            assert fetcher.config.url == "https://docs.example.com"

    @pytest.mark.asyncio
    async def test_fetcher_stats(self, mock_config):
        """Test Fetcher stats tracking."""
        with patch("docpull.core.fetcher.AsyncHttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            fetcher = Fetcher(mock_config)
            assert fetcher.stats.pages_fetched == 0
            assert fetcher.stats.pages_failed == 0

    @pytest.mark.asyncio
    async def test_fetcher_cancel(self, mock_config):
        """Test Fetcher cancellation."""
        with patch("docpull.core.fetcher.AsyncHttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)

            fetcher = Fetcher(mock_config)
            assert fetcher._cancelled is False
            fetcher.cancel()
            assert fetcher._cancelled is True

    @pytest.mark.asyncio
    async def test_streaming_discovery_failure_does_not_hang(self, mock_config):
        """Test discovery errors surface instead of deadlocking the stream."""
        with patch("docpull.core.fetcher.AsyncHttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.user_agent = "docpull-test"

            fetcher = Fetcher(mock_config)
            async with fetcher:

                async def broken_discover(_: str, max_urls: int | None = None):
                    if max_urls:
                        pass
                    yield "https://docs.example.com/page-1"
                    raise RuntimeError("discovery exploded")

                async def execute(url: str, output_path: Path, emit=None):
                    if emit:
                        emit(FetchEvent(type=EventType.FETCH_PROGRESS, url=url, message="progress"))
                    return PageContext(url=url, output_path=output_path, markdown="ok", bytes_downloaded=1)

                fetcher._discoverer = MagicMock()
                fetcher._discoverer.discover = broken_discover
                fetcher._pipeline = _PipelineStub(execute)

                with pytest.raises(RuntimeError, match="discovery exploded"):
                    await asyncio.wait_for(_collect_events(fetcher.run()), timeout=1)

    @pytest.mark.asyncio
    async def test_record_result_marks_empty_markdown_as_fetched(self, mock_config):
        """Test resume state is updated even when markdown is empty."""
        fetcher = Fetcher(mock_config)
        fetcher._cache_manager = MagicMock()
        ctx = PageContext(
            url="https://docs.example.com/empty",
            output_path=mock_config.output.directory / "empty.md",
            markdown="",
        )

        fetcher._record_result(ctx.url, ctx.output_path, ctx)

        fetcher._cache_manager.update_cache.assert_called_once_with(
            url=ctx.url,
            content="",
            file_path=ctx.output_path,
            etag=None,
            last_modified=None,
        )
        fetcher._cache_manager.mark_fetched.assert_called_once_with(ctx.url)

    @pytest.mark.asyncio
    async def test_fetch_one_save_updates_cache_state(self, mock_config):
        """Test single-page saved fetches share normal cache bookkeeping."""

        class StubStep:
            name = "stub"

            async def execute(self, ctx: PageContext, emit=None) -> PageContext:
                ctx.markdown = "body"
                ctx.bytes_downloaded = 12
                return ctx

        fetcher = Fetcher(mock_config)
        fetcher._pipeline = FetchPipeline(steps=[StubStep()])
        fetcher._cache_manager = MagicMock()

        ctx = await fetcher.fetch_one("https://docs.example.com/one", save=True)

        assert ctx.markdown == "body"
        assert fetcher.stats.pages_fetched == 1
        assert fetcher.stats.files_saved == 1
        fetcher._cache_manager.update_cache.assert_called_once()
        fetcher._cache_manager.mark_fetched.assert_called_once_with("https://docs.example.com/one")

    @pytest.mark.asyncio
    async def test_streaming_progress_counts_empty_markdown(self, mock_config):
        """Test streaming mode still emits progress for empty successful pages."""
        mock_config.crawl.max_concurrent = 1
        with patch("docpull.core.fetcher.AsyncHttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.user_agent = "docpull-test"

            fetcher = Fetcher(mock_config)
            async with fetcher:

                async def single_discover(_: str, max_urls: int | None = None):
                    if max_urls:
                        pass
                    yield "https://docs.example.com/empty"

                async def execute(url: str, output_path: Path, emit=None):
                    return PageContext(url=url, output_path=output_path, markdown="", bytes_downloaded=0)

                fetcher._discoverer = MagicMock()
                fetcher._discoverer.discover = single_discover
                fetcher._pipeline = _PipelineStub(execute)

                events = await _collect_events(fetcher.run())

        progress_events = [event for event in events if event.type == EventType.FETCH_PROGRESS]
        assert any(
            event.url == "https://docs.example.com/empty"
            and event.current == 1
            and event.processed_count == 1
            and event.saved_count == 1
            and event.skipped_count == 0
            and event.failed_count == 0
            for event in progress_events
        )

    @pytest.mark.asyncio
    async def test_streaming_progress_counts_failures_as_processed(self, mock_config):
        """Streaming progress should advance for failures, not only saves."""
        mock_config.crawl.max_concurrent = 1
        with patch("docpull.core.fetcher.AsyncHttpClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.user_agent = "docpull-test"

            fetcher = Fetcher(mock_config)
            async with fetcher:

                async def two_urls(_: str, max_urls: int | None = None):
                    if max_urls:
                        pass
                    yield "https://docs.example.com/ok"
                    yield "https://docs.example.com/fail"

                async def execute(url: str, output_path: Path, emit=None):
                    if url.endswith("/fail"):
                        raise RuntimeError("boom")
                    return PageContext(url=url, output_path=output_path, markdown="body", bytes_downloaded=1)

                fetcher._discoverer = MagicMock()
                fetcher._discoverer.discover = two_urls
                fetcher._pipeline = _PipelineStub(execute)

                events = await _collect_events(fetcher.run())

        progress_events = [event for event in events if event.type == EventType.FETCH_PROGRESS]
        assert any(
            event.url == "https://docs.example.com/fail"
            and event.current == 2
            and event.processed_count == 2
            and event.saved_count == 1
            and event.skipped_count == 0
            and event.failed_count == 1
            for event in progress_events
        )

    @pytest.mark.asyncio
    async def test_record_result_counts_deduplicated_skips_separately(self, mock_config):
        fetcher = Fetcher(mock_config)
        ctx = PageContext(
            url="https://docs.example.com/dup",
            output_path=mock_config.output.directory / "dup.md",
            should_skip=True,
            skip_reason="Duplicate of https://docs.example.com/original",
            skip_code=SkipReason.DUPLICATE_CONTENT,
        )

        fetcher._record_result(ctx.url, ctx.output_path, ctx)

        assert fetcher.stats.pages_skipped == 1
        assert fetcher.stats.pages_deduplicated == 1


async def _collect_events(stream):
    return [event async for event in stream]


class TestEventTypes:
    """Tests for all event types."""

    def test_all_event_types_exist(self):
        """Test that all expected event types exist."""
        expected_events = [
            "STARTED",
            "DISCOVERY_STARTED",
            "DISCOVERY_COMPLETE",
            "FETCH_PROGRESS",
            "FETCH_SKIPPED",
            "FETCH_FAILED",
            "CANCELLED",
            "COMPLETED",
            "FAILED",
        ]
        for event_name in expected_events:
            assert hasattr(EventType, event_name), f"Missing event type: {event_name}"


class TestProfileDefaults:
    """Tests for profile default values."""

    def test_rag_profile_defaults(self):
        """Test RAG profile applies correct defaults."""
        from docpull.models.profiles import apply_profile

        config = DocpullConfig(url="https://example.com", profile=ProfileName.RAG)
        config = apply_profile(config)
        # RAG profile should have streaming dedup enabled by default
        assert config.content_filter.streaming_dedup is True

    def test_mirror_profile_defaults(self):
        """Test mirror profile applies correct defaults."""
        from docpull.models.profiles import apply_profile

        config = DocpullConfig(url="https://example.com", profile=ProfileName.MIRROR)
        config = apply_profile(config)
        # Mirror profile should have no page limit
        assert config.crawl.max_pages is None

    def test_quick_profile_defaults(self):
        """Test quick profile applies correct defaults."""
        from docpull.models.profiles import apply_profile

        config = DocpullConfig(url="https://example.com", profile=ProfileName.QUICK)
        config = apply_profile(config)
        # Quick profile should have low max_pages (50) and max_depth (2)
        assert config.crawl.max_pages == 50
        assert config.crawl.max_depth == 2


class TestCoreExports:
    """Tests for the docpull.core package surface."""

    def test_sync_helpers_are_exported(self):
        assert callable(core_fetch_one)
        assert callable(core_fetch_blocking)

    def test_explicit_user_value_beats_profile_value(self):
        """User-supplied values must win over profile values on collision.

        Mirror sets ``cache.enabled=True``. A user who explicitly
        disables the cache should keep that setting; previously the
        profile deep-update overwrote whatever the user passed, masking
        the explicit choice.
        """
        from docpull.models.profiles import apply_profile

        config = DocpullConfig(
            url="https://example.com",
            profile=ProfileName.MIRROR,
            cache={"enabled": False},
        )
        applied = apply_profile(config)
        assert applied.cache.enabled is False, (
            "user explicitly disabled cache; Mirror profile default should not have re-enabled it"
        )
        # Other Mirror profile values that the user did NOT touch should
        # still apply (skip_unchanged was set by the profile).
        assert applied.cache.skip_unchanged is True
        assert applied.crawl.max_depth == 10  # Mirror sets this

    def test_profile_value_beats_pydantic_default(self):
        """When the user doesn't set a field, the profile value wins
        over the Pydantic default."""
        from docpull.models.profiles import apply_profile

        config = DocpullConfig(url="https://example.com", profile=ProfileName.RAG)
        applied = apply_profile(config)
        # Pydantic default for streaming_dedup is False; RAG flips it to True.
        assert applied.content_filter.streaming_dedup is True


class TestImports:
    """Tests for package imports."""

    def test_public_api_imports(self):
        """Test that the public API can be imported."""
        from docpull import (
            DocpullConfig,
            Fetcher,
        )

        # Just verify imports work
        assert Fetcher is not None
        assert DocpullConfig is not None

    def test_config_model_imports(self):
        """Test that config models can be imported."""
        from docpull import (
            ContentFilterConfig,
            CrawlConfig,
        )

        # Verify imports work
        assert CrawlConfig is not None
        assert ContentFilterConfig is not None


class TestByteSize:
    """Tests for ByteSize type."""

    def test_parse_bytes(self):
        """Test parsing bytes."""
        from docpull.models.config import ByteSize

        assert ByteSize._parse(1024) == 1024
        assert ByteSize._parse("1024") == 1024

    def test_parse_kb(self):
        """Test parsing kilobytes."""
        from docpull.models.config import ByteSize

        assert ByteSize._parse("1kb") == 1024
        assert ByteSize._parse("200KB") == 200 * 1024

    def test_parse_mb(self):
        """Test parsing megabytes."""
        from docpull.models.config import ByteSize

        assert ByteSize._parse("1mb") == 1024 * 1024
        assert ByteSize._parse("50MB") == 50 * 1024 * 1024

    def test_parse_gb(self):
        """Test parsing gigabytes."""
        from docpull.models.config import ByteSize

        assert ByteSize._parse("1gb") == 1024 * 1024 * 1024

    def test_parse_invalid(self):
        """Test parsing invalid size."""
        from docpull.models.config import ByteSize

        with pytest.raises(ValueError):
            ByteSize._parse("invalid")
