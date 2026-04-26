"""Tests for the Python MCP tool layer (pure functions, no network)."""

from __future__ import annotations

from docpull.mcp.sources import (
    BUILTIN_SOURCES,
    all_sources,
    load_user_sources,
    resolve_source,
)
from docpull.mcp.tools import grep_docs, list_indexed, list_sources


def test_builtin_sources_include_common_libraries():
    assert "react" in BUILTIN_SOURCES
    assert "nextjs" in BUILTIN_SOURCES
    assert "anthropic" in BUILTIN_SOURCES


def test_resolve_rejects_raw_urls():
    assert resolve_source("https://example.com/") is None


def test_resolve_known_alias():
    src = resolve_source("react")
    assert src is not None
    assert src.url.startswith("https://")


def test_resolve_unknown_returns_none():
    assert resolve_source("this-does-not-exist-xyz") is None


def test_list_sources_renders_rows():
    result = list_sources()
    assert "react" in result.text
    assert result.is_error is False


def test_list_sources_filter_by_category():
    result = list_sources(category="ai")
    assert "anthropic" in result.text
    assert "react" not in result.text


def test_list_indexed_when_empty(tmp_path):
    result = list_indexed(docs_dir=tmp_path)
    assert "No fetched docs" in result.text or "No docs fetched" in result.text


def test_grep_docs_requires_fetched_content(tmp_path):
    # Empty dir: should error cleanly
    result = grep_docs("pattern", docs_dir=tmp_path / "missing")
    assert result.is_error is True


def test_grep_docs_finds_matches(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    (lib / "a.md").write_text("line one\nHello world\nbye")
    result = grep_docs("Hello", docs_dir=tmp_path)
    assert "Hello world" in result.text
    assert result.is_error is False


def test_load_user_sources_missing_file(tmp_path):
    sources = load_user_sources(path=tmp_path / "does-not-exist.yaml")
    assert sources == {}


def test_load_user_sources_parses_yaml(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        """
sources:
  mydocs:
    url: https://example.com/docs
    description: My docs
    category: internal
    maxPages: 50
"""
    )
    sources = load_user_sources(path=path)
    assert "mydocs" in sources
    assert sources["mydocs"].url == "https://example.com/docs"
    assert sources["mydocs"].max_pages == 50


def test_all_sources_merges_builtin_and_user(tmp_path, monkeypatch):
    path = tmp_path / "sources.yaml"
    path.write_text("sources:\n  custom1:\n    url: https://example.com\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "docpull-mcp").mkdir()
    (tmp_path / "docpull-mcp" / "sources.yaml").write_text(
        "sources:\n  custom1:\n    url: https://example.com\n    description: custom\n    category: user\n"
    )
    merged = all_sources()
    assert "react" in merged  # builtin present
    assert "custom1" in merged  # user present


def test_grep_docs_ranks_by_match_density(tmp_path):
    """Files with more matches should appear before files with fewer."""
    a = tmp_path / "lib_a"
    b = tmp_path / "lib_b"
    a.mkdir()
    b.mkdir()
    (a / "page.md").write_text(
        "needle one\nneedle two\nneedle three\nfour\nfive"
    )
    (b / "page.md").write_text("filler\nneedle one\nfiller")

    result = grep_docs("needle", docs_dir=tmp_path)
    assert result.is_error is False
    # File with 3 matches should appear before file with 1 match.
    a_idx = result.text.find("lib_a/page.md")
    b_idx = result.text.find("lib_b/page.md")
    assert a_idx >= 0 and b_idx >= 0
    assert a_idx < b_idx


def test_grep_docs_includes_context_lines(tmp_path):
    """Each match should include a line above and below by default."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "page.md").write_text("alpha\ntargeted\nbravo\ncharlie")

    result = grep_docs("targeted", docs_dir=tmp_path)
    assert "alpha" in result.text  # line before
    assert "bravo" in result.text  # line after
    assert "targeted" in result.text


def test_grep_docs_context_zero_disables_context(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "page.md").write_text("alpha\ntargeted\nbravo")

    result = grep_docs("targeted", docs_dir=tmp_path, context=0)
    # The hit line is present but the surrounding lines are not.
    assert "targeted" in result.text
    assert "alpha" not in result.text


def test_list_indexed_reports_fetch_age(tmp_path):
    """list_indexed should surface a humanized age string when meta exists."""
    import json
    import time

    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "index.md").write_text("body")

    meta = tmp_path / ".src.meta.json"
    meta.write_text(
        json.dumps(
            {
                "source": "src",
                "url": "https://x.test/",
                "fetched_at_epoch": time.time() - 3700,  # ~1h 1m ago
                "fetched_at": "2026-04-26T00:00:00",
                "page_count": 1,
            }
        )
    )

    result = list_indexed(docs_dir=tmp_path)
    assert "fetched 1h ago" in result.text


def test_resolve_profile_accepts_known_names():
    from docpull.mcp.tools import _resolve_profile
    from docpull.models.config import ProfileName

    assert _resolve_profile("rag") is ProfileName.RAG
    assert _resolve_profile("RAG") is ProfileName.RAG
    assert _resolve_profile(None) is ProfileName.RAG
    assert _resolve_profile("llm") is ProfileName.LLM


def test_resolve_profile_rejects_unknown():
    from docpull.mcp.tools import _resolve_profile

    try:
        _resolve_profile("bogus")
    except ValueError as err:
        assert "Unknown profile" in str(err)
    else:
        raise AssertionError("expected ValueError")
