"""Tests for v2 conversion module."""

from docpull.conversion import (
    FrontmatterBuilder,
    HtmlToMarkdown,
    MainContentExtractor,
)


class TestMainContentExtractor:
    """Tests for MainContentExtractor."""

    def test_extracts_from_article_tag(self):
        """Test extraction from article tag."""
        extractor = MainContentExtractor()
        html = b"""<html><body>
            <nav>Navigation</nav>
            <article>
                <h1>Title</h1>
                <p>Content here</p>
            </article>
            <footer>Footer</footer>
        </body></html>"""

        result = extractor.extract(html, "https://example.com/page")

        assert "Title" in result
        assert "Content here" in result
        # Nav and footer should be removed
        assert "Navigation" not in result or "<nav>" not in result

    def test_extracts_from_main_tag(self):
        """Test extraction from main tag."""
        extractor = MainContentExtractor()
        html = b"""<html><body>
            <header>Header</header>
            <main>
                <h1>Main Content</h1>
                <p>This is the main content.</p>
            </main>
        </body></html>"""

        result = extractor.extract(html, "https://example.com/page")

        assert "Main Content" in result

    def test_resolves_relative_links(self):
        """Test that relative links are resolved."""
        extractor = MainContentExtractor()
        html = b"""<html><body>
            <article>
                <a href="/other-page">Link</a>
            </article>
        </body></html>"""

        result = extractor.extract(html, "https://example.com/page")

        assert "https://example.com/other-page" in result

    def test_removes_scripts_and_styles(self):
        """Test that scripts and styles are removed."""
        extractor = MainContentExtractor()
        html = b"""<html><body>
            <article>
                <script>alert("bad")</script>
                <style>.bad { color: red; }</style>
                <p>Good content</p>
            </article>
        </body></html>"""

        result = extractor.extract(html, "https://example.com/page")

        assert "alert" not in result
        assert ".bad" not in result
        assert "Good content" in result

    def test_handles_encoding(self):
        """Test handling of different encodings."""
        extractor = MainContentExtractor()
        html = """<html><head><meta charset="utf-8"></head><body>
            <article>
                <p>Héllo Wörld</p>
            </article>
        </body></html>""".encode()

        result = extractor.extract(html, "https://example.com/page")

        assert "Héllo" in result
        assert "Wörld" in result


class TestHtmlToMarkdown:
    """Tests for HtmlToMarkdown converter."""

    def test_converts_headings(self):
        """Test heading conversion."""
        converter = HtmlToMarkdown()
        html = "<h1>Title</h1><h2>Subtitle</h2>"

        result = converter.convert(html, "https://example.com")

        assert "# Title" in result
        assert "## Subtitle" in result

    def test_converts_paragraphs(self):
        """Test paragraph conversion."""
        converter = HtmlToMarkdown()
        html = "<p>First paragraph.</p><p>Second paragraph.</p>"

        result = converter.convert(html, "https://example.com")

        assert "First paragraph." in result
        assert "Second paragraph." in result

    def test_converts_links(self):
        """Test link conversion."""
        converter = HtmlToMarkdown()
        html = '<a href="https://example.com/page">Link Text</a>'

        result = converter.convert(html, "https://example.com")

        assert "[Link Text]" in result
        # Link should be present in some form
        assert "example.com" in result

    def test_converts_lists(self):
        """Test list conversion."""
        converter = HtmlToMarkdown()
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"

        result = converter.convert(html, "https://example.com")

        assert "* Item 1" in result or "- Item 1" in result
        assert "* Item 2" in result or "- Item 2" in result

    def test_converts_code_blocks(self):
        """Test code block conversion."""
        converter = HtmlToMarkdown()
        html = "<pre><code>def hello():\n    print('Hello')</code></pre>"

        result = converter.convert(html, "https://example.com")

        assert "def hello():" in result
        assert "print" in result

    def test_converts_inline_code(self):
        """Test inline code conversion."""
        converter = HtmlToMarkdown()
        html = "<p>Use the <code>print()</code> function.</p>"

        result = converter.convert(html, "https://example.com")

        assert "`print()`" in result

    def test_converts_bold_and_italic(self):
        """Test bold and italic conversion."""
        converter = HtmlToMarkdown()
        html = "<p><strong>Bold</strong> and <em>italic</em> text.</p>"

        result = converter.convert(html, "https://example.com")

        assert "**Bold**" in result
        assert "*italic*" in result or "_italic_" in result

    def test_resolves_relative_links(self):
        """Test that relative links are made absolute."""
        converter = HtmlToMarkdown()
        html = '<a href="/docs/page">Docs</a>'

        result = converter.convert(html, "https://example.com/base")

        # Link should contain docs/page in some form
        assert "docs/page" in result

    def test_cleans_excessive_whitespace(self):
        """Test that excessive whitespace is cleaned."""
        converter = HtmlToMarkdown()
        html = "<p>Text</p>\n\n\n\n\n<p>More text</p>"

        result = converter.convert(html, "https://example.com")

        # Should not have more than 2 consecutive newlines
        assert "\n\n\n" not in result


