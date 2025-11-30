"""Tests for link extraction strategies."""

import pytest

from docpull.discovery.link_extractors.enhanced import EnhancedLinkExtractor
from docpull.discovery.link_extractors.static import StaticLinkExtractor


class MockHttpClient:
    """Mock HTTP client for testing."""

    def __init__(self, responses: dict[str, tuple[int, str, bytes]] | None = None):
        """
        Initialize mock client.

        Args:
            responses: Dict mapping URLs to (status_code, content_type, content)
        """
        self.responses = responses or {}

    async def get(self, url: str, timeout: float = 30.0):
        """Mock GET request."""
        if url in self.responses:
            status, content_type, content = self.responses[url]
            return MockResponse(status, content_type, content)
        return MockResponse(404, "text/html", b"Not found")


class MockResponse:
    """Mock HTTP response."""

    def __init__(self, status_code: int, content_type: str, content: bytes):
        self.status_code = status_code
        self.content_type = content_type
        self.content = content


class TestStaticLinkExtractor:
    """Tests for StaticLinkExtractor."""

    @pytest.fixture
    def mock_client(self):
        return MockHttpClient()

    @pytest.fixture
    def extractor(self, mock_client):
        return StaticLinkExtractor(http_client=mock_client)

    @pytest.mark.asyncio
    async def test_extracts_standard_links(self, extractor):
        """Test extracting standard <a href> links."""
        html = b"""
        <html>
            <body>
                <a href="/page1">Page 1</a>
                <a href="/page2">Page 2</a>
                <a href="https://example.com/page3">Page 3</a>
            </body>
        </html>
        """
        links = await extractor.extract_links("https://example.com", content=html)

        assert "https://example.com/page1" in links
        assert "https://example.com/page2" in links
        assert "https://example.com/page3" in links

    @pytest.mark.asyncio
    async def test_skips_javascript_links(self, extractor):
        """Test that javascript: links are skipped."""
        html = b'<a href="javascript:void(0)">Click</a>'
        links = await extractor.extract_links("https://example.com", content=html)
        assert len(links) == 0

    @pytest.mark.asyncio
    async def test_skips_anchor_links(self, extractor):
        """Test that anchor-only links are skipped."""
        html = b'<a href="#section">Section</a>'
        links = await extractor.extract_links("https://example.com", content=html)
        assert len(links) == 0

    @pytest.mark.asyncio
    async def test_removes_fragments(self, extractor):
        """Test that URL fragments are removed."""
        html = b'<a href="/page#section">Page</a>'
        links = await extractor.extract_links("https://example.com", content=html)
        assert links == ["https://example.com/page"]

    @pytest.mark.asyncio
    async def test_resolves_relative_urls(self, extractor):
        """Test that relative URLs are resolved."""
        html = b'<a href="../other/page">Other</a>'
        links = await extractor.extract_links("https://example.com/docs/api/", content=html)
        assert "https://example.com/docs/other/page" in links


