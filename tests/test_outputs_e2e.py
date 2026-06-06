"""Local end-to-end output format tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web

from docpull.core.fetcher import Fetcher
from docpull.models.config import DocpullConfig
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidator

PAGE_HTML = b"""<!doctype html><html><head><title>Alpha Docs</title></head>
<body><article><h1>Alpha</h1><p>First page for local e2e output tests.</p></article></body></html>"""


@pytest.fixture
async def output_server(monkeypatch: pytest.MonkeyPatch):
    async def page(_request: web.Request) -> web.Response:
        return web.Response(body=PAGE_HTML, content_type="text/html")

    app = web.Application()
    app.router.add_get("/docs/alpha", page)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    def permissive_validate(self, hostname):  # type: ignore[no-untyped-def]
        from docpull.security.url_validator import UrlValidationResult

        return UrlValidationResult.valid()

    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "validate_hostname", permissive_validate)
    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)

    yield f"http://127.0.0.1:{port}/docs/alpha"

    await runner.cleanup()


async def _run_single(url: str, output_dir: Path, output: dict[str, object]) -> None:
    cfg = DocpullConfig(
        url=url,
        output={"directory": output_dir, **output},
        crawl={"max_pages": 1, "streaming_discovery": False},
    )
    async with Fetcher(cfg) as fetcher:
        await fetcher.fetch_one(url)


@pytest.mark.asyncio
async def test_json_output_schema_local_server(output_server: str, tmp_path: Path) -> None:
    await _run_single(output_server, tmp_path, {"format": "json"})

    data = json.loads((tmp_path / "documents.json").read_text())
    assert data["document_count"] == 1
    record = data["documents"][0]
    assert record["url"] == output_server
    assert record["document_id"].startswith("doc_")
    assert record["content_hash"]

    manifest = json.loads((tmp_path / "corpus.manifest.json").read_text())
    assert manifest["output_format"] == "json"
    assert manifest["document_count"] == 1
    assert manifest["records"][0]["document_id"] == record["document_id"]


@pytest.mark.asyncio
async def test_ndjson_chunk_output_local_server(output_server: str, tmp_path: Path) -> None:
    await _run_single(
        output_server,
        tmp_path,
        {
            "format": "ndjson",
            "emit_chunks": True,
            "max_tokens_per_file": 100,
            "ndjson_filename": "docs.ndjson",
        },
    )

    lines = [json.loads(line) for line in (tmp_path / "docs.ndjson").read_text().splitlines()]
    assert lines
    assert all(line["chunk_id"].startswith("chunk_") for line in lines)

    manifest = json.loads((tmp_path / "corpus.manifest.json").read_text())
    assert manifest["output_format"] == "ndjson"
    assert manifest["chunk_count"] == len(lines)
    assert manifest["records"][0]["chunk_id"] == lines[0]["chunk_id"]


@pytest.mark.asyncio
async def test_sqlite_output_schema_local_server(output_server: str, tmp_path: Path) -> None:
    await _run_single(output_server, tmp_path, {"format": "sqlite"})

    conn = sqlite3.connect(tmp_path / "documents.db")
    try:
        row = conn.execute("SELECT url, content_hash FROM documents").fetchone()
    finally:
        conn.close()
    assert row == (output_server, row[1])
    assert row[1]

    manifest = json.loads((tmp_path / "corpus.manifest.json").read_text())
    assert manifest["output_format"] == "sqlite"
    assert manifest["record_count"] == 1
