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
