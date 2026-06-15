"""Pytest configuration and fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def _benchmark_path(path: Path) -> bool:
    return path.parent.name == "benchmarks" and path.name.startswith("test_")


def _explicitly_requested(path: Path, config: pytest.Config) -> bool:
    for arg in config.invocation_params.args:
        if arg.startswith("-"):
            continue

        requested = Path(arg.split("::", 1)[0])
        if not requested.is_absolute():
            requested = Path(config.rootpath) / requested

        try:
            if path.resolve().is_relative_to(requested.resolve()):
                return True
        except FileNotFoundError:
            continue

    return False


def pytest_ignore_collect(collection_path: object, config: pytest.Config) -> bool:
    """Keep benchmarks out of default coverage runs unless explicitly requested."""
    path = Path(str(collection_path))
    if not _benchmark_path(path):
        return False

    if path.name == "test_10k_pages.py":
        return os.environ.get("DOCPULL_BENCHMARK_10K") != "1"

    if os.environ.get("DOCPULL_BENCHMARKS") == "1":
        return False

    return not _explicitly_requested(path, config)


@pytest.fixture
def temp_output_dir(tmp_path):
    """Provide an isolated output directory for tests."""
    output_dir = tmp_path / "test_docs"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def sample_html():
    """Provide sample HTML for testing."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Page</title>
    </head>
    <body>
        <h1>Test Heading</h1>
        <p>Test paragraph with <a href="/link">a link</a>.</p>
        <ul>
            <li>Item 1</li>
            <li>Item 2</li>
        </ul>
    </body>
    </html>
    """


@pytest.fixture
def sample_config():
    """Provide sample configuration dictionary."""
    return {
        "output_dir": "./docs",
        "rate_limit": 0.5,
        "skip_existing": True,
        "log_level": "INFO",
    }
