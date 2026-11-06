"""Sitemap lastmod skipping, removal reporting, and incremental manifests."""

from __future__ import annotations

import html
import json
from datetime import timedelta
from pathlib import Path

import pytest
from aiohttp import web

from docpull.cache import CacheManager
from docpull.core.fetcher import Fetcher
from docpull.discovery.sitemap import normalize_lastmod
from docpull.http.rate_limiter import PerHostRateLimiter
from docpull.models.config import DocpullConfig
from docpull.models.events import EventType, SkipReason
from docpull.pack_tools import diff_packs
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidationResult, UrlValidator
from docpull.time_utils import utc_now

BODY = "<!doctype html><html><body><article><h1>{0}</h1><p>Text for {0}.</p></article></body></html>"


class Site:
    def __init__(self) -> None:
        self.pages: dict[str, str | None] = {f"/p{i}": "2026-01-01" for i in range(6)}
        self.sitemap_status = 200
        self.requests: list[str] = []
        self.etag: str | None = None
        self.base = ""

    def sitemap(self) -> str:
        rows = "".join(
            f"<url><loc>{self.base}{path}</loc>" + (f"<lastmod>{date}</lastmod>" if date else "") + "</url>"
            for path, date in self.pages.items()
        )
        return (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{rows}</urlset>"
        )


@pytest.fixture
async def site(monkeypatch):
    state = Site()

    async def sitemap(_request: web.Request) -> web.Response:
        if state.sitemap_status != 200:
            return web.Response(status=state.sitemap_status)
        return web.Response(text=state.sitemap(), content_type="application/xml")

    async def page(request: web.Request) -> web.Response:
        state.requests.append(request.path)
        if state.etag and request.headers.get("If-None-Match") == state.etag:
            return web.Response(status=304, headers={"ETag": state.etag})
        headers = {"ETag": state.etag} if state.etag else {}
        return web.Response(
            text=BODY.format(html.escape(request.path)), content_type="text/html", headers=headers
        )

    app = web.Application()
    app.router.add_get("/sitemap.xml", sitemap)
    app.router.add_get("/{name}", page)
    runner = web.AppRunner(app)
    await runner.setup()
    tcp = web.TCPSite(runner, "127.0.0.1", 0)
    await tcp.start()
    state.base = f"http://127.0.0.1:{tcp._server.sockets[0].getsockname()[1]}"  # type: ignore[union-attr]

    monkeypatch.setattr(UrlValidator, "validate_hostname", lambda self, host: UrlValidationResult.valid())
    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)
    yield state
    await runner.cleanup()


def config(site: Site, tmp_path: Path, *, fmt: str = "markdown", **cache: object) -> DocpullConfig:
    return DocpullConfig(
        url=f"{site.base}/sitemap.xml",
        output={"directory": tmp_path / "out", "format": fmt},
        cache={"enabled": True, "directory": tmp_path / "cache", **cache},
        crawl={"rate_limit": 0},
    )


async def run(cfg: DocpullConfig) -> tuple[Fetcher, list[SkipReason | None]]:
    skips: list[SkipReason | None] = []
    async with Fetcher(cfg) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_SKIPPED:
                skips.append(event.skip_reason)
    return fetcher, skips


def manifest(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "cache" / "manifest.json").read_text())


def set_verified(tmp_path: Path, days_ago: int) -> None:
    data = manifest(tmp_path)
    when = (utc_now() - timedelta(days=days_ago)).isoformat()
    for entry in data.values():
        entry["fetched_at"] = when
    (tmp_path / "cache" / "manifest.json").write_text(json.dumps(data))


def corpus_urls(tmp_path: Path) -> set[str]:
    data = json.loads((tmp_path / "out" / "corpus.manifest.json").read_text())
    return {record["url"] for record in data["records"]}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-02", "2026-01-02T00:00:00+00:00"),
        ("2026-01-02T01:00:00+01:00", "2026-01-02T00:00:00+00:00"),
        ("2026-01-02T00:00:00Z", "2026-01-02T00:00:00+00:00"),
        ("not a date", None),
        (None, None),
    ],
)
def test_normalize_lastmod(raw, expected):
    assert normalize_lastmod(raw) == expected


@pytest.mark.asyncio
async def test_unchanged_lastmod_sends_no_requests(site, tmp_path):
    cfg = config(site, tmp_path)
    await run(cfg)
    assert len(site.requests) == 6
    site.requests.clear()

    fetcher, skips = await run(cfg)

    assert site.requests == []
    assert fetcher.stats.pages_fetched == 0
    assert skips == [SkipReason.CACHE_UNCHANGED] * 6
    assert len(corpus_urls(tmp_path)) == 6


@pytest.mark.asyncio
async def test_changed_and_new_pages_are_fetched(site, tmp_path):
    cfg = config(site, tmp_path)
    await run(cfg)
    site.requests.clear()
    site.pages["/p0"] = "2026-02-01"
    site.pages["/new"] = "2026-02-01"

    fetcher, _ = await run(cfg)

    assert sorted(site.requests) == ["/new", "/p0"]
    assert fetcher.stats.pages_fetched == 2
    assert len(corpus_urls(tmp_path)) == 7


