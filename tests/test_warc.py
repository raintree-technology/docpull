"""WARC output tests: unit coverage for the writer plus a Fetcher integration run.

The integration tests mirror tests/test_cache_conditional_get.py: a local
aiohttp server plus monkeypatched URL/robots validators so no network access
is required.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web

from docpull.core.fetcher import Fetcher
from docpull.models.config import DocpullConfig
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidator
from docpull.warc import WARC_FILENAME, WarcWriter, payload_digest, read_warc_records

FETCHED_AT = datetime(2026, 7, 21, 12, 0, 0, tzinfo=timezone.utc)

PAGE_HTML = b"""<!doctype html><html><body><article>
<h1>Hello</h1><p>Archived page</p>
</article></body></html>"""


def _split_http_block(block: bytes) -> tuple[bytes, bytes]:
    head, _, body = block.partition(b"\r\n\r\n")
    return head, body


def _count_gzip_members(path: Path) -> int:
    raw = path.read_bytes()
    members = 0
    while raw:
        decompressor = zlib.decompressobj(wbits=31)
        decompressor.decompress(raw)
        assert decompressor.eof, "truncated gzip member"
        members += 1
        raw = decompressor.unused_data
    return members


def test_writer_writes_warcinfo_then_response(tmp_path: Path) -> None:
    warc_path = tmp_path / WARC_FILENAME
    writer = WarcWriter(warc_path)
    record_id = writer.write_response(
        url="https://example.com/page",
        status_code=200,
        headers={"Content-Type": "text/html", "X-Custom": "yes"},
        body=PAGE_HTML,
        fetched_at=FETCHED_AT,
    )
    writer.close()

    records = list(read_warc_records(warc_path))
    assert len(records) == 2

    info_headers, info_block = records[0]
    assert info_headers["WARC-Type"] == "warcinfo"
    assert info_headers["WARC-Filename"] == WARC_FILENAME
    assert info_headers["Content-Type"] == "application/warc-fields"
    assert b"software: docpull/" in info_block
    assert b"format: WARC File Format 1.1" in info_block

    resp_headers, resp_block = records[1]
    assert resp_headers["WARC-Type"] == "response"
    assert resp_headers["WARC-Record-ID"] == record_id
    assert re.fullmatch(r"<urn:uuid:[0-9a-f-]{36}>", record_id)
    assert resp_headers["WARC-Target-URI"] == "https://example.com/page"
    assert resp_headers["Content-Type"] == "application/http;msgtype=response"
    assert resp_headers["WARC-Date"] == "2026-07-21T12:00:00Z"
    assert int(resp_headers["Content-Length"]) == len(resp_block)

    head, body = _split_http_block(resp_block)
    assert head.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"X-Custom: yes" in head
    assert f"Content-Length: {len(PAGE_HTML)}".encode() in head
    assert body == PAGE_HTML


def test_payload_digest_matches_body_sha256(tmp_path: Path) -> None:
    warc_path = tmp_path / WARC_FILENAME
    body = b"payload bytes \x00\xff"
    writer = WarcWriter(warc_path)
    writer.write_response(
        url="https://example.com/x",
        status_code=200,
        headers={},
        body=body,
        fetched_at=FETCHED_AT,
    )
    writer.close()

    headers, _block = list(read_warc_records(warc_path))[1]
    expected = f"sha256:{hashlib.sha256(body).hexdigest()}"
    assert headers["WARC-Payload-Digest"] == expected
    assert payload_digest(body) == expected


def test_sensitive_and_hop_by_hop_headers_stripped(tmp_path: Path) -> None:
    warc_path = tmp_path / WARC_FILENAME
    writer = WarcWriter(warc_path)
    writer.write_response(
        url="https://example.com/page",
        status_code=200,
        headers={
            "Set-Cookie": "session=secret",
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=5",
            "Transfer-Encoding": "chunked",
            "Upgrade": "h2c",
            "Proxy-Authenticate": "Basic",
            "Proxy-Connection": "keep-alive",
            "Authorization": "Bearer token",
            "WWW-Authenticate": "Basic realm=x",
            "X-Kept": "kept-value",
        },
        body=b"body",
        fetched_at=FETCHED_AT,
    )
    writer.close()

    _headers, block = list(read_warc_records(warc_path))[1]
    head, _body = _split_http_block(block)
    text = head.decode("utf-8").lower()
    for banned in (
        "set-cookie",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "upgrade",
        "proxy-",
        "authorization",
        "www-authenticate",
        "secret",
        "bearer token",
    ):
        assert banned not in text
    assert "x-kept: kept-value" in text


def test_crlf_in_header_values_is_sanitized(tmp_path: Path) -> None:
    warc_path = tmp_path / WARC_FILENAME
    writer = WarcWriter(warc_path)
    writer.write_response(
        url="https://example.com/page",
        status_code=200,
        headers={"X-Weird": "evil\r\nInjected: yes"},
        body=b"body",
        fetched_at=FETCHED_AT,
    )
    writer.close()

    _headers, block = list(read_warc_records(warc_path))[1]
    head, _body = _split_http_block(block)
    lines = head.split(b"\r\n")
    assert not any(line.startswith(b"Injected:") for line in lines)
    assert b"X-Weird: evilInjected: yes" in lines


def test_each_record_is_a_separate_gzip_member(tmp_path: Path) -> None:
    warc_path = tmp_path / WARC_FILENAME
    writer = WarcWriter(warc_path)
    for i in range(3):
        writer.write_response(
            url=f"https://example.com/{i}",
            status_code=200,
            headers={},
            body=f"body {i}".encode(),
            fetched_at=FETCHED_AT,
        )
    writer.close()

    # warcinfo + 3 responses, each its own gzip member.
    assert _count_gzip_members(warc_path) == 4
    assert len(list(read_warc_records(warc_path))) == 4

    # Sanity: plain gzip decompression sees the concatenated members too.
    with gzip.open(warc_path, "rb") as fh:
        assert fh.read().count(b"WARC/1.1\r\n") == 4


def test_reopening_existing_file_appends_without_second_warcinfo(tmp_path: Path) -> None:
    warc_path = tmp_path / WARC_FILENAME
    first = WarcWriter(warc_path)
    first.write_response(
        url="https://example.com/1", status_code=200, headers={}, body=b"a", fetched_at=FETCHED_AT
    )
    first.close()

    second = WarcWriter(warc_path)
    second.write_response(
        url="https://example.com/2", status_code=200, headers={}, body=b"b", fetched_at=FETCHED_AT
    )
    second.close()

    records = list(read_warc_records(warc_path))
    types = [headers["WARC-Type"] for headers, _ in records]
    assert types == ["warcinfo", "response", "response"]


@pytest.fixture
async def server(monkeypatch):
    """Local aiohttp server; validators patched to accept plain-HTTP localhost."""

    async def handler(_request: web.Request) -> web.Response:
        return web.Response(
            body=PAGE_HTML,
            content_type="text/html",
            headers={"X-Custom": "custom-value", "Set-Cookie": "session=topsecret"},
        )

    async def robots(_request: web.Request) -> web.Response:
        return web.Response(text="", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/page", handler)
    app.router.add_get("/robots.txt", robots)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server_socket = site._server.sockets[0]  # type: ignore[union-attr]
    port = server_socket.getsockname()[1]

    def permissive_validate(self, hostname):  # type: ignore[no-untyped-def]
        from docpull.security.url_validator import UrlValidationResult

        return UrlValidationResult.valid()

    monkeypatch.setattr(UrlValidator, "validate_hostname", permissive_validate)

    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)

    yield {"url": f"http://127.0.0.1:{port}/page"}

    await runner.cleanup()


async def _run(config: DocpullConfig) -> None:
    async with Fetcher(config) as fetcher:
        async for _event in fetcher.run():
            pass


@pytest.mark.asyncio
async def test_fetcher_writes_warc_and_links_manifest(server, tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    cfg = DocpullConfig(
        url=server["url"],
        output={"directory": output_dir},
        crawl={"max_pages": 1, "max_depth": 1},
        warc_output=True,
    )
    await _run(cfg)

    warc_path = output_dir / WARC_FILENAME
    assert warc_path.exists()

    records = list(read_warc_records(warc_path))
    assert [headers["WARC-Type"] for headers, _ in records] == ["warcinfo", "response"]
    resp_headers, resp_block = records[1]
    assert resp_headers["WARC-Target-URI"] == server["url"]
    assert resp_headers["WARC-Payload-Digest"] == payload_digest(PAGE_HTML)

    head, body = _split_http_block(resp_block)
    assert body == PAGE_HTML, "archived body must be byte-for-byte what the server sent"
    assert head.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"custom-value" in head
    assert b"topsecret" not in head, "Set-Cookie must not be archived"

    manifest = json.loads((output_dir / "corpus.manifest.json").read_text(encoding="utf-8"))
    assert manifest["archive"] == {"warc_path": WARC_FILENAME, "warc_record_count": 1}
    page_records = [item for item in manifest["records"] if item["url"] == server["url"]]
    assert page_records
    for item in page_records:
        assert item["warc_record_id"] == resp_headers["WARC-Record-ID"]
        assert item["raw_content_hash"] == payload_digest(PAGE_HTML)


@pytest.mark.asyncio
async def test_warc_disabled_by_default(server, tmp_path: Path) -> None:
    output_dir = tmp_path / "out"
    cfg = DocpullConfig(
        url=server["url"],
        output={"directory": output_dir},
        crawl={"max_pages": 1, "max_depth": 1},
    )
    assert cfg.warc_output is False
    await _run(cfg)

    assert not (output_dir / WARC_FILENAME).exists()
    manifest = json.loads((output_dir / "corpus.manifest.json").read_text(encoding="utf-8"))
    assert "archive" not in manifest
    for item in manifest["records"]:
        assert "warc_record_id" not in item
        assert "raw_content_hash" not in item
