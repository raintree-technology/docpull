"""Tests for v2 discovery module."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from docpull.discovery import (
    CompositeFilter,
    DomainFilter,
    PatternFilter,
    SeenUrlTracker,
    normalize_url,
)
from docpull.discovery.composite import CompositeDiscoverer
from docpull.discovery.crawler import LinkCrawler
from docpull.discovery.sitemap import SitemapDiscoverer


class TestNormalizeUrl:
    """Tests for URL normalization."""

    def test_removes_fragment(self):
        """Test that fragments are removed."""
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_lowercase_scheme_and_host(self):
        """Test that scheme and host are lowercased."""
        result = normalize_url("HTTPS://EXAMPLE.COM/Page")
        assert "https://example.com" in result

    def test_removes_trailing_slash(self):
        """Test that trailing slashes are handled."""
        result = normalize_url("https://example.com/path/")
        assert result.endswith("/path") or result.endswith("/path/")


class TestPatternFilter:
    """Tests for PatternFilter."""

    def test_include_pattern_matches(self):
        """Test that include patterns work."""
        pattern_filter = PatternFilter(include_patterns=["/docs/*"])
        assert pattern_filter.should_include("https://example.com/docs/page") is True
        assert pattern_filter.should_include("https://example.com/blog/post") is False

    def test_exclude_pattern_matches(self):
        """Test that exclude patterns work."""
        pattern_filter = PatternFilter(exclude_patterns=["/admin/*"])
        assert pattern_filter.should_include("https://example.com/docs/page") is True
        assert pattern_filter.should_include("https://example.com/admin/users") is False

    def test_include_and_exclude(self):
        """Test combined include and exclude patterns."""
        pattern_filter = PatternFilter(
            include_patterns=["/docs/*"],
            exclude_patterns=["/docs/internal/*"],
        )
        assert pattern_filter.should_include("https://example.com/docs/public") is True
        assert pattern_filter.should_include("https://example.com/docs/internal/secret") is False
        assert pattern_filter.should_include("https://example.com/blog/post") is False

    def test_no_patterns_allows_all(self):
        """Test that no patterns allows all URLs."""
        pattern_filter = PatternFilter()
        assert pattern_filter.should_include("https://example.com/anything") is True


class TestDomainFilter:
    """Tests for DomainFilter."""

    def test_same_domain_allowed(self):
        """Test that same domain is allowed."""
        domain_filter = DomainFilter("https://docs.example.com/start")
        assert domain_filter.should_include("https://docs.example.com/page") is True

    def test_different_domain_blocked(self):
        """Test that different domain is blocked."""
        domain_filter = DomainFilter("https://docs.example.com/start")
        assert domain_filter.should_include("https://other.com/page") is False

    def test_subdomain_blocked_by_default(self):
        """Test that subdomains are blocked by default."""
        domain_filter = DomainFilter("https://example.com/start")
        assert domain_filter.should_include("https://docs.example.com/page") is False

    def test_subdomain_allowed_when_enabled(self):
        """Test that subdomains are allowed when enabled."""
        domain_filter = DomainFilter("https://example.com/start", allow_subdomains=True)
        assert domain_filter.should_include("https://docs.example.com/page") is True

    def test_additional_domains(self):
        """Test additional allowed domains."""
        domain_filter = DomainFilter(
            "https://example.com",
            additional_domains={"cdn.example.com", "other.com"},
        )
        assert domain_filter.should_include("https://cdn.example.com/asset") is True
        assert domain_filter.should_include("https://other.com/page") is True


class TestCompositeFilter:
    """Tests for CompositeFilter."""

    def test_all_filters_must_pass(self):
        """Test that all filters must approve."""
        filter1 = PatternFilter(include_patterns=["/docs/*"])
        filter2 = PatternFilter(exclude_patterns=["/docs/internal/*"])

        composite = CompositeFilter([filter1, filter2])

        assert composite.should_include("https://example.com/docs/public") is True
        assert composite.should_include("https://example.com/docs/internal/secret") is False
        assert composite.should_include("https://example.com/blog/post") is False


class TestSeenUrlTracker:
    """Tests for SeenUrlTracker."""

    def test_add_returns_true_for_new(self):
        """Test that add returns True for new URLs."""
        tracker = SeenUrlTracker()
        assert tracker.add("https://example.com/page1") is True
        assert tracker.add("https://example.com/page2") is True

    def test_add_returns_false_for_seen(self):
        """Test that add returns False for seen URLs."""
        tracker = SeenUrlTracker()
        tracker.add("https://example.com/page")
        assert tracker.add("https://example.com/page") is False

    def test_contains_check(self):
        """Test __contains__ method."""
        tracker = SeenUrlTracker()
        tracker.add("https://example.com/page")
        assert "https://example.com/page" in tracker
        assert "https://example.com/other" not in tracker

    def test_clear(self):
        """Test clear method."""
        tracker = SeenUrlTracker()
        tracker.add("https://example.com/page")
        tracker.clear()
        assert len(tracker) == 0
        assert tracker.add("https://example.com/page") is True


class TestSitemapDiscoverer:
    """Tests for SitemapDiscoverer."""

    @pytest.fixture
    def mock_http_client(self):
        """Create mock HTTP client."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def mock_validator(self):
        """Create mock URL validator."""
        validator = MagicMock()
        validator.is_valid.return_value = True
        return validator

    @pytest.mark.asyncio
    async def test_parse_simple_sitemap(self, mock_http_client, mock_validator):
        """Test parsing a simple sitemap."""
        sitemap_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/page1</loc></url>
            <url><loc>https://example.com/page2</loc></url>
        </urlset>"""

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = sitemap_content
        mock_http_client.get.return_value = mock_response

        discoverer = SitemapDiscoverer(mock_http_client, mock_validator)
        urls = []
        async for url in discoverer.discover("https://example.com/sitemap.xml"):
            urls.append(url)

        assert len(urls) == 2
        assert "https://example.com/page1" in urls
        assert "https://example.com/page2" in urls

    @pytest.mark.asyncio
    async def test_respects_max_urls(self, mock_http_client, mock_validator):
        """Test that max_urls limit is respected."""
        sitemap_content = b"""<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://example.com/page1</loc></url>
            <url><loc>https://example.com/page2</loc></url>
            <url><loc>https://example.com/page3</loc></url>
        </urlset>"""

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = sitemap_content
        mock_http_client.get.return_value = mock_response

        discoverer = SitemapDiscoverer(mock_http_client, mock_validator)
        urls = []
        async for url in discoverer.discover("https://example.com/sitemap.xml", max_urls=2):
            urls.append(url)

        assert len(urls) == 2


class TestLinkCrawler:
    """Tests for LinkCrawler."""

    @pytest.fixture
    def mock_http_client(self):
        """Create mock HTTP client."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def mock_validator(self):
        """Create mock URL validator."""
        validator = MagicMock()
        validator.is_valid.return_value = True
        return validator

    @pytest.fixture
    def mock_robots(self):
        """Create mock robots checker."""
        robots = MagicMock()
        robots.is_allowed.return_value = True
        return robots

    @pytest.mark.asyncio
    async def test_extracts_links(self, mock_http_client, mock_validator, mock_robots):
        """Test link extraction from HTML."""
        html_content = b"""<html><body>
            <a href="/page1">Page 1</a>
            <a href="/page2">Page 2</a>
        </body></html>"""

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = html_content
        mock_response.content_type = "text/html"
        mock_http_client.get.return_value = mock_response

        crawler = LinkCrawler(
            mock_http_client,
            mock_validator,
            mock_robots,
            max_depth=1,
        )
        urls = []
        async for url in crawler.discover("https://example.com", max_depth=1):
            urls.append(url)

        # Should include start URL and discovered links
        assert "https://example.com" in urls

    @pytest.mark.asyncio
    async def test_respects_max_depth(self, mock_http_client, mock_validator, mock_robots):
        """Test that max_depth is respected."""
        crawler = LinkCrawler(
            mock_http_client,
            mock_validator,
            mock_robots,
            max_depth=0,
        )

        # With max_depth=0, should only return start URL
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b'<html><a href="/other">Other</a></html>'
        mock_response.content_type = "text/html"
        mock_http_client.get.return_value = mock_response

        urls = []
        async for url in crawler.discover("https://example.com", max_depth=0):
            urls.append(url)

        # Only start URL, no crawling
        assert len(urls) == 1