@pytest.mark.asyncio
async def test_missing_local_file_forces_fetch(site, tmp_path):
    cfg = config(site, tmp_path)
    await run(cfg)
    site.requests.clear()
    Path(manifest(tmp_path)[f"{site.base}/p0"]["file_path"]).unlink()

    await run(cfg)

    assert site.requests == ["/p0"]


@pytest.mark.asyncio
async def test_page_without_lastmod_falls_back_to_conditional_get(site, tmp_path):
    site.pages["/p0"] = None
    site.etag = '"v1"'
    cfg = config(site, tmp_path)
    await run(cfg)
    site.requests.clear()

    _, skips = await run(cfg)

    assert site.requests == ["/p0"]
    assert skips.count(SkipReason.CACHE_UNCHANGED) == 6


@pytest.mark.asyncio
async def test_max_age_revalidates_then_trusts_lastmod_again(site, tmp_path):
    site.etag = '"v1"'
    cfg = config(site, tmp_path, max_age_days=7)
    await run(cfg)
    set_verified(tmp_path, 8)
    site.requests.clear()

    await run(cfg)
    assert len(site.requests) == 6  # past max-age: every page revalidated
    site.requests.clear()

    await run(cfg)
    assert site.requests == []  # the 304s refreshed verified_at


@pytest.mark.asyncio
async def test_max_age_zero_and_no_skip_unchanged_disable_skipping(site, tmp_path):
    for options in ({"max_age_days": 0}, {"skip_unchanged": False}):
        cfg = config(site, tmp_path, **options)
        await run(cfg)
        site.requests.clear()
        await run(cfg)
        assert len(site.requests) == 6
        site.requests.clear()


@pytest.mark.asyncio
async def test_aggregate_output_is_never_lastmod_skipped(site, tmp_path):
    cfg = config(site, tmp_path, fmt="ndjson")
    await run(cfg)
    site.requests.clear()

    await run(cfg)

    assert len(site.requests) == 6


@pytest.mark.asyncio
async def test_removed_url_is_reported_and_file_kept_by_default(site, tmp_path):
    cfg = config(site, tmp_path)
    await run(cfg)
    url = f"{site.base}/p5"
    saved = Path(manifest(tmp_path)[url]["file_path"])
    del site.pages["/p5"]

    fetcher, _ = await run(cfg)

    assert fetcher.stats.removed_urls == [url]
    assert fetcher.stats.pruned_urls == []
    assert saved.exists()


@pytest.mark.asyncio
async def test_prune_removed_deletes_file_and_entry(site, tmp_path):
    cfg = config(site, tmp_path, prune_removed=True)
    await run(cfg)
    url = f"{site.base}/p5"
    saved = Path(manifest(tmp_path)[url]["file_path"])
    del site.pages["/p5"]

    fetcher, _ = await run(cfg)

    assert fetcher.stats.pruned_urls == [url]
    assert not saved.exists()
    assert url not in manifest(tmp_path)


@pytest.mark.asyncio
async def test_unreadable_sitemap_reports_nothing_removed(site, tmp_path):
    cfg = config(site, tmp_path, prune_removed=True)
    await run(cfg)
    site.sitemap_status = 500

    fetcher, _ = await run(cfg)

    assert fetcher.stats.removed_urls == []


@pytest.mark.asyncio
async def test_prune_refuses_when_most_urls_vanish(site, tmp_path):
    site.pages = {f"/p{i}": "2026-01-01" for i in range(12)}
    cfg = config(site, tmp_path, prune_removed=True)
    await run(cfg)
    files = [Path(entry["file_path"]) for entry in manifest(tmp_path).values()]
    site.pages = {f"/p{i}": "2026-01-01" for i in range(5)}

    fetcher, _ = await run(cfg)

    assert len(fetcher.stats.removed_urls) == 7
    assert fetcher.stats.pruned_urls == []
    assert all(path.exists() for path in files)


def test_verified_entries_outlive_ttl(tmp_path):
    cache = CacheManager(tmp_path, ttl_days=30)
    cache.update_cache("https://a.test/x", "body", tmp_path / "x.md")
    cache.manifest["https://a.test/x"]["fetched_at"] = (utc_now() - timedelta(days=40)).isoformat()
    cache.mark_verified("https://a.test/x")
    assert cache.evict_expired() == 0


def test_shorten_next_delay_respects_explicit_host_limits():
    limiter = PerHostRateLimiter(default_delay=1.0, host_configs={"slow.test": {"delay": 5.0}})
    limiter._last_request.update({"fast.test": 100.0, "slow.test": 100.0})

    limiter.shorten_next_delay("https://fast.test/a", 0.1)
    limiter.shorten_next_delay("https://slow.test/a", 0.1)

    assert limiter._last_request["fast.test"] == pytest.approx(99.1)
    assert limiter._last_request["slow.test"] == 100.0


def test_diff_packs_reads_markdown_pack(tmp_path):
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "a.md").write_text("# A\n", encoding="utf-8")
    (old / "corpus.manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "document_id": "d1",
                        "url": "https://a.test/",
                        "title": "A",
                        "content_hash": "h1",
                        "output_path": "a.md",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    record = {
        "document_id": "d2",
        "url": "https://a.test/",
        "title": "A",
        "content_hash": "h2",
        "content": "B",
    }
    (new / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")

    assert diff_packs(old, new)["changed_urls"] == ["https://a.test/"]
