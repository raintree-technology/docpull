"""Tests for ConvertStep special-case and SPA handling (v2.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.convert import ConvertStep


def _page_context(url: str, html: bytes) -> PageContext:
    return PageContext(url=url, output_path=Path("/tmp/out.md"), html=html)


@pytest.mark.asyncio
async def test_next_data_takes_precedence_over_generic():
    payload = {"props": {"pageProps": {"title": "Page", "source": "body text " * 50}}}
    html = (
        b'<html><body><script id="__NEXT_DATA__">' + json.dumps(payload).encode() + b"</script></body></html>"
    )
    step = ConvertStep(add_frontmatter=False)
    ctx = await step.execute(_page_context("https://example.com/", html))
    assert ctx.source_type == "next_data"
    assert ctx.markdown is not None
    assert "body text" in ctx.markdown


@pytest.mark.asyncio
async def test_spa_detected_and_skipped():
    html = b'<html><body><div id="root"></div><script>' + b"x" * 5000 + b"</script></body></html>"
    step = ConvertStep(add_frontmatter=False)
    ctx = await step.execute(_page_context("https://example.com/", html))
    assert ctx.should_skip is True
    assert ctx.skip_reason is not None
    assert "SPA" in ctx.skip_reason or "JS" in ctx.skip_reason


@pytest.mark.asyncio
async def test_strict_js_required_raises_error():
    html = b'<html><body><div id="root"></div><script>' + b"x" * 5000 + b"</script></body></html>"
    step = ConvertStep(add_frontmatter=False, strict_js_required=True)
    ctx = await step.execute(_page_context("https://example.com/", html))
    assert ctx.error is not None
    assert "SPA" in ctx.error or "JavaScript" in ctx.error


@pytest.mark.asyncio
async def test_special_cases_can_be_disabled():
    payload = {"props": {"pageProps": {"title": "Page", "source": "body text " * 50}}}
    html = (
        b"<html><body><p>generic paragraph</p>"
        b'<script id="__NEXT_DATA__">' + json.dumps(payload).encode() + b"</script>"
        b"</body></html>"
    )
    step = ConvertStep(add_frontmatter=False, enable_special_cases=False)
    ctx = await step.execute(_page_context("https://example.com/", html))
    assert ctx.source_type != "next_data"
    assert ctx.markdown is not None


@pytest.mark.asyncio
async def test_frontmatter_includes_source_type():
    payload = {"props": {"pageProps": {"title": "Page", "source": "body text " * 50}}}
    html = (
        b'<html><body><script id="__NEXT_DATA__">' + json.dumps(payload).encode() + b"</script></body></html>"
    )
    step = ConvertStep(add_frontmatter=True)
    ctx = await step.execute(_page_context("https://example.com/", html))
    assert ctx.markdown is not None
    assert ctx.markdown.startswith("---")
    assert "source_type" in ctx.markdown


@pytest.mark.asyncio
async def test_llms_txt_converts_without_html_wrapper():
    body = b"# Docs\n\n- [Search](https://docs.example.com/search.md): Search API docs.\n"
    step = ConvertStep(add_frontmatter=False)
    ctx = await step.execute(_page_context("https://docs.example.com/llms.txt", body))

    assert ctx.source_type == "llms_txt"
    assert ctx.markdown is not None
    assert ctx.markdown.startswith("# Docs")


@pytest.mark.asyncio
async def test_content_type_routes_extensionless_plain_text_without_html_extraction():
    body = b"Request for Comments\n\nInteroperability Considerations\n\nParser behavior.\n"
    step = ConvertStep(add_frontmatter=False)
    ctx = _page_context("https://example.com/source", body)
    ctx.content_type = "text/plain; charset=utf-8"

    ctx = await step.execute(ctx)

    assert ctx.source_type == "raw_text"
    assert ctx.markdown is not None
    assert "Interoperability Considerations" in ctx.markdown


@pytest.mark.asyncio
async def test_remote_pdf_is_locally_parsed_only_when_explicitly_enabled(monkeypatch):
    from docpull.document_parse import ParsedDocument

    calls = []

    async def fake_parse(
        body,
        *,
        source_url,
        content_type,
        backend,
        timeout_seconds,
        memory_mib,
    ):
        calls.append((body, source_url, content_type, backend, timeout_seconds, memory_mib))
        return ParsedDocument(
            path=Path("remote.pdf"),
            source_url=source_url,
            title="Controlled Paper",
            content="# Controlled Paper\n\nTransformer and machine translation.",
            backend="markitdown",
            source_mime_type="application/pdf",
            metadata={"source_sha256": "a" * 64, "remote_source_retained": False},
        )

    monkeypatch.setattr("docpull.document_parse.parse_remote_document_bytes_async", fake_parse)
    ctx = _page_context("https://example.com/paper.pdf", b"%PDF-1.7\nfixture")
    ctx.content_type = "application/pdf"

    ctx = await ConvertStep(
        add_frontmatter=False,
        remote_documents="pdf",
        remote_document_backend="markitdown",
    ).execute(ctx)

    assert ctx.error is None
    assert ctx.source_type == "remote_pdf"
    assert ctx.title == "Controlled Paper"
    assert ctx.markdown is not None and "Transformer" in ctx.markdown
    assert ctx.extraction_info["parser"] == "markitdown"
    assert calls and calls[0][2:] == ("application/pdf", "markitdown", 60, 1024)


@pytest.mark.asyncio
async def test_remote_pdf_signature_mismatch_fails_closed(monkeypatch):
    from docpull.document_parse import DocumentParseError

    async def fail_parse(*args, **kwargs):
        raise DocumentParseError("Remote PDF response did not contain a PDF signature.")

    monkeypatch.setattr("docpull.document_parse.parse_remote_document_bytes_async", fail_parse)
    ctx = _page_context("https://example.com/paper", b"not a pdf")
    ctx.content_type = "application/pdf"

    ctx = await ConvertStep(add_frontmatter=False, remote_documents="pdf").execute(ctx)

    assert ctx.error is not None
    assert "PDF signature" in ctx.error


@pytest.mark.asyncio
async def test_raw_markdown_frontmatter_is_not_duplicated():
    body = b"---\ntitle: Original\n---\n\n# Body\n\n- item\n"
    step = ConvertStep(add_frontmatter=True)
    ctx = await step.execute(_page_context("https://docs.example.com/page.md", body))

    assert ctx.markdown is not None
    generated_frontmatter, body_markdown = ctx.markdown.split("---", 2)[1:]
    assert 'title: "Original"' in generated_frontmatter
    assert 'title: "---"' not in generated_frontmatter
    assert body_markdown.lstrip().startswith("# Body")
    assert "\ntitle: Original\n" not in body_markdown


@pytest.mark.asyncio
async def test_article_cleanup_removes_caption_labels_and_related_sections():
    html = b"""
    <html><body><article>
      <h1>Rescue story</h1>
      <p><strong>A survivor was rescued after eight days.</strong></p>
      <p>Watch: rescue teams reach the survivor</p>
      <p>ByJane ReporterBBC News</p>
      <p>By Jane Reporter</p>
      <p>BBC News</p>
      <p>Published</p>
      <p>Published 3 hours ago</p>
      <p>2 July 2026, 12:31 BST</p>
      <p>This video can not be played</p>
      <p>Figure caption,</p>
      <p><img src="/photo.jpg" alt="Rescue team">Image source, Example Agency</p>
      <p>Image caption,</p>
      <p>Short orphan caption</p>
      <p>The main article continues with useful reported detail.</p>
      <p>More on this story</p>
      <ul><li><a href="/noise">Unrelated link</a></li></ul>
    </article></body></html>
    """
    ctx = _page_context("https://www.bbc.co.uk/news/articles/example", html)
    ctx.metadata["published_time"] = "2026-07-02T12:00:00Z"
    step = ConvertStep(add_frontmatter=False)

    result = await step.execute(ctx)

    assert result.markdown is not None
    assert "A survivor was rescued" in result.markdown
    assert "The main article continues" in result.markdown
    assert "Figure caption" not in result.markdown
    assert "Watch:" not in result.markdown
    assert "ByJane" not in result.markdown
    assert "By Jane Reporter" not in result.markdown
    assert "BBC News" not in result.markdown
    assert "Published" not in result.markdown
    assert "12:31 BST" not in result.markdown
    assert "This video can not be played" not in result.markdown
    assert "Image source" not in result.markdown
    assert "Image caption" not in result.markdown
    assert "Short orphan caption" not in result.markdown
    assert "More on this story" not in result.markdown
    assert "Unrelated link" not in result.markdown