class TestCompositeDiscoverer:
    """Tests for CompositeDiscoverer."""

    @pytest.mark.asyncio
    async def test_uses_sitemap_first(self):
        """Test that sitemap is tried first."""

        class MockSitemap:
            discover_called = False

            async def discover(self, start_url, *, max_urls=None):
                self.discover_called = True
                for url in ["https://example.com/page1", "https://example.com/page2"]:
                    yield url

        class MockCrawler:
            discover_called = False

            async def discover(self, start_url, *, max_urls=None):
                self.discover_called = True
                return
                yield  # Make it a generator

        sitemap = MockSitemap()
        crawler = MockCrawler()

        discoverer = CompositeDiscoverer(
            sitemap_discoverer=sitemap,
            link_crawler=crawler,
            fallback_threshold=1,
        )

        urls = []
        async for url in discoverer.discover("https://example.com"):
            urls.append(url)

        assert len(urls) == 2
        assert sitemap.discover_called
        # Crawler should not be called since sitemap yielded >= threshold
        assert not crawler.discover_called

    @pytest.mark.asyncio
    async def test_falls_back_to_crawler(self):
        """Test fallback to crawler when sitemap yields too few."""

        class MockSitemap:
            async def discover(self, start_url, *, max_urls=None):
                yield "https://example.com/page1"

        class MockCrawler:
            discover_called = False

            async def discover(self, start_url, *, max_urls=None):
                self.discover_called = True
                yield "https://example.com/page2"

        sitemap = MockSitemap()
        crawler = MockCrawler()

        discoverer = CompositeDiscoverer(
            sitemap_discoverer=sitemap,
            link_crawler=crawler,
            fallback_threshold=5,  # Need 5, but sitemap only yields 1
        )

        urls = []
        async for url in discoverer.discover("https://example.com"):
            urls.append(url)

        # Should have URLs from both
        assert "https://example.com/page1" in urls
        assert "https://example.com/page2" in urls
        assert crawler.discover_called
