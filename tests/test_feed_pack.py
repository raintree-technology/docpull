"""Tests for local RSS/Atom/JSON Feed packs."""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from docpull.cli import main
from docpull.context_packs.feed import build_feed_pack
from docpull.output_contract import validate_pack_contract
from docpull.pack_reader import load_pack
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidationResult, UrlValidator


def _rss_fixture() -> str:
    return """<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>Example News</title>
    <link>https://news.example.com/</link>
    <description>Latest example stories.</description>
    <item>
      <title>Alpha Launches</title>
      <link>https://news.example.com/alpha</link>
      <guid>alpha-guid</guid>
      <pubDate>Wed, 01 Jul 2026 10:30:00 GMT</pubDate>
      <description><![CDATA[<p>Alpha shipped a cited release.</p>]]></description>
      <content:encoded><![CDATA[<p>Alpha shipped a cited release with evidence.</p>]]></content:encoded>
      <category>Launches</category>
    </item>
    <item>
      <title>Beta Updates</title>
      <link>https://news.example.com/beta</link>
      <guid>beta-guid</guid>
      <pubDate>Thu, 02 Jul 2026 11:00:00 GMT</pubDate>
      <description>Beta added freshness metadata.</description>
    </item>
  </channel>
</rss>
"""


def test_build_feed_pack_writes_item_level_v3_records(tmp_path: Path) -> None:
    feed_path = tmp_path / "feed.xml"
    feed_path.write_text(_rss_fixture(), encoding="utf-8")
    pack_dir = tmp_path / "pack"

    result = build_feed_pack(feed_path, output_dir=pack_dir)

    assert result["workflow"] == "feed-pack"
    assert result["validation"]["status"] == "pass"
    assert result["summary"]["item_count"] == 2
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
    for artifact in (
        "feed.index.json",
        "feed.items.ndjson",
        "listing.items.ndjson",
        "freshness.report.json",
        "FEED.md",
    ):
        assert (pack_dir / artifact).exists()

    feed_items = [
        json.loads(line) for line in (pack_dir / "feed.items.ndjson").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["item_citation_id"] for item in feed_items] == ["I1", "I2"]
    assert feed_items[0]["published_at"] == "2026-07-01T10:30:00+00:00"

    pack = load_pack(pack_dir)
    assert len(pack.documents) == 2
    assert {record.source_type for record in pack.documents} == {"feed_item"}
    first = pack.documents[0]
    assert first.route["name"] == "local-feed-parse"
    assert first.route["feed_format"] == "rss"
    assert first.metadata["feed_title"] == "Example News"
    assert first.metadata["published_at"] == "2026-07-01T10:30:00+00:00"
    assert pack.record_citation_id(first) == "S1.1"


def test_feed_pack_cli_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    feed_path = tmp_path / "feed.xml"
    feed_path.write_text(_rss_fixture(), encoding="utf-8")
    pack_dir = tmp_path / "pack"

    assert main(["feed-pack", str(feed_path), "-o", str(pack_dir), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "feed-pack"
    assert payload["summary"]["feed_format"] == "rss"
    assert payload["summary"]["item_count"] == 2
    assert payload["validation"]["status"] == "pass"


def test_json_feed_pack_supports_json_feed_sources(tmp_path: Path) -> None:
    feed_path = tmp_path / "feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "version": "https://jsonfeed.org/version/1.1",
                "title": "Example JSON Feed",
                "home_page_url": "https://news.example.com/",
                "items": [
                    {
                        "id": "json-1",
                        "url": "/json-1",
                        "title": "JSON Feed Item",
                        "date_published": "2026-07-02T12:00:00Z",
                        "content_html": "<p>JSON feed item content.</p>",
                        "tags": ["json", "feeds"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pack_dir = tmp_path / "pack"

    result = build_feed_pack(feed_path, output_dir=pack_dir)

    assert result["summary"]["feed_format"] == "json-feed"
    assert result["summary"]["item_count"] == 1
    assert load_pack(pack_dir).documents[0].metadata["categories"] == ["json", "feeds"]


def test_feed_pack_discovers_advertised_feed_from_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    (site_dir / "index.html").write_text(
        """<!doctype html>
<html>
  <head>
    <title>News</title>
    <link rel="alternate" type="application/rss+xml" href="/feed.xml">
  </head>
  <body><main><h1>News</h1></main></body>
</html>
""",
        encoding="utf-8",
    )
    (site_dir / "feed.xml").write_text(
        """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Advertised News</title>
    <item>
      <title>Relative Story</title>
      <link>/stories/relative</link>
      <pubDate>Thu, 02 Jul 2026 16:00:00 GMT</pubDate>
      <description>Story from an advertised feed.</description>
    </item>
  </channel>
</rss>
""",
        encoding="utf-8",
    )

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(site_dir)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    original_init = UrlValidator.__init__

    def permissive_init(self: UrlValidator, *args: Any, **kwargs: Any) -> None:
        kwargs["allowed_schemes"] = {"http", "https"}
        kwargs["block_private_ips"] = False
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", permissive_init)
    monkeypatch.setattr(
        UrlValidator,
        "validate_hostname",
        lambda self, _hostname: UrlValidationResult.valid(),
    )
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, _url: True)

    try:
        start_url = f"http://127.0.0.1:{server.server_port}/index.html"
        pack_dir = tmp_path / "pack"
        result = build_feed_pack(start_url, output_dir=pack_dir)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["discovered_from"] == start_url
    assert result["source"].endswith("/feed.xml")
    record = load_pack(pack_dir).documents[0]
    assert record.url == f"http://127.0.0.1:{server.server_port}/stories/relative"
    assert record.metadata["discovered_from"] == start_url


def test_feed_pack_accepts_mislabeled_octet_stream_feed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FeedHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = _rss_fixture().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    original_init = UrlValidator.__init__

    def permissive_init(self: UrlValidator, *args: Any, **kwargs: Any) -> None:
        kwargs["allowed_schemes"] = {"http", "https"}
        kwargs["block_private_ips"] = False
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", permissive_init)
    monkeypatch.setattr(
        UrlValidator,
        "validate_hostname",
        lambda self, _hostname: UrlValidationResult.valid(),
    )
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, _url: True)

    try:
        pack_dir = tmp_path / "pack"
        result = build_feed_pack(f"http://127.0.0.1:{server.server_port}/feed", output_dir=pack_dir)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result["summary"]["item_count"] == 2
    assert validate_pack_contract(pack_dir, level="raw")["status"] == "pass"
