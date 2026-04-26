"""Tests for output naming strategies."""

from __future__ import annotations

from pathlib import Path

from docpull.core.fetcher import Fetcher, _url_to_filename, _url_to_path_parts
from docpull.models.config import DocpullConfig


class TestHierarchicalPathParts:
    """Test the URL → nested-path-parts conversion."""

    def test_simple_path(self) -> None:
        assert _url_to_path_parts(
            "https://docs.foo.com/api/auth/oauth2"
        ) == ["api", "auth", "oauth2.md"]

    def test_root_url(self) -> None:
        assert _url_to_path_parts("https://docs.foo.com/") == ["index.md"]

    def test_trailing_slash_becomes_index(self) -> None:
        assert _url_to_path_parts("https://docs.foo.com/api/") == [
            "api",
            "index.md",
        ]

    def test_strips_html_extension(self) -> None:
        assert _url_to_path_parts(
            "https://docs.foo.com/api/auth.html"
        ) == ["api", "auth.md"]

    def test_strips_htm_extension(self) -> None:
        assert _url_to_path_parts("https://docs.foo.com/index.htm") == [
            "index.md"
        ]

    def test_strips_base_path(self) -> None:
        result = _url_to_path_parts(
            "https://docs.foo.com/v2/api/auth",
            base_url="https://docs.foo.com/v2",
        )
        assert result == ["api", "auth.md"]

    def test_unsafe_segment_sanitized(self) -> None:
        result = _url_to_path_parts(
            "https://docs.foo.com/foo bar/with$special"
        )
        assert result == ["foo_bar", "with_special.md"]

    def test_traversal_segment_neutralized(self) -> None:
        # URLs with `..` segments should not allow path traversal in output.
        result = _url_to_path_parts("https://docs.foo.com/foo/../etc/passwd")
        assert ".." not in result
        assert "passwd.md" in result[-1]

    def test_segments_dot_sequence_replaced_with_index(self) -> None:
        # A segment that's literally "." or ".." (after sanitization) becomes
        # "index" so we never emit an empty or traversal name.
        result = _url_to_path_parts("https://docs.foo.com/./api")
        assert "index" in result or result == ["api.md"]


class TestComputeOutputPath:
    """Test that Fetcher routes to the right strategy."""

    def test_full_strategy_is_default(self, tmp_path: Path) -> None:
        config = DocpullConfig(
            url="https://docs.foo.com/v2",
            output={"directory": tmp_path, "naming_strategy": "full"},
        )
        fetcher = Fetcher(config)
        # _compute_output_path doesn't need __aenter__; it only reads config.
        path = fetcher._compute_output_path("https://docs.foo.com/v2/api/auth")
        assert path == tmp_path / "api_auth.md"

    def test_hierarchical_strategy(self, tmp_path: Path) -> None:
        config = DocpullConfig(
            url="https://docs.foo.com/v2",
            output={"directory": tmp_path, "naming_strategy": "hierarchical"},
        )
        fetcher = Fetcher(config)
        path = fetcher._compute_output_path("https://docs.foo.com/v2/api/auth")
        assert path == tmp_path / "api" / "auth.md"

    def test_hierarchical_trailing_slash(self, tmp_path: Path) -> None:
        config = DocpullConfig(
            url="https://docs.foo.com/",
            output={"directory": tmp_path, "naming_strategy": "hierarchical"},
        )
        fetcher = Fetcher(config)
        path = fetcher._compute_output_path("https://docs.foo.com/api/")
        assert path == tmp_path / "api" / "index.md"

    def test_flat_aliases_to_full(self, tmp_path: Path) -> None:
        config = DocpullConfig(
            url="https://docs.foo.com",
            output={"directory": tmp_path, "naming_strategy": "flat"},
        )
        fetcher = Fetcher(config)
        # `flat` and `short` route through _url_to_filename until 3.0.
        path = fetcher._compute_output_path("https://docs.foo.com/api/auth")
        assert path.suffix == ".md"
        assert path.parent == tmp_path


class TestFlattenedFilename:
    """Regression test for _url_to_filename — keeps current behavior intact."""

    def test_strips_base(self) -> None:
        assert (
            _url_to_filename(
                "https://docs.foo.com/v2/api/auth",
                base_url="https://docs.foo.com/v2",
            )
            == "api_auth.md"
        )

    def test_root_becomes_index(self) -> None:
        assert _url_to_filename("https://docs.foo.com/") == "index.md"
