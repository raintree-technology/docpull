"""CLI regression tests."""

import subprocess
import sys
from importlib.metadata import version

import pytest

import docpull
from docpull.cli import create_parser


def test_runtime_version_matches_package_metadata():
    assert docpull.__version__ == version("docpull")


def test_parser_rejects_removed_js_flag():
    """Ensure the removed JavaScript flag stays unavailable."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--js"])


def test_parser_rejects_removed_naming_aliases():
    """Removed naming aliases should stay unavailable at the CLI surface."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--naming-strategy", "flat"])

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--naming-strategy", "short"])


def test_importing_cli_has_no_doctor_side_effect():
    """Importing the CLI module must not inspect sys.argv and exit."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import sys; sys.argv=['docpull', '--doctor']; import docpull.cli; print('imported')"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert result.stdout.strip() == "imported"
    assert "Running docpull diagnostics" not in result.stdout
