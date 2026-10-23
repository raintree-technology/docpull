"""Regression tests for the fetch run lifecycle (see docs/formal/fetch-pipeline.md)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from docpull import DocpullConfig, EventType, Fetcher
from docpull.cache import CacheManager
from docpull.pipeline.base import FetchPipeline, PageContext
from docpull.pipeline.steps import FetchStep as HttpFetchStep  # noqa: F401  (real step, patched client)

START = "https://example.com/"


class _Discoverer:
    def __init__(self, urls, fail_after: int | None = None):
        self.urls = urls
        self.fail_after = fail_after

    async def discover(self, start_url, max_urls=None):
        for i, url in enumerate(self.urls):
            if self.fail_after is not None and i >= self.fail_after:
                raise RuntimeError("discovery boom")
            await asyncio.sleep(0)
            yield url


class _OkStep:
    name = "ok"

    def __init__(self, on_url=None):
        self.on_url = on_url

    async def execute(self, ctx: PageContext, emit=None) -> PageContext:
        if self.on_url:
            self.on_url(ctx.url)
        await asyncio.sleep(0)
        ctx.markdown = "# hi"
        return ctx


def _fetcher(tmp_path: Path, urls, *, steps=None, cache=False, concurrent=2, fail_after=None, **crawl):
    cfg = DocpullConfig(
        url=START,
        output={"directory": tmp_path / "out"},
        cache={"enabled": cache, "directory": tmp_path / "cache", "resume": False},
        crawl={"max_concurrent": concurrent, "streaming_discovery": True, **crawl},
    )
    f = Fetcher(cfg)
    f._pipeline = FetchPipeline(steps=steps or [_OkStep()])
    f._discoverer = _Discoverer(urls, fail_after)
    if cache:
        f._cache_manager = CacheManager(cache_dir=tmp_path / "cache")
    return f


async def _collect(fetcher: Fetcher, timeout: float = 5.0, on_event=None):
    events = []

    async def go():
        async for ev in fetcher.run():
            events.append(ev)
            if on_event:
                on_event(ev)

    await asyncio.wait_for(go(), timeout)
    return events


@pytest.mark.asyncio
async def test_cancel_with_full_queue_does_not_hang(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(60)]
    holder: dict = {}
    f = _fetcher(tmp_path, urls, concurrent=2, steps=[_OkStep(on_url=lambda u: holder["f"].cancel())])
    holder["f"] = f
    events = await _collect(f)
    assert events[-1].type == EventType.CANCELLED


@pytest.mark.asyncio
async def test_discovery_error_ends_run_with_failed_event(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(5)]
    f = _fetcher(tmp_path, urls, fail_after=2)
    events = []
    with pytest.raises(RuntimeError, match="discovery boom"):
        await asyncio.wait_for(_drain(f, events), 5)
    assert events[-1].type == EventType.FAILED


async def _drain(fetcher, events):
    async for ev in fetcher.run():
        events.append(ev)


@pytest.mark.asyncio
async def test_cancelled_streaming_run_keeps_unfinished_urls_resumable(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(30)]
    holder: dict = {}
    f = _fetcher(
        tmp_path, urls, cache=True, concurrent=1, steps=[_OkStep(on_url=lambda u: holder["f"].cancel())]
    )
    holder["f"] = f
    await _collect(f)
    pending = f._cache_manager.frontier.pending_urls()
    assert pending, "cancelled run must leave unfinished URLs pending for --resume"


class _RaisingFetch:
    """Step that emits FETCH_FAILED itself, then raises (like FetchStep / SaveStep)."""

    name = "boom"

    async def execute(self, ctx, emit=None):
        from docpull.models.events import EventType, FetchEvent

        if emit:
            emit(FetchEvent(type=EventType.FETCH_FAILED, url=ctx.url, error="x"))
        raise RuntimeError("x")


@pytest.mark.asyncio
async def test_failed_url_emits_exactly_one_fetch_failed(tmp_path):
    f = _fetcher(tmp_path, ["https://example.com/a"], steps=[_RaisingFetch()])
    events = await _collect(f)
    failed = [e for e in events if e.type == EventType.FETCH_FAILED]
    assert len(failed) == 1
    assert f.stats.pages_failed == 1
