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


class TestFenceLanguageNormalization:
    """The conversion pipeline must emit GFM fenced code blocks with the
    language tag preserved from the source HTML's syntax-highlight class."""

    def _convert(self, body_html: str) -> str:
        html = (
            b"<html><body><article>"
            + body_html.encode()
            + b"</article></body></html>"
        )
        extracted = MainContentExtractor().extract(html, "https://x.test/")
        return HtmlToMarkdown().convert(extracted, "https://x.test/")

    def test_prism_language_class_emits_fenced_block(self):
        md = self._convert(
            '<pre class="language-python"><code class="language-python">print("hi")</code></pre>'
        )
        assert "```python\nprint(\"hi\")\n```" in md
        assert "[code]" not in md

    def test_legacy_lang_class_emits_fenced_block(self):
        md = self._convert(
            '<pre><code class="lang-bash">echo ok</code></pre>'
        )
        assert "```bash\necho ok\n```" in md

    def test_github_highlight_source_class_emits_fenced_block(self):
        md = self._convert(
            '<pre class="highlight-source-rust"><code>fn main() {}</code></pre>'
        )
        assert "```rust\nfn main() {}\n```" in md

    def test_unknown_language_emits_bare_fence(self):
        md = self._convert("<pre><code>just plain code</code></pre>")
        assert "```\njust plain code\n```" in md
        assert "[code]" not in md

    def test_plaintext_class_does_not_set_language(self):
        md = self._convert(
            '<pre><code class="lang-plaintext">no lang here</code></pre>'
        )
        # 'plaintext' / 'text' / 'none' shouldn't end up as the fence label
        assert "```\nno lang here\n```" in md
        assert "```plaintext" not in md

    def test_multiline_block_preserves_indentation(self):
        md = self._convert(
            '<pre class="language-python"><code>'
            "def f():\n    return 1\n"
            "</code></pre>"
        )
        assert "```python" in md
        assert "def f():" in md
        # 4-space body indentation html2text adds must be stripped
        lines = md.splitlines()
        opening = next(i for i, line in enumerate(lines) if line == "```python")
        # Line right after the fence should not be 8-space indented
        assert not lines[opening + 1].startswith("    ")


# Cookie / consent banner copy that must NOT leak into the body Markdown.
# Sourced from the public DOM of the vendor SDKs we strip.
_BANNER_COPY = "By clicking Accept All Cookies you agree to the storing of cookies"

# Real-world DOM patterns lifted from public docs sites. Each fixture
# wraps the banner in the structural element a vendor SDK injects, plus
# legitimate article content. After extraction + Markdown conversion, the
# banner copy must be gone but the article content must survive.
_COOKIE_FIXTURES = [
    (
        "onetrust",
        f'''
        <html><body>
        <div id="onetrust-consent-sdk">
          <div id="onetrust-banner-sdk">
            <div class="ot-sdk-row">{_BANNER_COPY}</div>
            <button>Accept All Cookies</button>
          </div>
        </div>
        <article><h1>Real Title</h1><p>Real article body here.</p></article>
        </body></html>
        ''',
    ),
    (
        "osano",
        f'''
        <html><body>
        <div class="osano-cm-window">
          <div class="osano-cm-dialog">{_BANNER_COPY}</div>
        </div>
        <article><h1>Real Title</h1><p>Real article body here.</p></article>
        </body></html>
        ''',
    ),
    (
        "cookieconsent",
        f'''
        <html><body>
        <div class="cc-window">
          <div class="cc-banner">{_BANNER_COPY}</div>
        </div>
        <article><h1>Real Title</h1><p>Real article body here.</p></article>
        </body></html>
        ''',
    ),
    (
        "cookielaw",
        f'''
        <html><body>
        <div class="cookielaw-banner">{_BANNER_COPY}</div>
        <article><h1>Real Title</h1><p>Real article body here.</p></article>
        </body></html>
        ''',
    ),
    (
        "generic-aria",
        f'''
        <html><body>
        <div role="dialog" aria-label="Cookie Consent">{_BANNER_COPY}</div>
        <article><h1>Real Title</h1><p>Real article body here.</p></article>
        </body></html>
        ''',
    ),
]


