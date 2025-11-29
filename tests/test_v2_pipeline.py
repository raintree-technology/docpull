"""Tests for v2 pipeline steps."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from docpull.cache import StreamingDeduplicator
from docpull.pipeline.base import FetchPipeline, PageContext
from docpull.pipeline.steps import (
    ConvertStep,
    DedupStep,
    FetchStep,
    MetadataStep,
    SaveStep,
    ValidateStep,
)


class TestPageContext:
    """Tests for PageContext dataclass."""

    def test_create_context(self):
        """Test creating a page context."""
        ctx = PageContext(url="https://example.com/page", output_path=Path("/tmp/test.md"))
        assert ctx.url == "https://example.com/page"
        assert ctx.html is None
        assert ctx.markdown is None
        assert ctx.should_skip is False
        assert ctx.error is None

    def test_context_with_output_path(self):
        """Test context with output path."""
        ctx = PageContext(
            url="https://example.com/page",
            output_path=Path("/tmp/output.md"),
        )
        assert ctx.output_path == Path("/tmp/output.md")


class TestValidateStep:
    """Tests for ValidateStep."""

    @pytest.fixture
    def mock_validator(self):
        """Create mock URL validator."""
        validator = MagicMock()
        # validate() returns an object with is_valid attribute
        valid_result = MagicMock()
        valid_result.is_valid = True
        validator.validate.return_value = valid_result
        return validator

    @pytest.fixture
    def mock_robots(self):
        """Create mock robots checker."""
        robots = MagicMock()
        robots.is_allowed.return_value = True
        return robots

    @pytest.mark.asyncio
    async def test_valid_url_passes(self, mock_validator, mock_robots):
        """Test that valid URLs pass validation."""
        step = ValidateStep(
            url_validator=mock_validator,
            robots_checker=mock_robots,
            check_existing=False,
        )
        ctx = PageContext(url="https://example.com/page", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        assert result.should_skip is False
        assert result.error is None
        mock_validator.validate.assert_called_with("https://example.com/page")

    @pytest.mark.asyncio
    async def test_invalid_url_skipped(self, mock_validator, mock_robots):
        """Test that invalid URLs are skipped."""
        invalid_result = MagicMock()
        invalid_result.is_valid = False
        invalid_result.rejection_reason = "Invalid scheme"
        mock_validator.validate.return_value = invalid_result

        step = ValidateStep(
            url_validator=mock_validator,
            robots_checker=mock_robots,
            check_existing=False,
        )
        ctx = PageContext(url="file:///etc/passwd", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        assert result.should_skip is True
        assert result.skip_reason is not None

    @pytest.mark.asyncio
    async def test_robots_blocked_skipped(self, mock_validator, mock_robots):
        """Test that robots-blocked URLs are skipped."""
        valid_result = MagicMock()
        valid_result.is_valid = True
        mock_validator.validate.return_value = valid_result
        mock_robots.is_allowed.return_value = False

        step = ValidateStep(
            url_validator=mock_validator,
            robots_checker=mock_robots,
            check_existing=False,
        )
        ctx = PageContext(url="https://example.com/private", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        assert result.should_skip is True
        assert "robots.txt" in result.skip_reason


class TestFetchStep:
    """Tests for FetchStep."""

    @pytest.fixture
    def mock_http_client(self):
        """Create mock HTTP client."""
        client = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_successful_fetch(self, mock_http_client):
        """Test successful page fetch."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"<html><body>Hello</body></html>"
        mock_response.content_type = "text/html"
        mock_http_client.get.return_value = mock_response

        step = FetchStep(http_client=mock_http_client)
        ctx = PageContext(url="https://example.com/page", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        assert result.html == b"<html><body>Hello</body></html>"
        assert result.status_code == 200
        assert result.error is None

    @pytest.mark.asyncio
    async def test_404_error(self, mock_http_client):
        """Test handling of 404 errors."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.content = b"Not Found"
        mock_response.content_type = "text/html"
        mock_http_client.get.return_value = mock_response

        step = FetchStep(http_client=mock_http_client)
        ctx = PageContext(url="https://example.com/missing", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        # 404 sets should_skip, not error
        assert result.should_skip is True
        assert result.status_code == 404

    @pytest.mark.asyncio
    async def test_non_html_skipped(self, mock_http_client):
        """Test that non-HTML content is skipped."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = b"binary data"
        mock_response.content_type = "application/pdf"
        mock_http_client.get.return_value = mock_response

        step = FetchStep(http_client=mock_http_client, validate_content_type=True)
        ctx = PageContext(url="https://example.com/file.pdf", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        assert result.should_skip is True


class TestConvertStep:
    """Tests for ConvertStep."""

    @pytest.mark.asyncio
    async def test_html_to_markdown_conversion(self):
        """Test HTML to Markdown conversion."""
        step = ConvertStep(add_frontmatter=False)
        ctx = PageContext(
            url="https://example.com/page",
            output_path=Path("/tmp/out.md"),
            html=b"<html><body><h1>Title</h1><p>Content</p></body></html>",
        )
        result = await step.execute(ctx)

        assert result.markdown is not None
        assert "Title" in result.markdown
        assert "Content" in result.markdown

    @pytest.mark.asyncio
    async def test_frontmatter_added(self):
        """Test that frontmatter is added when enabled."""
        step = ConvertStep(add_frontmatter=True)
        ctx = PageContext(
            url="https://example.com/page",
            output_path=Path("/tmp/out.md"),
            html=b"<html><head><title>Test Page</title></head><body><p>Content</p></body></html>",
        )
        ctx.title = "Test Page"
        result = await step.execute(ctx)

        assert result.markdown is not None
        assert "---" in result.markdown
        assert "source:" in result.markdown

    @pytest.mark.asyncio
    async def test_no_html_skipped(self):
        """Test that missing HTML is handled."""
        step = ConvertStep()
        ctx = PageContext(url="https://example.com/page", output_path=Path("/tmp/out.md"))
        result = await step.execute(ctx)

        assert result.error is not None


class TestMetadataStep:
    """Tests for MetadataStep."""

    @pytest.mark.asyncio
    async def test_title_extraction(self):
        """Test title extraction from HTML."""
        step = MetadataStep()
        ctx = PageContext(
            url="https://example.com/page",
            output_path=Path("/tmp/out.md"),
            html=b"<html><head><title>My Page Title</title></head><body></body></html>",
        )
        result = await step.execute(ctx)

        assert result.title == "My Page Title"

    @pytest.mark.asyncio
    async def test_og_title_preferred(self):
        """Test that Open Graph title is preferred."""
        step = MetadataStep()
        ctx = PageContext(
            url="https://example.com/page",
            output_path=Path("/tmp/out.md"),
            html=b"""<html><head>
                <title>Regular Title</title>
                <meta property="og:title" content="OG Title">
            </head><body></body></html>""",
        )
        result = await step.execute(ctx)

        assert result.title == "OG Title"


class TestDedupStep:
    """Tests for DedupStep."""

    @pytest.fixture
    def deduplicator(self):
        """Create streaming deduplicator."""
        return StreamingDeduplicator()

    @pytest.mark.asyncio
    async def test_unique_content_passes(self, deduplicator):
        """Test that unique content is not skipped."""
        step = DedupStep(deduplicator=deduplicator)
        ctx = PageContext(
            url="https://example.com/page1",
            output_path=Path("/tmp/out.md"),
            markdown="# Unique Content\n\nThis is unique.",
        )
        result = await step.execute(ctx)

        assert result.should_skip is False
        stats = deduplicator.get_stats()
        assert stats["unique_pages"] == 1

    @pytest.mark.asyncio
    async def test_duplicate_content_skipped(self, deduplicator):
        """Test that duplicate content is skipped."""
        step = DedupStep(deduplicator=deduplicator)

        # First page
        ctx1 = PageContext(
            url="https://example.com/page1",
            output_path=Path("/tmp/out1.md"),
            markdown="# Same Content\n\nThis is the same.",
        )
        await step.execute(ctx1)

        # Second page with same content
        ctx2 = PageContext(
            url="https://example.com/page2",
            output_path=Path("/tmp/out2.md"),
            markdown="# Same Content\n\nThis is the same.",
        )
        result = await step.execute(ctx2)

        assert result.should_skip is True
        assert "Duplicate" in result.skip_reason


class TestSaveStep:
    """Tests for SaveStep."""

    @pytest.mark.asyncio
    async def test_save_markdown(self, tmp_path):
        """Test saving markdown content."""
        step = SaveStep(base_output_dir=tmp_path)
        output_file = tmp_path / "test.md"
        ctx = PageContext(
            url="https://example.com/page",
            output_path=output_file,
            markdown="# Test\n\nContent here.",
        )
        result = await step.execute(ctx)

        assert output_file.exists()
        assert output_file.read_text() == "# Test\n\nContent here."
        assert result.error is None


class TestFetchPipeline:
    """Tests for FetchPipeline."""

    @pytest.mark.asyncio
    async def test_pipeline_executes_steps_in_order(self):
        """Test that pipeline executes steps in order."""
        execution_order = []

        class Step1:
            name = "step1"

            async def execute(self, ctx, emit=None):
                execution_order.append("step1")
                return ctx

        class Step2:
            name = "step2"

            async def execute(self, ctx, emit=None):
                execution_order.append("step2")
                return ctx

        pipeline = FetchPipeline(steps=[Step1(), Step2()])
        await pipeline.execute("https://example.com", Path("/tmp/out.md"))

        assert execution_order == ["step1", "step2"]

    @pytest.mark.asyncio
    async def test_pipeline_stops_on_skip(self):
        """Test that pipeline stops when should_skip is True."""
        execution_order = []

        class SkipStep:
            name = "skip"

            async def execute(self, ctx, emit=None):
                execution_order.append("skip")
                ctx.should_skip = True
                return ctx

        class NeverReached:
            name = "never"

            async def execute(self, ctx, emit=None):
                execution_order.append("never")
                return ctx

        pipeline = FetchPipeline(steps=[SkipStep(), NeverReached()])
        await pipeline.execute("https://example.com", Path("/tmp/out.md"))

        assert execution_order == ["skip"]
        assert "never" not in execution_order
