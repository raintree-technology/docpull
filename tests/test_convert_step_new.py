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
