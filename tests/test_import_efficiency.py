"""Regression coverage for the lightweight package import boundary."""

from __future__ import annotations

import subprocess
import sys

import docpull


def test_all_public_sdk_exports_resolve() -> None:
    for name in docpull.__all__:
        assert getattr(docpull, name) is not None


def test_plain_package_import_does_not_load_fetch_or_html_stacks() -> None:
    script = """
import sys
import docpull
blocked = {'aiohttp', 'bs4', 'docpull.core.fetcher', 'docpull.context_ci'}
loaded = sorted(blocked.intersection(sys.modules))
if loaded:
    raise SystemExit(','.join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _assert_import_avoids(statement: str, blocked: set[str]) -> None:
    script = f"""
import sys
{statement}
blocked = {blocked!r}
loaded = sorted(blocked.intersection(sys.modules))
if loaded:
    raise SystemExit(','.join(loaded))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_lightweight_internal_features_do_not_import_unrelated_stacks() -> None:
    _assert_import_avoids(
        "import docpull.models.events",
        {"pydantic", "docpull.models.run", "docpull.models.config"},
    )
    _assert_import_avoids(
        "import docpull.cache.frontier",
        {"pydantic", "docpull.cache.manager", "docpull.models.run"},
    )
    _assert_import_avoids(
        "import docpull.http.protocols",
        {"aiohttp", "docpull.http.client", "docpull.http.rate_limiter"},
    )
    _assert_import_avoids(
        "import docpull.conversion.chunking",
        {"bs4", "docpull.conversion.extractor", "docpull.conversion.special_cases"},
    )
    _assert_import_avoids(
        "import docpull.mcp.sources",
        {"aiohttp", "docpull.mcp.server", "docpull.core.fetcher"},
    )
    _assert_import_avoids(
        "import docpull.project",
        {"aiohttp", "bs4", "docpull.core.fetcher"},
    )


def test_internal_package_public_exports_still_resolve() -> None:
    script = """
import importlib

packages = (
    'docpull.models',
    'docpull.http',
    'docpull.cache',
    'docpull.conversion',
    'docpull.pipeline.steps',
    'docpull.mcp',
    'docpull.discovery',
    'docpull.discovery.link_extractors',
    'docpull.security',
    'docpull.context_packs',
)
for package_name in packages:
    package = importlib.import_module(package_name)
    for name in package.__all__:
        if getattr(package, name) is None:
            raise SystemExit(f'{package_name}.{name}')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
