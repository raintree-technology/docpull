"""End-to-end benchmark: 10,000 synthetic pages over localhost HTTP.

Numbers this benchmark produces are the ones we will publish in the
README under "Performance." They cover what the plan calls the
falsifiable scaling claim:

- Wall time (full crawl, sitemap-driven)
- Peak RSS delta from baseline (via stdlib resource.getrusage)
- Manifest size on disk after the run
- Per-page latency p50/p95/p99 (from FetchEvent timestamps)
- Dedup rate (we inject 5% duplicate content)
- Discovery time vs fetch time split

This is gated behind `DOCPULL_BENCHMARK_10K=1` because it takes 30-60s
and stands up a localhost aiohttp server. CI nightly should set the
env var; local `pytest tests/` should skip it.
"""

from __future__ import annotations

import json
import os
import resource
import statistics
import sys
import time
from pathlib import Path

import pytest
from aiohttp import web

from docpull.core.fetcher import Fetcher
from docpull.models.config import DocpullConfig, ProfileName
from docpull.models.events import EventType
from docpull.security.robots import RobotsChecker
from docpull.security.url_validator import UrlValidator

PAGE_COUNT = 10_000
DUPLICATE_FRACTION = 0.05  # 5% pages are content-duplicates of another page

pytestmark = pytest.mark.skipif(
    os.environ.get("DOCPULL_BENCHMARK_10K") != "1",
    reason="set DOCPULL_BENCHMARK_10K=1 to run the 10k-page benchmark",
)


# Generate pseudo-realistic page bodies. Cheap (no Faker dep): repeat a
# small library of paragraphs keyed on the page index so output is
# deterministic across runs.
_PARAGRAPHS = [
    (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
    ),
    (
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco "
        "laboris nisi ut aliquip ex ea commodo consequat."
    ),
    (
        "Duis aute irure dolor in reprehenderit in voluptate velit esse "
        "cillum dolore eu fugiat nulla pariatur."
    ),
    (
        "Excepteur sint occaecat cupidatat non proident, sunt in culpa "
        "qui officia deserunt mollit anim id est laborum."
    ),
]


def _body_for(index: int) -> str:
    """Deterministic body for page `index`, using a rotating paragraph mix."""
    paras = "\n".join(
        f"<p>{_PARAGRAPHS[(index + i) % len(_PARAGRAPHS)]} (page {index})</p>"
        for i in range(4)
    )
    return (
        f"<!doctype html><html><head>"
        f"<title>Page {index}</title>"
        f'<meta name="description" content="Synthetic doc page {index}">'
        f"</head><body><article>"
        f"<h1>Page {index}</h1>"
        f"<h2>Overview</h2>{paras}"
        f"<h2>Details</h2>{paras}"
        f"</article></body></html>"
    )


