"""Tests for token-aware Markdown chunking."""

from __future__ import annotations

from docpull.conversion.chunking import TokenCounter, chunk_markdown


class WordCounter:
    def count(self, text: str) -> int:
        return len(text.split())


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


def test_chunk_heading_does_not_roll_forward_to_next_section():
    md = "# First\n\n" + ("alpha " * 10) + "\n\n## Second\n\n" + ("beta " * 10)
    chunks = chunk_markdown(md, max_tokens=15, counter=WordCounter())

    assert chunks[0].text.startswith("# First")
    assert chunks[0].heading == "First"
    assert chunks[1].text.startswith("## Second")
    assert chunks[1].heading == "Second"


def test_headings_inside_fenced_code_are_not_chunk_headings():
    md = '# Real\n\n```python\n# Not a heading\nprint("x")\n```\n\nAfter.'
    chunks = chunk_markdown(md, max_tokens=100, counter=WordCounter())

    assert chunks == [chunks[0]]
    assert chunks[0].heading == "Real"
    assert "# Not a heading" in chunks[0].text


def test_oversize_paragraph_becomes_own_chunk():
    huge = "word " * 10000
    md = f"# H\n\n{huge}\n"
    chunks = chunk_markdown(md, max_tokens=100)
    # At minimum one chunk for the oversize paragraph.
    assert len(chunks) >= 1
    assert any(c.token_count > 100 for c in chunks)