class TestCookieBannerStripping:
    """Vendor cookie/consent banner copy must not leak into Markdown body.

    The FAQ at web/components/FAQ.tsx claims that 'common cookie/consent
    banners' are stripped before conversion. These tests are the proof.
    """

    def _convert(self, html: str) -> str:
        extracted = MainContentExtractor().extract(html.encode(), "https://x.test/")
        return HtmlToMarkdown().convert(extracted, "https://x.test/")

    def test_banner_copy_does_not_leak(self):
        for name, html in _COOKIE_FIXTURES:
            md = self._convert(html)
            assert _BANNER_COPY not in md, f"banner copy leaked through {name} fixture"
            assert "Accept All Cookies" not in md, f"CTA leaked through {name} fixture"

    def test_real_article_content_survives(self):
        for name, html in _COOKIE_FIXTURES:
            md = self._convert(html)
            assert "Real article body here." in md, (
                f"genuine article content lost in {name} fixture"
            )

    def test_legitimate_cookie_documentation_is_preserved(self):
        # A page whose body legitimately discusses cookies must NOT be
        # stripped. Selectors are structural, not text-based — only
        # vendor-shaped wrappers should match.
        html = '''
        <html><body><article>
        <h1>How cookies work</h1>
        <p>This page explains how the Set-Cookie header behaves.</p>
        <pre><code>Set-Cookie: session=abc; HttpOnly</code></pre>
        </article></body></html>
        '''
        md = self._convert(html)
        assert "How cookies work" in md
        assert "Set-Cookie: session=abc; HttpOnly" in md


class TestFrontmatterEnrichment:
    """ConvertStep should surface description, heading outline, and a
    crawled_at timestamp into YAML frontmatter so RAG / skill loaders
    don't have to re-parse the body."""

    def _convert_step_run(self, html: bytes, with_rich: bool = False) -> str:
        import asyncio
        from pathlib import Path

        from docpull.pipeline.base import PageContext
        from docpull.pipeline.steps.convert import ConvertStep
        from docpull.pipeline.steps.metadata import MetadataStep

        async def run() -> str:
            ctx = PageContext(url="https://x.test/page", output_path=Path("/tmp/x.md"))
            ctx.html = html
            await MetadataStep(extract_rich=with_rich).execute(ctx)
            await ConvertStep(add_frontmatter=True).execute(ctx)
            return ctx.markdown or ""

        return asyncio.run(run())

    def test_description_makes_it_into_frontmatter(self):
        html = (
            b'<!doctype html><html><head>'
            b'<title>T</title>'
            b'<meta name="description" content="A real description.">'
            b'</head><body><article><h1>Hi</h1><p>Body.</p></article></body></html>'
        )
        md = self._convert_step_run(html)
        assert 'description: "A real description."' in md

    def test_headings_outline_is_emitted(self):
        html = (
            b"<!doctype html><html><body><article>"
            b"<h1>Top</h1><h2>Section A</h2><h2>Section B</h2>"
            b"<h3>Skipped</h3>"
            b"<p>body</p></article></body></html>"
        )
        md = self._convert_step_run(html)
        assert "headings:" in md
        assert "- Top" in md
        assert "- Section A" in md
        assert "- Section B" in md
        # h3+ should NOT appear by default (outline depth = 2)
        assert "- Skipped" not in md

    def test_headings_skip_inside_code_fences(self):
        html = (
            b'<!doctype html><html><body><article>'
            b'<h1>Real</h1>'
            b'<pre class="language-markdown"><code># Not a real heading</code></pre>'
            b'</article></body></html>'
        )
        md = self._convert_step_run(html)
        # The fenced code block should not contribute a "Not a real heading"
        # entry to the headings outline.
        # Frontmatter heading list lives between '---' delimiters at the top.
        frontmatter_end = md.find("\n---", 4)
        frontmatter = md[:frontmatter_end]
        assert "Not a real heading" not in frontmatter

    def test_crawled_at_is_iso8601_utc(self):
        import re

        html = b"<!doctype html><html><body><article><h1>x</h1></article></body></html>"
        md = self._convert_step_run(html)
        match = re.search(r'crawled_at: "(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"', md)
        assert match, f"no crawled_at timestamp in:\n{md[:300]}"
