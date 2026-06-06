"""CLI regression tests."""

from importlib.metadata import version

import pytest

import docpull
from docpull.cli import create_parser, run_fetcher


def test_runtime_version_matches_package_metadata():
    assert docpull.__version__ == version("docpull")


def test_parser_rejects_removed_js_flag():
    """Ensure the removed JavaScript flag stays unavailable."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--js"])


@pytest.mark.parametrize("alias", ["flat", "short"])
def test_parser_rejects_removed_naming_aliases(alias: str):
    """Ensure removed naming aliases stay unavailable at the CLI boundary."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--naming-strategy", alias])


def test_parser_accepts_supported_naming_strategies():
    parser = create_parser()

    full = parser.parse_args(["https://example.com", "--naming-strategy", "full"])
    hierarchical = parser.parse_args(["https://example.com", "--naming-strategy", "hierarchical"])

    assert full.naming_strategy == "full"
    assert hierarchical.naming_strategy == "hierarchical"


def test_help_describes_insecure_tls_as_rejected():
    parser = create_parser()

    assert "Deprecated and rejected" in parser.format_help()


def test_help_describes_mirror_naming_override():
    parser = create_parser()
    help_text = " ".join(parser.format_help().split())

    assert "Mirror profile defaults to hierarchical unless explicitly overridden" in help_text


def test_single_invalid_url_returns_nonzero(tmp_path):
    parser = create_parser()
    args = parser.parse_args(["http://example.com", "--single", "--output-dir", str(tmp_path)])

    assert run_fetcher(args) == 1
