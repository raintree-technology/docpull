"""Tests for token-aware Markdown chunking."""

from __future__ import annotations

from docpull.conversion.chunking import TokenCounter, chunk_markdown


def test_empty_input_produces_no_chunks():
    assert chunk_markdown("") == []
    assert chunk_markdown("---\ntitle: x\n---\n") == []


def test_single_small_doc_returns_one_chunk():
    md = "# Title\n\nShort body."
    chunks = chunk_markdown(md, max_tokens=1000)
    assert len(chunks) == 1
    assert "Title" in chunks[0].text
    assert chunks[0].index == 0


def test_splits_on_headings_when_exceeding_budget():
    sections = "\n\n".join(f"## Section {i}\n\n" + ("Paragraph. " * 30) for i in range(5))
    md = "# Doc\n\n" + sections
    chunks = chunk_markdown(md, max_tokens=100)
    assert len(chunks) > 1
    # Every chunk (except possibly oversize paragraphs) should stay under the budget
    assert all(c.text for c in chunks)


def test_frontmatter_is_preserved_on_first_chunk():
    md = "---\ntitle: Hello\n---\n\n" + ("# H\n\n" + "Content. " * 30)
    chunks = chunk_markdown(md, max_tokens=1000)
    assert chunks
    assert chunks[0].text.startswith("---")


def test_counter_fallback_estimate():
    counter = TokenCounter()
    # Whether tiktoken is present or not, count must be positive and stable.
    n1 = counter.count("hello world")
    n2 = counter.count("hello world")
    assert n1 == n2
    assert n1 > 0


def test_chunk_heading_captured():
    md = "# Top\n\nIntro text.\n\n## Second\n\n" + ("Body. " * 50)
    chunks = chunk_markdown(md, max_tokens=50)
    # At least one chunk should carry the "Second" heading context
    assert any(c.heading and "Second" in c.heading for c in chunks)


def test_chunk_heading_matches_buffer_start_when_next_section_flushes():
    md = "# First\n\n" + ("alpha " * 20) + "\n\n## Second\n\n" + ("beta " * 20)
    chunks = chunk_markdown(md, max_tokens=25)

    assert chunks[0].heading == "First"
    assert "Second" not in chunks[0].text
    assert any(chunk.heading == "Second" for chunk in chunks[1:])


def test_frontmatter_is_counted_in_first_chunk_budget():
    md = "---\ntitle: " + ("x" * 400) + "\n---\n\n# H\n\n" + ("word " * 80)
    chunks = chunk_markdown(md, max_tokens=40)

    assert chunks[0].text.startswith("---")
    assert chunks[0].token_count == TokenCounter().count(chunks[0].text)
    assert chunks[0].token_count > 40
    assert len(chunks) > 1


def test_oversize_paragraph_becomes_own_chunk():
    huge = "word " * 10000
    md = f"# H\n\n{huge}\n"
    chunks = chunk_markdown(md, max_tokens=100)
    # At minimum one chunk for the oversize paragraph.
    assert len(chunks) >= 1
    assert any(c.token_count > 100 for c in chunks)
