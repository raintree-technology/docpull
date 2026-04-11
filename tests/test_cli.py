"""CLI regression tests."""

import pytest

from docpull.cli import create_parser


def test_parser_rejects_removed_js_flag():
    """Ensure the removed JavaScript flag stays unavailable."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--js"])
