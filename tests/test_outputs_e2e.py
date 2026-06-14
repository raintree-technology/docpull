"""Local end-to-end output format tests."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from aiohttp import web
from pydantic import ValidationError

from docpull import ProfileName, Scraper, scrape_one
from docpull.conversion.special_cases import _split_markdown_frontmatter
from docpull.core.fetcher import Fetcher
from docpull.models.config import DocpullConfig
from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.save_okf import OkfSaveStep
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


@pytest.fixture
async def user_agent_server(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, str | None] = {}

    async def page(request: web.Request) -> web.Response:
        seen["user_agent"] = request.headers.get("User-Agent")
        return web.Response(body=PAGE_HTML, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", page)
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

    yield f"http://127.0.0.1:{port}/", seen

    await runner.cleanup()


async def _run_single(url: str, output_dir: Path, output: dict[str, object]) -> None:
    cfg = DocpullConfig(
        url=url,
        output={"directory": output_dir, **output},
        crawl={"max_pages": 1, "streaming_discovery": False},
    )
    async with Fetcher(cfg) as fetcher:
        await fetcher.fetch_one(url)


def assert_okf_bundle_conformant(bundle_dir: Path) -> None:
    """Assert the generated bundle meets OKF v0.1's hard conformance rules."""
    concept_count = 0
    for path in bundle_dir.rglob("*.md"):
        rel = path.relative_to(bundle_dir).as_posix()
        text = path.read_text(encoding="utf-8")
        frontmatter, body = _split_markdown_frontmatter(text)
        if path.name == "index.md":
            if rel == "index.md" and frontmatter is not None:
                data = yaml.safe_load(frontmatter)
                assert data == {"okf_version": "0.1"}
                assert body.strip()
            else:
                assert frontmatter is None
                assert text.strip()
            continue
        if path.name == "log.md":
            assert frontmatter is None
            assert text.startswith("# ")
            continue

        concept_count += 1
        assert frontmatter is not None, rel
        data = yaml.safe_load(frontmatter)
        assert isinstance(data, dict), rel
        assert isinstance(data.get("type"), str) and data["type"].strip(), rel
        assert body.strip(), rel

    assert concept_count > 0


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
async def test_markdown_output_writes_manifest(output_server: str, tmp_path: Path) -> None:
    await _run_single(output_server, tmp_path, {"format": "markdown"})

    manifest = json.loads((tmp_path / "corpus.manifest.json").read_text())

    assert manifest["output_format"] == "markdown"
    assert manifest["document_count"] == 1
    assert manifest["records"][0]["output_path"] == "index.md"
    assert manifest["records"][0]["content_hash"]


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
    assert manifest["records"][0]["output_path"] == "docs.ndjson"


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


@pytest.mark.asyncio
async def test_okf_output_bundle_local_server(output_server: str, tmp_path: Path) -> None:
    await _run_single(output_server, tmp_path, {"format": "okf"})

    concept_path = tmp_path / "_root.md"
    concept = concept_path.read_text()
    assert concept.startswith("---\n")
    frontmatter = yaml.safe_load(concept.split("---", 2)[1])
    assert frontmatter["type"] == "Documentation Page"
    assert frontmatter["title"] == "Alpha Docs"
    assert frontmatter["resource"] == output_server
    assert frontmatter["source"] == output_server
    assert "# Alpha" in concept

    root_index = (tmp_path / "index.md").read_text()
    assert root_index.startswith('---\nokf_version: "0.1"\n---')
    assert "* [Alpha Docs](_root.md)" in root_index

    manifest = json.loads((tmp_path / "corpus.manifest.json").read_text())
    assert manifest["output_format"] == "okf"
    assert manifest["record_count"] == 1
    assert manifest["records"][0]["output_path"] == "_root.md"
    assert_okf_bundle_conformant(tmp_path)


@pytest.mark.asyncio
async def test_okf_indexes_include_nested_directories(tmp_path: Path) -> None:
    step = OkfSaveStep(tmp_path)
    ctx = PageContext(
        url="https://docs.example.com/api/",
        output_path=tmp_path / "api" / "_page.md",
    )
    ctx.title = "API"
    ctx.markdown = "# API\n\nBody."

    await step.execute(ctx)
    step.finalize()

    root_index = (tmp_path / "index.md").read_text()
    nested_index = (tmp_path / "api" / "index.md").read_text()

    assert root_index.startswith('---\nokf_version: "0.1"\n---')
    assert "* [api](api/) - 1 concept" in root_index
    assert not nested_index.startswith("---")
    assert "* [API](_page.md)" in nested_index
    assert_okf_bundle_conformant(tmp_path)


@pytest.mark.asyncio
async def test_scrape_one_api_returns_in_memory_markdown(output_server: str) -> None:
    result = await scrape_one(output_server)

    assert result.url == output_server
    assert result.title == "Alpha Docs"
    assert result.markdown
    assert "# Alpha" in result.text
    assert result.error is None
    assert result.skipped is False
    assert result.extraction["confidence"] > 0


@pytest.mark.asyncio
async def test_scrape_one_api_does_not_write_default_docs_dir(
    output_server: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    result = await scrape_one(output_server)

    assert result.markdown
    assert not (tmp_path / "docs").exists()


@pytest.mark.asyncio
async def test_scrape_one_api_ignores_existing_default_output_file(
    output_server: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.md").write_text("old content", encoding="utf-8")

    result = await scrape_one(output_server)

    assert result.skipped is False
    assert result.markdown
    assert "# Alpha" in result.markdown


@pytest.mark.asyncio
async def test_scrape_one_api_forwards_docpull_config_kwargs(
    user_agent_server: tuple[str, dict[str, str | None]],
) -> None:
    url, seen = user_agent_server

    result = await scrape_one(url, network={"user_agent": "docpull-test-agent"})

    assert result.markdown
    assert seen["user_agent"] == "docpull-test-agent"


@pytest.mark.asyncio
async def test_scraper_facade_writes_site_outputs(output_server: str, tmp_path: Path) -> None:
    scraper = Scraper()

    result = await scraper.scrape_site(
        output_server,
        output_dir=tmp_path,
        output_format="ndjson",
        max_pages=1,
    )

    assert result.stats.pages_fetched == 1
    assert result.manifest_path.exists()
    assert (tmp_path / "documents.ndjson").exists()


@pytest.mark.asyncio
async def test_scraper_facade_preserves_profile_output_defaults(output_server: str, tmp_path: Path) -> None:
    scraper = Scraper()

    result = await scraper.scrape_site(
        output_server,
        output_dir=tmp_path,
        profile=ProfileName.OKF,
        max_pages=1,
    )

    assert result.output_format == "okf"
    assert result.manifest_path.exists()
    assert (tmp_path / "_root.md").exists()


@pytest.mark.asyncio
async def test_scraper_facade_rejects_invalid_crawl_depth(output_server: str, tmp_path: Path) -> None:
    scraper = Scraper()

    with pytest.raises(ValidationError):
        await scraper.scrape_site(
            output_server,
            output_dir=tmp_path,
            max_depth=0,
        )