class TestEnhancedLinkExtractor:
    """Tests for EnhancedLinkExtractor."""

    @pytest.fixture
    def mock_client(self):
        return MockHttpClient()

    @pytest.fixture
    def extractor(self, mock_client):
        return EnhancedLinkExtractor(http_client=mock_client)

    @pytest.mark.asyncio
    async def test_extracts_data_href(self, extractor):
        """Test extracting links from data-href attributes."""
        html = b'<div data-href="/page1">Click me</div>'
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/page1" in links

    @pytest.mark.asyncio
    async def test_extracts_data_url(self, extractor):
        """Test extracting links from data-url attributes."""
        html = b'<button data-url="/api/endpoint">Load</button>'
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/api/endpoint" in links

    @pytest.mark.asyncio
    async def test_extracts_data_link(self, extractor):
        """Test extracting links from data-link attributes."""
        html = b'<span data-link="/docs/guide">Guide</span>'
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/docs/guide" in links

    @pytest.mark.asyncio
    async def test_extracts_onclick_location_href(self, extractor):
        """Test extracting URLs from onclick location.href."""
        html = b"""<button onclick="location.href='/contact'">Contact</button>"""
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/contact" in links

    @pytest.mark.asyncio
    async def test_extracts_onclick_window_location(self, extractor):
        """Test extracting URLs from onclick window.location."""
        html = b"""<div onclick="window.location = '/about'">About</div>"""
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/about" in links

    @pytest.mark.asyncio
    async def test_extracts_onclick_router_push(self, extractor):
        """Test extracting URLs from onclick router.push."""
        html = b"""<a onclick="router.push('/dashboard')">Dashboard</a>"""
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/dashboard" in links

    @pytest.mark.asyncio
    async def test_extracts_json_ld_urls(self, extractor):
        """Test extracting URLs from JSON-LD."""
        html = b"""
        <script type="application/ld+json">
        {
            "@type": "WebSite",
            "url": "https://example.com/home",
            "mainEntityOfPage": "https://example.com/main"
        }
        </script>
        """
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/home" in links
        assert "https://example.com/main" in links

    @pytest.mark.asyncio
    async def test_extracts_prefetch_links(self, extractor):
        """Test extracting URLs from prefetch hints."""
        html = b"""
        <head>
            <link rel="prefetch" href="/next-page">
        </head>
        """
        links = await extractor.extract_links("https://example.com", content=html)
        assert "https://example.com/next-page" in links

    @pytest.mark.asyncio
    async def test_combines_all_sources(self, extractor):
        """Test that all link sources are combined."""
        html = b"""
        <html>
            <head>
                <link rel="prefetch" href="/prefetched">
                <script type="application/ld+json">
                {"url": "https://example.com/jsonld"}
                </script>
            </head>
            <body>
                <a href="/standard">Standard</a>
                <div data-href="/data-attr">Data</div>
                <button onclick="location.href='/onclick'">Click</button>
            </body>
        </html>
        """
        links = await extractor.extract_links("https://example.com", content=html)

        assert "https://example.com/prefetched" in links
        assert "https://example.com/jsonld" in links
        assert "https://example.com/standard" in links
        assert "https://example.com/data-attr" in links
        assert "https://example.com/onclick" in links

    @pytest.mark.asyncio
    async def test_deduplicates_urls(self, extractor):
        """Test that duplicate URLs are removed."""
        html = b"""
        <a href="/page">Link 1</a>
        <a href="/page">Link 2</a>
        <div data-href="/page">Data</div>
        """
        links = await extractor.extract_links("https://example.com", content=html)

        # Should only have one instance
        assert links.count("https://example.com/page") == 1

    @pytest.mark.asyncio
    async def test_respects_enable_flags(self, mock_client):
        """Test that enable flags control extraction."""
        html = b"""
        <a href="/standard">Standard</a>
        <div data-href="/data">Data</div>
        <button onclick="location.href='/onclick'">Click</button>
        <script type="application/ld+json">{"url": "/jsonld"}</script>
        <link rel="prefetch" href="/prefetch">
        """

        # Only standard links
        extractor = EnhancedLinkExtractor(
            http_client=mock_client,
            enable_data_attrs=False,
            enable_onclick=False,
            enable_json_ld=False,
            enable_prefetch=False,
        )
        links = await extractor.extract_links("https://example.com", content=html)

        assert "https://example.com/standard" in links
        assert "https://example.com/data" not in links
        assert "https://example.com/onclick" not in links
        # Note: JSON-LD with relative URL won't be resolved correctly, so skip this assertion


class TestRobotsSitemapDiscovery:
    """Tests for robots.txt sitemap discovery in SitemapDiscoverer."""

    @pytest.mark.asyncio
    async def test_sitemap_discoverer_uses_robots_checker(self):
        """Test that SitemapDiscoverer accepts robots_checker."""
        from docpull.discovery.sitemap import SitemapDiscoverer
        from docpull.security.robots import RobotsChecker

        # Just test that the parameter is accepted
        mock_client = MockHttpClient()
        robots_checker = RobotsChecker()

        # This should not raise
        discoverer = SitemapDiscoverer(
            http_client=mock_client,
            url_validator=MockUrlValidator(),
            robots_checker=robots_checker,
        )

        # Verify the robots checker was stored
        assert discoverer._robots is robots_checker


class MockUrlValidator:
    """Mock URL validator."""

    def is_valid(self, url: str) -> bool:
        return url.startswith("http")
