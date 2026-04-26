"""Tests for the Python MCP tool layer (pure functions, no network)."""

from __future__ import annotations

import logging

import pytest

from docpull.mcp.sources import (
    BUILTIN_SOURCES,
    all_sources,
    is_safe_library_name,
    load_user_sources,
    resolve_source,
)
from docpull.mcp.tools import (
    fetch_url,
    grep_docs,
    list_indexed,
    list_sources,
    read_doc,
)


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


def test_resolve_profile_rejects_custom():
    """CUSTOM is a marker for ad-hoc config, not an agent-facing profile."""
    from docpull.mcp.tools import _resolve_profile

    with pytest.raises(ValueError, match="not exposed to agents"):
        _resolve_profile("custom")


# --- Security: SSRF ---------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_url_rejects_http():
    result = await fetch_url("http://example.com/")
    assert result.is_error
    assert "rejected" in result.text.lower() or "scheme" in result.text.lower()


@pytest.mark.asyncio
async def test_fetch_url_rejects_file_scheme():
    result = await fetch_url("file:///etc/passwd")
    assert result.is_error


@pytest.mark.asyncio
async def test_fetch_url_rejects_localhost():
    result = await fetch_url("https://localhost/admin")
    assert result.is_error
    assert "localhost" in result.text.lower() or "rejected" in result.text.lower()


@pytest.mark.asyncio
async def test_fetch_url_rejects_metadata_ip():
    result = await fetch_url("https://169.254.169.254/latest/meta-data/")
    assert result.is_error


@pytest.mark.asyncio
async def test_fetch_url_rejects_private_ip():
    result = await fetch_url("https://10.0.0.1/")
    assert result.is_error


# --- Security: path traversal ----------------------------------------


def test_is_safe_library_name_rejects_traversal():
    assert is_safe_library_name("react")
    assert is_safe_library_name("react-19")
    assert is_safe_library_name("my.docs")
    assert not is_safe_library_name("..")
    assert not is_safe_library_name("../etc")
    assert not is_safe_library_name("a/b")
    assert not is_safe_library_name(".hidden")
    assert not is_safe_library_name("")
    assert not is_safe_library_name("a" * 200)


def test_grep_docs_rejects_traversal_library(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "a.md").write_text("hello")
    result = grep_docs("hello", library="../etc", docs_dir=tmp_path)
    assert result.is_error
    assert "Invalid library" in result.text


def test_read_doc_rejects_traversal_library(tmp_path):
    (tmp_path / "real").mkdir()
    result = read_doc("../etc", "passwd", docs_dir=tmp_path)
    assert result.is_error


def test_read_doc_rejects_path_escape(tmp_path):
    """Even with a valid library name, path arg can't escape the lib root."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("inside")
    (tmp_path / "secret.md").write_text("outside")
    result = read_doc("lib", "../secret.md", docs_dir=tmp_path)
    assert result.is_error
    assert "escapes" in result.text.lower()


def test_read_doc_returns_full_file(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("line1\nline2\nline3")
    result = read_doc("lib", "a.md", docs_dir=tmp_path)
    assert not result.is_error
    assert "line1" in result.text and "line3" in result.text


def test_read_doc_slices_lines(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("\n".join(f"line{i}" for i in range(1, 11)))
    result = read_doc("lib", "a.md", docs_dir=tmp_path, line_start=3, line_end=5)
    assert not result.is_error
    assert "line3" in result.text and "line5" in result.text
    assert "line1" not in result.text
    assert "line7" not in result.text


def test_read_doc_missing_file(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    result = read_doc("lib", "nope.md", docs_dir=tmp_path)
    assert result.is_error


# --- Security: ReDoS / oversized pattern ------------------------------


def test_grep_docs_rejects_oversized_pattern(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("hello")
    result = grep_docs("a" * 1001, docs_dir=tmp_path)
    assert result.is_error
    assert "too long" in result.text.lower()


def test_grep_docs_rejects_invalid_regex(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("hello")
    result = grep_docs("[unclosed", docs_dir=tmp_path)
    assert result.is_error
    assert "Invalid pattern" in result.text


# --- Robustness -------------------------------------------------------


def test_load_user_sources_logs_yaml_error(tmp_path, caplog):
    path = tmp_path / "sources.yaml"
    path.write_text(": : : not valid yaml [")
    with caplog.at_level(logging.WARNING, logger="docpull.mcp.sources"):
        sources = load_user_sources(path=path)
    assert sources == {}
    assert any("Failed to parse" in rec.message for rec in caplog.records)


def test_partial_meta_treats_cache_as_stale(tmp_path):
    """A meta file marked partial=true should not be considered fresh."""
    import json
    import time

    from docpull.mcp.tools import _cache_fresh

    meta = tmp_path / ".x.meta.json"
    meta.write_text(
        json.dumps(
            {
                "fetched_at_epoch": time.time(),
                "page_count": 5,
                "partial": True,
            }
        )
    )
    assert _cache_fresh(meta) is False


def test_grep_docs_context_two_renders_two_lines(tmp_path):
    """`context=2` should render two lines above and below, not silently cap at 1."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "a.md").write_text("L1\nL2\nL3\nTARGET\nL5\nL6\nL7")
    result = grep_docs("TARGET", docs_dir=tmp_path, context=2)
    assert "L2" in result.text and "L3" in result.text
    assert "L5" in result.text and "L6" in result.text
    assert "L1" not in result.text
    assert "L7" not in result.text


def test_atomic_meta_write_no_tmp_left_behind(tmp_path):
    """After ``_write_meta``, no .tmp sibling should remain."""
    from docpull.mcp.tools import _write_meta

    meta = tmp_path / ".x.meta.json"
    _write_meta(meta, "x", "https://x.test", 3)
    assert meta.exists()
    assert not (tmp_path / ".x.meta.json.tmp").exists()
