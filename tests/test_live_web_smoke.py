"""Free live-web smokes for ordinary public sources.

These tests are skipped by default and run from the scheduled
``live-web-smoke`` workflow. They intentionally avoid API keys and paid
services.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docpull.cli import main
from docpull.context_packs.feed import build_feed_pack
from docpull.context_packs.wiki import build_wiki_pack
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack

LIVE_WEB_SMOKE = pytest.mark.skipif(
    os.environ.get("DOCPULL_LIVE_WEB_SMOKE") != "1",
    reason="set DOCPULL_LIVE_WEB_SMOKE=1 to run free live-web smoke tests",
)


@LIVE_WEB_SMOKE
def test_live_public_blog_page_fetches_as_raw_pack(tmp_path: Path) -> None:
    pack_dir = tmp_path / "python-blog"

    assert main(["https://www.python.org/blogs/", "--single", "-o", str(pack_dir), "--quiet"]) == 0

    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    pack = load_pack(pack_dir)
    assert pack.documents
    assert any(record.url.startswith("https://www.python.org/blogs/") for record in pack.documents)
    assert any(record.content.strip() for record in pack.documents)


@LIVE_WEB_SMOKE
def test_live_public_feed_pack_writes_item_level_records(tmp_path: Path) -> None:
    pack_dir = tmp_path / "python-blog-feed"

    result = build_feed_pack("https://blog.python.org/feeds/posts/default", output_dir=pack_dir, max_items=3)

    assert result["validation"]["status"] == "pass"
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    pack = load_pack(pack_dir)
    assert pack.documents
    assert {record.source_type for record in pack.documents} == {"feed_item"}


@LIVE_WEB_SMOKE
def test_live_public_feed_discovery_writes_item_level_records(tmp_path: Path) -> None:
    pack_dir = tmp_path / "python-blog-feed-discovery"

    result = build_feed_pack("https://blog.python.org/", output_dir=pack_dir, max_items=3)

    assert result["validation"]["status"] == "pass"
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    pack = load_pack(pack_dir)
    assert pack.documents
    assert {record.source_type for record in pack.documents} == {"feed_item"}


@LIVE_WEB_SMOKE
def test_live_wikimedia_api_pack_avoids_robots_blocked_html_path(tmp_path: Path) -> None:
    pack_dir = tmp_path / "wiki"

    result = build_wiki_pack(["wiki:Web_scraping"], output_dir=pack_dir, max_items=3)

    assert result["validation"]["status"] == "pass"
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    pack = load_pack(pack_dir)
    assert any(record.source_type == "wiki_section" for record in pack.documents)
