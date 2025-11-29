"""Integration tests for docpull v2 API."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from docpull import (
    DocpullConfig,
    EventType,
    Fetcher,
    FetchEvent,
    FetchStats,
    ProfileName,
)
from docpull.core.fetcher import _url_to_filename


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
            message="Fetching 5/10",
        )
        assert event.type == EventType.FETCH_PROGRESS
        assert event.url == "https://example.com/page"
        assert event.current == 5
        assert event.total == 10

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


class TestImports:
    """Tests for package imports."""

    def test_v2_api_imports(self):
        """Test that v2 API can be imported."""
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