def _peak_rss_bytes() -> int:
    """Process peak RSS in bytes. macOS ru_maxrss is bytes, Linux is KiB."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)


@pytest.fixture
async def server(monkeypatch):
    """aiohttp server that serves PAGE_COUNT pages plus sitemap/robots."""
    # Map duplicate URLs to the canonical body. 5% of pages will share
    # body bytes with another page so streaming dedup can show its work.
    dup_step = int(1 / DUPLICATE_FRACTION)
    canonical_for: dict[int, int] = {
        i: (i - 1) for i in range(1, PAGE_COUNT) if i % dup_step == 0
    }

    async def page_handler(request: web.Request) -> web.Response:
        index = int(request.match_info["index"])
        canonical = canonical_for.get(index, index)
        return web.Response(
            body=_body_for(canonical),
            content_type="text/html",
            headers={"ETag": f'"page-{canonical}"'},
        )

    async def sitemap_handler(_request: web.Request) -> web.Response:
        # Single sitemap with all 10k URLs.
        host = "http://127.0.0.1:" + str(_request.transport.get_extra_info("sockname")[1])
        urls = "\n".join(
            f"<url><loc>{host}/page/{i}</loc></url>" for i in range(PAGE_COUNT)
        )
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{urls}</urlset>"
        )
        return web.Response(text=body, content_type="application/xml")

    async def robots_handler(_request: web.Request) -> web.Response:
        return web.Response(text="", content_type="text/plain")

    app = web.Application()
    app.router.add_get("/page/{index}", page_handler)
    app.router.add_get("/sitemap.xml", sitemap_handler)
    app.router.add_get("/robots.txt", robots_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    # Same monkeypatches as test_cache_conditional_get: allow http://127.0.0.1.
    def permissive_validate(self, hostname):
        from docpull.security.url_validator import UrlValidationResult

        return UrlValidationResult.valid()

    monkeypatch.setattr(UrlValidator, "validate_hostname", permissive_validate)
    original_init = UrlValidator.__init__

    def init_with_http(self, *args, **kwargs):
        kwargs["allowed_schemes"] = {"http", "https"}
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(UrlValidator, "__init__", init_with_http)
    monkeypatch.setattr(RobotsChecker, "is_allowed", lambda self, url: True)
    monkeypatch.setattr(RobotsChecker, "get_sitemaps", lambda self, url: [f"http://127.0.0.1:{port}/sitemap.xml"])
    monkeypatch.setattr(RobotsChecker, "get_crawl_delay", lambda self, url: None)

    yield {"port": port, "base": f"http://127.0.0.1:{port}"}

    await runner.cleanup()


@pytest.mark.asyncio
async def test_10k_pages_end_to_end(server, tmp_path: Path) -> None:
    """Crawl 10,000 synthetic pages and report the headline numbers."""
    rss_baseline = _peak_rss_bytes()

    cfg = DocpullConfig(
        url=server["base"] + "/",
        profile=ProfileName.RAG,
        output={"directory": tmp_path / "out", "naming_strategy": "hierarchical"},
        cache={"enabled": True, "directory": tmp_path / "cache", "skip_unchanged": True},
        crawl={"max_concurrent": 50, "rate_limit": 0.0, "max_pages": PAGE_COUNT},
    )

    discovery_started: float | None = None
    discovery_complete: float | None = None
    page_save_times: list[float] = []
    first_save_time: float | None = None
    duplicates_seen = 0
    t0 = time.monotonic()

    async with Fetcher(cfg) as fetcher:
        async for event in fetcher.run():
            now = time.monotonic()
            if event.type == EventType.DISCOVERY_STARTED:
                discovery_started = now
            elif event.type == EventType.DISCOVERY_COMPLETE:
                discovery_complete = now
            elif event.type == EventType.PAGE_SAVED:
                page_save_times.append(now)
                if first_save_time is None:
                    first_save_time = now - t0
            elif event.type == EventType.PAGE_DEDUPLICATED:
                duplicates_seen += 1

    wall = time.monotonic() - t0
    rss_peak = _peak_rss_bytes()

    # Per-page latency = inter-arrival time of PAGE_SAVED events.
    deltas = [
        (page_save_times[i] - page_save_times[i - 1]) * 1000
        for i in range(1, len(page_save_times))
    ]
    p50 = statistics.median(deltas) if deltas else 0.0
    p95 = (
        statistics.quantiles(deltas, n=20)[18] if len(deltas) >= 20 else max(deltas, default=0.0)
    )
    p99 = (
        statistics.quantiles(deltas, n=100)[98] if len(deltas) >= 100 else max(deltas, default=0.0)
    )

    manifest_path = tmp_path / "cache" / "manifest.json"
    manifest_size = manifest_path.stat().st_size if manifest_path.exists() else 0

    expected_unique = PAGE_COUNT - int(PAGE_COUNT * DUPLICATE_FRACTION)
    discovery_secs = (
        (discovery_complete - discovery_started)
        if discovery_started and discovery_complete
        else 0.0
    )

    report = {
        "pages_total": PAGE_COUNT,
        "pages_fetched": fetcher.stats.pages_fetched,
        "pages_skipped": fetcher.stats.pages_skipped,
        "pages_failed": fetcher.stats.pages_failed,
        "duplicates_detected": duplicates_seen,
        "expected_unique_pages": expected_unique,
        "wall_seconds": round(wall, 2),
        "discovery_seconds": round(discovery_secs, 2),
        "fetch_seconds": round(wall - discovery_secs, 2),
        "time_to_first_save_seconds": round(first_save_time or 0.0, 3),
        "rss_baseline_mb": round(rss_baseline / (1024 * 1024), 1),
        "rss_peak_mb": round(rss_peak / (1024 * 1024), 1),
        "rss_delta_mb": round((rss_peak - rss_baseline) / (1024 * 1024), 1),
        "manifest_kb": round(manifest_size / 1024, 1),
        "p50_inter_save_ms": round(p50, 2),
        "p95_inter_save_ms": round(p95, 2),
        "p99_inter_save_ms": round(p99, 2),
    }

    # Emit as JSON so CI can ingest it and trend the numbers over time.
    print("\nDOCPULL_10K_BENCHMARK_REPORT=" + json.dumps(report))

    # Hard floors: regressions worth failing CI on.
    # Fetched + skipped (dedup hits skip with should_skip) should equal total.
    assert (
        fetcher.stats.pages_fetched + fetcher.stats.pages_skipped == PAGE_COUNT
    ), report
    # Dedup detected something close to the injected rate.
    assert duplicates_seen >= int(PAGE_COUNT * DUPLICATE_FRACTION * 0.9), report
    # Memory ceiling: fail if we burn more than 200 MiB on this workload.
    # Real number on a clean run should land well under 100 MiB.
    assert (
        rss_peak - rss_baseline
    ) < 200 * 1024 * 1024, f"RSS regression: {report}"
