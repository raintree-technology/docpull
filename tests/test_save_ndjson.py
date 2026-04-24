"""Tests for NdjsonSaveStep and ChunkStep integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpull.conversion.chunking import Chunk
from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.chunk import ChunkStep
from docpull.pipeline.steps.save_ndjson import NdjsonSaveStep


@pytest.mark.asyncio
async def test_ndjson_writes_one_line_per_page(tmp_path):
    step = NdjsonSaveStep(base_output_dir=tmp_path, filename="out.ndjson")
    for i in range(3):
        ctx = PageContext(
            url=f"https://example.com/p{i}",
            output_path=tmp_path / f"p{i}.md",
            markdown=f"# Page {i}\n\nBody.",
            title=f"Page {i}",
        )
        await step.execute(ctx)
    out_path = step.finalize()

    assert out_path is not None
    lines = out_path.read_text().strip().split("\n")
    assert len(lines) == 3
    records = [json.loads(line) for line in lines]
    assert [r["url"] for r in records] == [f"https://example.com/p{i}" for i in range(3)]
    assert all("hash" in r for r in records)


@pytest.mark.asyncio
async def test_ndjson_emits_chunks_when_enabled(tmp_path):
    step = NdjsonSaveStep(base_output_dir=tmp_path, filename="out.ndjson", emit_chunks=True)
    ctx = PageContext(
        url="https://example.com/",
        output_path=tmp_path / "page.md",
        markdown="full body",
        title="Page",
        chunks=[
            Chunk(index=0, text="chunk 0 body", token_count=4, heading="H1"),
            Chunk(index=1, text="chunk 1 body", token_count=4, heading="H2"),
        ],
    )
    await step.execute(ctx)
    out_path = step.finalize()

    lines = out_path.read_text().strip().split("\n")
    assert len(lines) == 2
    r0 = json.loads(lines[0])
    assert r0["chunk_index"] == 0
    assert r0["content"] == "chunk 0 body"
    assert r0["token_count"] == 4


@pytest.mark.asyncio
async def test_chunk_step_populates_ctx_chunks():
    step = ChunkStep(max_tokens=50)
    ctx = PageContext(
        url="https://example.com/",
        output_path=Path("/tmp/x.md"),
        markdown="# Title\n\n" + ("Paragraph. " * 30),
    )
    ctx = await step.execute(ctx)
    assert ctx.chunks
    assert all(hasattr(c, "token_count") for c in ctx.chunks)


@pytest.mark.asyncio
async def test_chunk_step_skips_when_no_markdown():
    step = ChunkStep(max_tokens=50)
    ctx = PageContext(url="https://example.com/", output_path=Path("/tmp/x.md"), markdown=None)
    ctx = await step.execute(ctx)
    assert ctx.chunks == []
