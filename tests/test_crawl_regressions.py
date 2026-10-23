"""Regression tests for bugs found by the crawl/politeness model (tests/test_model_crawl_politeness.py)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from docpull.cache.manager import CacheManager
from docpull.discovery.crawler import LinkCrawler
from docpull.http.rate_limiter import PerHostRateLimiter

FP = {"max_pages": 5}
START = "https://example.com"


def test_streaming_first_snapshot_keeps_urls_finished_before_it(tmp_path):
    """Streaming workers finish pages before the first discovery snapshot is written.

    The snapshot must not reset those pages to queued, or resume refetches them.
    """
    cache = CacheManager(tmp_path)
    cache.frontier.mark_processing("https://example.com/a")
    cache.mark_fetched("https://example.com/a")

    cache.save_discovered_urls(["https://example.com/a", "https://example.com/b"], START, FP)

    assert cache.get_pending_urls(START, FP) == ["https://example.com/b"]


def test_frontier_still_resets_for_a_different_crawl(tmp_path):
    cache = CacheManager(tmp_path)
    cache.save_discovered_urls(["https://example.com/a"], START, FP)
    cache.mark_fetched("https://example.com/a")

    other = CacheManager(tmp_path)
    other.save_discovered_urls(["https://other.com/x"], "https://other.com", FP)

    assert other.get_pending_urls("https://other.com", FP) == ["https://other.com/x"]


def _html_client(fetched: list[str]) -> MagicMock:
    client = MagicMock()

    async def get(url: str, **_: object) -> MagicMock:
        fetched.append(url)
        response = MagicMock()
        response.status_code = 200
        response.content = b'<a href="/next">n</a>'
        response.content_type = "text/html"
        response.url = url
        return response

    client.get = AsyncMock(side_effect=get)
    return client


async def test_crawler_never_fetches_robots_disallowed_start_url():
    fetched: list[str] = []
    validator = MagicMock()
    validator.is_valid.return_value = True
    robots = MagicMock()
    robots.is_allowed.return_value = False

    crawler = LinkCrawler(_html_client(fetched), validator, robots, max_depth=2)
    urls = [u async for u in crawler.discover(START)]

    assert urls == []
    assert fetched == []


async def test_crawler_never_fetches_invalid_start_url():
    fetched: list[str] = []
    validator = MagicMock()
    validator.is_valid.return_value = False
    robots = MagicMock()
    robots.is_allowed.return_value = True

    crawler = LinkCrawler(_html_client(fetched), validator, robots, max_depth=2)
    assert [u async for u in crawler.discover(START)] == []
    assert fetched == []


async def test_rate_limiter_treats_host_case_as_one_host():
    limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    inside = 0
    peak = 0

    async def hit(url: str) -> None:
        nonlocal inside, peak
        async with limiter.limit(url):
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.01)
            inside -= 1

    await asyncio.gather(hit("https://Docs.Example.com/a"), hit("https://docs.example.com/b"))

    assert peak == 1


def test_robots_crawl_delay_applies_whatever_the_host_case():
    limiter = PerHostRateLimiter(default_delay=0.5)
    limiter.update_host_config("Docs.Example.com", delay=5.0)

    assert limiter._get_config(limiter._get_host("https://docs.example.com/x")) == (5.0, 3)


async def test_adaptive_limiter_never_drops_below_robots_crawl_delay():
    from docpull.http.rate_limiter import AdaptiveRateLimiter

    url = "https://example.com/p"
    limiter = AdaptiveRateLimiter(default_delay=0.5, success_threshold=2, min_delay=0.1)
    limiter.update_host_config("example.com", delay=5.0)  # robots.txt Crawl-delay

    for _ in range(10):
        await limiter.record_success(url)
    assert limiter._get_config("example.com")[0] >= 5.0

    await limiter.record_rate_limit(url, retry_after=1)
    assert limiter._get_config("example.com")[0] >= 5.0


async def test_sitemap_index_cycle_is_fetched_once_per_sitemap():
    from docpull.discovery.sitemap import SitemapDiscoverer

    base = "https://example.com"
    loops = "".join(f"<sitemap><loc>{base}/s{i}.xml</loc></sitemap>" for i in range(3))
    index = f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{loops}</sitemapindex>'
    fetched: list[str] = []

    async def get(url: str, **_: object) -> MagicMock:
        fetched.append(url)
        response = MagicMock()
        response.status_code = 200
        response.content = index.encode()
        return response

    client = MagicMock()
    client.get = AsyncMock(side_effect=get)
    validator = MagicMock()
    validator.is_valid.return_value = True

    discoverer = SitemapDiscoverer(client, validator)
    assert [u async for u in discoverer.discover(f"{base}/s0.xml")] == []

    assert len(fetched) == len(set(fetched)) == 3


@pytest.mark.asyncio
async def test_redirect_to_robots_disallowed_target_is_not_fetched() -> None:
    from docpull.http.client import AsyncHttpClient
    from tests.test_security_hardening import _DummyRateLimiter, _FakeResponse, _FakeSession

    client = AsyncHttpClient(rate_limiter=_DummyRateLimiter())
    client.redirect_allowed = lambda url: "/private/" not in url
    client._session = _FakeSession(
        [_FakeResponse(302, headers={"Location": "/private/page"}, url="https://docs.example/start")]
    )

    with pytest.raises(ValueError, match="blocked by robots.txt"):
        await client.get("https://docs.example/start")

    assert len(client._session.calls) == 1