class TestFrontmatterBuilder:
    """Tests for FrontmatterBuilder."""

    def test_builds_basic_frontmatter(self):
        """Test basic frontmatter generation."""
        builder = FrontmatterBuilder()
        result = builder.build(
            title="Test Page",
            url="https://example.com/page",
        )

        assert result.startswith("---\n")
        assert result.endswith("---\n\n")
        assert 'title: "Test Page"' in result
        assert "source: https://example.com/page" in result

    def test_escapes_quotes_in_title(self):
        """Test that quotes in title are escaped."""
        builder = FrontmatterBuilder()
        result = builder.build(title='Test "Quoted" Page')

        assert 'title: "Test \\"Quoted\\" Page"' in result

    def test_includes_description(self):
        """Test description in frontmatter."""
        builder = FrontmatterBuilder()
        result = builder.build(
            title="Test",
            description="This is a test page.",
        )

        assert 'description: "This is a test page."' in result

    def test_truncates_long_description(self):
        """Test that long descriptions are truncated."""
        builder = FrontmatterBuilder()
        long_desc = "A" * 1000
        result = builder.build(description=long_desc)

        # Should be truncated to 500 chars
        assert len(result) < 600

    def test_handles_extra_fields(self):
        """Test extra fields in frontmatter."""
        builder = FrontmatterBuilder()
        result = builder.build(
            title="Test",
            author="John Doe",
            date="2024-01-01",
        )

        assert 'author: "John Doe"' in result
        assert 'date: "2024-01-01"' in result

    def test_handles_list_fields(self):
        """Test list fields in frontmatter."""
        builder = FrontmatterBuilder()
        result = builder.build(
            title="Test",
            tags=["python", "testing", "docs"],
        )

        assert "tags:" in result
        assert "- python" in result
        assert "- testing" in result


class TestIntegration:
    """Integration tests for the conversion pipeline."""

    def test_full_conversion_pipeline(self):
        """Test complete extraction and conversion."""
        html = b"""<!DOCTYPE html>
        <html>
        <head>
            <title>Getting Started Guide</title>
            <meta property="og:description" content="Learn how to get started.">
        </head>
        <body>
            <nav><a href="/">Home</a></nav>
            <main>
                <h1>Getting Started</h1>
                <p>Welcome to our documentation.</p>
                <h2>Installation</h2>
                <pre><code>pip install mypackage</code></pre>
                <p>Then run:</p>
                <code>mypackage --help</code>
            </main>
            <footer>Copyright 2024</footer>
        </body>
        </html>"""

        # Extract
        extractor = MainContentExtractor()
        extracted = extractor.extract(html, "https://docs.example.com/getting-started")

        # Convert
        converter = HtmlToMarkdown()
        markdown = converter.convert(extracted, "https://docs.example.com/getting-started")

        # Verify
        assert "# Getting Started" in markdown
        assert "## Installation" in markdown
        assert "pip install mypackage" in markdown
        assert "`mypackage --help`" in markdown

        # Nav and footer should be removed
        assert "Copyright" not in markdown
