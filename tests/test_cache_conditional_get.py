"""Integration test: conditional GET against a local aiohttp test server.

Verifies that ``Fetcher`` with ``cache.enabled=True``:

1. Sends ``If-None-Match`` / ``If-Modified-Since`` request headers on the
   second run when the manifest has an entry for the URL.
2. Treats a ``304 Not Modified`` response as a successful skip with
   ``SkipReason.CACHE_UNCHANGED``.
3. Re-fetches normally when the cached file is missing on disk
   (so a user clearing the output dir doesn't end up with an empty mirror).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from aiohttp import web

from docpull.core.fetcher import Fetcher
from docpull.models.config import DocpullConfig
from docpull.models.events import EventType, SkipReason
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidator

PAGE_HTML = b"""<!doctype html><html><body><article>
<h1>Hello</h1><p>Cached page</p>
</article></body></html>"""


def _make_resolver(server_host: str, server_port: int):
    """Resolver that maps any incoming hostname to the test server."""

    def resolve(hostname: str) -> list[str]:
        return [server_host]

    return resolve


@pytest.fixture
async def server(monkeypatch):
    """aiohttp server that serves PAGE_HTML with a stable ETag and honors
    ``If-None-Match`` by responding 304."""
    request_log: list[dict[str, str]] = []

    async def handler(request: web.Request) -> web.Response:
        request_log.append(dict(request.headers))
        etag = '"abc123"'
        if_none_match = request.headers.get("If-None-Match")
        if_modified_since = request.headers.get("If-Modified-Since")
        # Either header counts: If-None-Match is the priority signal but
        # If-Modified-Since alone is also valid per RFC 9110 §13.1.3.
        if if_none_match == etag or if_modified_since:
            return web.Response(status=304, headers={"ETag": etag})
        return web.Response(
            body=PAGE_HTML,
            content_type="text/html",
            headers={"ETag": etag, "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"},
        )

    async def robots(_request: web.Request) -> web.Response:
        # Empty robots: allow everything.
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

    # docpull blocks loopback IPs at validation time. For this test we patch
    # both the SSRF blocklist and the resolver so the validator accepts our
    # 127.0.0.1 server. Production code is unchanged.
    def permissive_validate(self, hostname):  # type: ignore[no-untyped-def]
        # Any hostname is acceptable for these tests; the test server is
        # bound to a single localhost port so there's no real SSRF risk.
        from docpull.security.url_validator import UrlValidationResult
        return UrlValidationResult.valid()

    monkeypatch.setattr(UrlValidator, "validate_hostname", permissive_validate)

    # Also override scheme to allow http (test server is plain HTTP).
    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)

    # Bypass robots.txt — the production checker enforces HTTPS-only.
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)

    yield {
        "url": f"http://127.0.0.1:{port}/page",
        "request_log": request_log,
    }

    await runner.cleanup()


async def _run(config: DocpullConfig) -> tuple[int, list[SkipReason | None]]:
    """Run the fetcher and return (pages_fetched, [skip_reasons])."""
    skip_reasons: list[SkipReason | None] = []
    async with Fetcher(config) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_SKIPPED:
                skip_reasons.append(event.skip_reason)
    return fetcher.stats.pages_fetched, skip_reasons


@pytest.mark.asyncio
async def test_conditional_get_returns_304_on_second_run(
    server, tmp_path: Path, monkeypatch
):
    """Second `--cache` run sends If-None-Match and gets 304."""
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    cfg = DocpullConfig(
        url=server["url"],
        output={"directory": output_dir},
        cache={"enabled": True, "directory": cache_dir, "skip_unchanged": True},
        crawl={"max_pages": 1, "max_depth": 1},
    )

    # Run 1: full fetch, populates manifest.
    fetched1, _ = await _run(cfg)
    assert fetched1 == 1
    saved = list(output_dir.glob("*.md"))
    assert saved, f"expected a saved markdown file in {output_dir}"

    # Reset the request log before the second run.
    server["request_log"].clear()

    # Run 2: should send If-None-Match and skip on 304.
    fetched2, skip_reasons = await _run(cfg)
    assert fetched2 == 0
    assert SkipReason.CACHE_UNCHANGED in skip_reasons

    # Confirm the request actually carried the conditional header.
    headers_seen = [
        h.get("If-None-Match") for h in server["request_log"] if "If-None-Match" in h
    ]
    assert headers_seen, "expected at least one request bearing If-None-Match"
    assert headers_seen[0] == '"abc123"'


@pytest.mark.asyncio
async def test_missing_output_file_forces_full_fetch(
    server, tmp_path: Path, monkeypatch
):
    """If the user wipes the output dir, the cache must NOT cause a 304-skip."""
    output_dir = tmp_path / "out"
    cache_dir = tmp_path / "cache"
    cfg = DocpullConfig(
        url=server["url"],
        output={"directory": output_dir},
        cache={"enabled": True, "directory": cache_dir, "skip_unchanged": True},
        crawl={"max_pages": 1, "max_depth": 1},
    )

    # Populate cache.
    await _run(cfg)
    saved = list(output_dir.glob("*.md"))
    assert saved, f"expected a saved markdown file in {output_dir}"
    saved_path = saved[0]

    # Wipe the output file but keep the cache.
    saved_path.unlink()
    server["request_log"].clear()

    fetched, skip_reasons = await _run(cfg)
    # Should re-fetch fully — no 304 path.
    assert fetched == 1
    assert SkipReason.CACHE_UNCHANGED not in skip_reasons
    # And no conditional header should have been sent.
    no_ifmatch = [
        h for h in server["request_log"] if "If-None-Match" not in h
    ]
    assert no_ifmatch, "expected at least one unconditional request"
