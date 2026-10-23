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


async def _drain(fetcher, events):
    async for ev in fetcher.run():
        events.append(ev)


async def _run_or_hang(fetcher, timeout: float = 3.0):
    """Run to the end; report a hang as a failure (a timeout cancel can mask it as the run's own error)."""
    events: list = []
    task = asyncio.create_task(_drain(fetcher, events))
    done, _ = await asyncio.wait({task}, timeout=timeout)
    assert done, f"run hung after events {[e.type.value for e in events]}"
    return events, task.exception()


@pytest.mark.asyncio
async def test_discovery_error_ends_run_with_failed_event(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(5)]
    f = _fetcher(tmp_path, urls, fail_after=2)
    events, exc = await _run_or_hang(f)
    assert isinstance(exc, RuntimeError)
    assert events[-1].type == EventType.FAILED


@pytest.mark.asyncio
async def test_discovery_error_with_nothing_pending_does_not_leave_empty_resume(tmp_path):
    f = _fetcher(tmp_path, [f"https://example.com/{i}" for i in range(5)], fail_after=2, cache=True)
    _events, exc = await _run_or_hang(f)
    assert isinstance(exc, RuntimeError)
    pending = f._cache_manager.get_pending_urls(START, config_fingerprint=f._run_fingerprint())
    assert pending is None, "resume would report an empty run and never discover the rest"


class _Gate:
    name = "gate"

    def __init__(self):
        self.open = asyncio.Event()

    async def execute(self, ctx, emit=None):
        await self.open.wait()
        ctx.markdown = "# hi"
        return ctx


class _Flood:
    async def discover(self, start_url, max_urls=None):
        for i in range(60):
            yield f"https://example.com/{i}"


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrent", [1, 2, 4])
async def test_cancel_while_discoverer_blocked_on_full_queue(tmp_path, concurrent):
    gate = _Gate()
    f = _fetcher(tmp_path, [], steps=[gate], concurrent=concurrent)
    f._discoverer = _Flood()

    async def cancel_later():
        for _ in range(20):
            await asyncio.sleep(0)
        f.cancel()
        gate.open.set()

    asyncio.create_task(cancel_later())
    events, exc = await _run_or_hang(f)
    assert exc is None
    assert events[-1].type == EventType.CANCELLED


@pytest.mark.asyncio
async def test_worker_bookkeeping_error_fails_run_instead_of_hanging(tmp_path):
    f = _fetcher(tmp_path, [f"https://example.com/{i}" for i in range(40)], concurrent=1)

    def boom(*_a, **_k):
        raise OSError("disk full")

    f._record_result = boom  # type: ignore[method-assign]
    events, exc = await _run_or_hang(f)
    assert isinstance(exc, OSError)
    assert events[-1].type == EventType.FAILED


@pytest.mark.asyncio
async def test_resume_error_emits_failed_event(tmp_path):
    f = _fetcher(tmp_path, ["https://example.com/a"], cache=True)
    f.config.cache.resume = True
    f._cache_manager.save_discovered_urls(
        ["https://example.com/a"], START, config_fingerprint=f._run_fingerprint()
    )

    def boom(*_a, **_k):
        raise OSError("disk full")

    f._record_result = boom  # type: ignore[method-assign]
    events, exc = await _run_or_hang(f)
    assert isinstance(exc, OSError)
    assert events[-1].type == EventType.FAILED


@pytest.mark.asyncio
async def test_cancelled_streaming_run_keeps_unfinished_urls_resumable(tmp_path):
    urls = [f"https://example.com/{i}" for i in range(30)]
    holder: dict = {}
    f = _fetcher(
        tmp_path, urls, cache=True, concurrent=1, steps=[_OkStep(on_url=lambda u: holder["f"].cancel())]
    )
    holder["f"] = f
    f._discoverer = _Flood()  # fills the queue, so unfinished URLs exist when cancel lands
    await _collect(f)
    pending = f._cache_manager.frontier.pending_urls()
    assert pending, "cancelled run must leave unfinished URLs pending for --resume"


class _HtmlOnlyThenFail:
    """Leaves raw HTML but no markdown (SaveStep persists it); fails on /b so the frontier survives."""

    name = "html_only"

    async def execute(self, ctx, emit=None):
        if ctx.url.endswith("/b"):
            raise RuntimeError("b broke")
        ctx.html = b"<p>x</p>"
        return ctx


@pytest.mark.asyncio
async def test_saved_page_without_markdown_is_not_left_pending(tmp_path):
    urls = ["https://example.com/a", "https://example.com/b"]
    f = _fetcher(tmp_path, urls, cache=True, steps=[_HtmlOnlyThenFail()], concurrent=1)
    await _collect(f)
    assert f.stats.pages_fetched == 1
    assert f._cache_manager.frontier.pending_urls() == ["https://example.com/b"]


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
