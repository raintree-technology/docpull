"""Regression tests for audited cache, MCP ensure_docs, and fetcher queue bugs."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import docpull.mcp.tools as mcp_tools
from docpull._fileutil import atomic_write_text, file_lock
from docpull.cache.manager import CacheManager

# --- helpers -----------------------------------------------------------


class _Event:
    def __init__(self, value: str) -> None:
        self.type = SimpleNamespace(value=value)


def _make_fetcher(
    pages: list[str], calls: list[int], *, gate: asyncio.Event | None = None, fail: bool = False
):
    class FakeFetcher:
        def __init__(self, config: Any) -> None:
            self.out = Path(config.output.directory)
            self.stats = SimpleNamespace(pages_fetched=len(pages), pages_skipped=0, pages_failed=0)

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def run(self):
            calls.append(1)
            self.out.mkdir(parents=True, exist_ok=True)
            for name in pages:
                (self.out / name).write_text(f"# {name}")
                yield _Event("fetch_completed")
                if gate is not None:
                    await gate.wait()
            if fail:
                raise RuntimeError("boom")

    return FakeFetcher


@pytest.fixture
def source(monkeypatch):
    resolved = SimpleNamespace(url="https://docs.example.test/", max_pages=None)
    monkeypatch.setattr(mcp_tools, "resolve_source", lambda name: resolved if name == "demo" else None)
    mcp_tools._SOURCE_LOCKS.clear()
    return "demo"


def _meta(docs: Path) -> dict[str, Any]:
    return json.loads((docs / ".demo.meta.json").read_text())


# --- 1. unique temp files ---------------------------------------------


def test_meta_write_uses_unique_temp(tmp_path, monkeypatch):
    seen: list[str] = []
    real_replace = mcp_tools.os.replace if hasattr(mcp_tools, "os") else None
    import docpull._fileutil as fu

    def spy(src, dst):
        seen.append(str(src))
        return real_replace(src, dst)  # type: ignore[misc]

    monkeypatch.setattr(fu.os, "replace", spy)
    meta = tmp_path / ".demo.meta.json"
    mcp_tools._write_meta(meta, "demo", "u", 1, profile="rag", max_pages=None)
    mcp_tools._write_meta(meta, "demo", "u", 2, profile="rag", max_pages=None)
    assert len(set(seen)) == 2
    assert not any(s.endswith("meta.json.tmp") for s in seen)
    assert json.loads(meta.read_text())["page_count"] == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_text_cleans_temp_on_error(tmp_path, monkeypatch):
    import docpull._fileutil as fu

    def broken(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(fu.os, "replace", broken)
    with pytest.raises(OSError):
        atomic_write_text(tmp_path / "x.json", "{}")
    assert list(tmp_path.iterdir()) == []


# --- 2. cancellation writes partial meta ------------------------------


async def test_cancel_marks_partial(tmp_path, monkeypatch, source):
    gate = asyncio.Event()
    calls: list[int] = []
    monkeypatch.setattr(
        mcp_tools, "Fetcher", _make_fetcher(["a.md", "b.md"], calls, gate=gate), raising=False
    )
    task = asyncio.create_task(mcp_tools.ensure_docs(source, docs_dir=tmp_path))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if calls:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _meta(tmp_path)["partial"] is True
    assert not mcp_tools._cache_fresh(tmp_path / ".demo.meta.json")
    assert not list(tmp_path.glob(".demo.staging-*"))


async def test_exception_marks_partial_and_keeps_old_tree(tmp_path, monkeypatch, source):
    old = tmp_path / "demo"
    old.mkdir()
    (old / "keep.md").write_text("old")
    monkeypatch.setattr(mcp_tools, "Fetcher", _make_fetcher(["a.md"], [], fail=True), raising=False)
    with pytest.raises(RuntimeError):
        await mcp_tools.ensure_docs(source, docs_dir=tmp_path, force=True)
    assert (old / "keep.md").exists()
    assert not (old / "a.md").exists()
    assert _meta(tmp_path)["partial"] is True


# --- 3. forced refresh drops pages deleted upstream -------------------


async def test_force_refresh_removes_stale_pages(tmp_path, monkeypatch, source):
    monkeypatch.setattr(mcp_tools, "Fetcher", _make_fetcher(["a.md", "b.md"], []), raising=False)
    await mcp_tools.ensure_docs(source, docs_dir=tmp_path)
    monkeypatch.setattr(mcp_tools, "Fetcher", _make_fetcher(["a.md"], []), raising=False)
    await mcp_tools.ensure_docs(source, docs_dir=tmp_path, force=True)
    files = sorted(p.name for p in (tmp_path / "demo").rglob("*.md"))
    assert files == ["a.md"]
    assert _meta(tmp_path)["page_count"] == 1
    assert "partial" not in _meta(tmp_path)


# --- 4. single-flight -------------------------------------------------


async def test_concurrent_ensure_docs_crawls_once(tmp_path, monkeypatch, source):
    calls: list[int] = []
    monkeypatch.setattr(mcp_tools, "Fetcher", _make_fetcher(["a.md"], calls), raising=False)
    results = await asyncio.gather(*(mcp_tools.ensure_docs(source, docs_dir=tmp_path) for _ in range(5)))
    assert len(calls) == 1
    assert sum(1 for r in results if r.data and r.data["cached"] is False) == 1


def _hold_lock(path: str, ready, release) -> None:
    with file_lock(path):
        ready.set()
        release.wait(10)


def test_file_lock_excludes_other_process(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    lock = tmp_path / "x.lock"
    proc = ctx.Process(target=_hold_lock, args=(str(lock), ready, release))
    proc.start()
    try:
        assert ready.wait(20)
        import fcntl

        with open(lock, "a+b") as fh, pytest.raises(BlockingIOError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        release.set()
        proc.join(20)


# --- 5. compact text keeps document content ---------------------------


def test_compact_read_doc_keeps_content(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "page.md").write_text("line one\nline two\nline three\n")
    monkeypatch.setenv(mcp_tools.COMPACT_TEXT_ENV_VAR, "1")
    full = mcp_tools.read_doc("lib", "page.md", docs_dir=tmp_path)
    assert "line two" in full.text
    sliced = mcp_tools.read_doc("lib", "page.md", docs_dir=tmp_path, line_start=2, line_end=2)
    assert sliced.text == "line two"
    monkeypatch.delenv(mcp_tools.COMPACT_TEXT_ENV_VAR)
    default = mcp_tools.read_doc("lib", "page.md", docs_dir=tmp_path)
    assert default.text.startswith("# lib/page.md")


async def test_compact_fetch_url_keeps_content(monkeypatch):
    ctx = SimpleNamespace(
        error=None, should_skip=False, markdown="BODY TEXT", chunks=None, title="T", source_type="generic"
    )

    class F:
        def __init__(self, config: Any) -> None: ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a: object) -> None: ...

        async def fetch_one(self, url: str, save: bool = False):
            return ctx

    monkeypatch.setattr(mcp_tools, "Fetcher", F, raising=False)
    monkeypatch.setattr(
        mcp_tools._FETCH_URL_VALIDATOR, "validate", lambda url: SimpleNamespace(is_valid=True)
    )
    monkeypatch.setenv(mcp_tools.COMPACT_TEXT_ENV_VAR, "1")
    result = await mcp_tools.fetch_url("https://example.com/")
    assert "BODY TEXT" in result.text
    assert result.data and result.data["markdown"].endswith("BODY TEXT")


# --- 6. cache save/load failures surface ------------------------------


def test_flush_failure_propagates(tmp_path, monkeypatch):
    cm = CacheManager(tmp_path)
    cm.update_cache("https://a/", "x", tmp_path / "a.md")

    def broken(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(cm, "_write_json", broken)
    with pytest.raises(OSError):
        cm.flush()
    assert cm._manifest_dirty


def test_save_discovered_urls_failure_propagates(tmp_path, monkeypatch):
    cm = CacheManager(tmp_path)

    def broken(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(cm, "_write_json", broken)
    with pytest.raises(OSError):
        cm.save_discovered_urls(["https://a/"], "https://a/")


@pytest.mark.parametrize("name", ["manifest.json", "state.json"])
def test_corrupt_files_backed_up(tmp_path, caplog, name):
    (tmp_path / name).write_text("{not json")
    with caplog.at_level("WARNING"):
        cm = CacheManager(tmp_path)
    assert cm.manifest == {}
    backups = list(tmp_path.glob(f"{name}.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == "{not json"
    assert str(tmp_path / name) in caplog.text


def test_corrupt_discovered_urls_backed_up(tmp_path):
    cm = CacheManager(tmp_path)
    cm.discovered_urls_file.write_text("garbage")
    assert cm.load_discovered_urls("https://a/") is None
    assert list(tmp_path.glob("discovered_urls.json.corrupt-*"))


# --- 7. merge-on-flush across writers ---------------------------------


def test_concurrent_managers_merge_entries(tmp_path):
    a = CacheManager(tmp_path)
    b = CacheManager(tmp_path)
    a.update_cache("https://a/", "A", tmp_path / "a.md")
    a.mark_fetched("https://a/")
    b.update_cache("https://b/", "B", tmp_path / "b.md")
    b.mark_fetched("https://b/")
    a.flush()
    b.flush()
    fresh = CacheManager(tmp_path)
    assert set(fresh.manifest) == {"https://a/", "https://b/"}
    assert fresh.get_fetched_urls() == {"https://a/", "https://b/"}


def test_merge_honours_forget(tmp_path):
    a = CacheManager(tmp_path)
    a.update_cache("https://a/", "A", tmp_path / "a.md")
    a.mark_fetched("https://a/")
    a.flush()
    b = CacheManager(tmp_path)
    b.forget("https://a/")
    other = CacheManager(tmp_path)
    other.update_cache("https://c/", "C", tmp_path / "c.md")
    other.flush()
    b.flush()
    fresh = CacheManager(tmp_path)
    assert set(fresh.manifest) == {"https://c/"}
    assert "https://a/" not in fresh.get_fetched_urls()


# --- 8. bounded event queue -------------------------------------------


async def test_bounded_event_queue_full_run_and_early_exit(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_lifecycle", Path(__file__).parent / "test_fetch_lifecycle.py"
    )
    assert spec and spec.loader
    lifecycle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lifecycle)
    from docpull import EventType

    urls = [f"https://example.com/{i}" for i in range(400)]
    # Slow consumer, many events: queue must apply backpressure, not drop.
    f = lifecycle._fetcher(tmp_path / "a", urls, concurrent=4)
    events = []

    async def slow():
        async for ev in f.run():
            events.append(ev)
            if len(events) % 50 == 0:
                await asyncio.sleep(0.01)

    await asyncio.wait_for(slow(), 20)
    assert events[-1].type == EventType.COMPLETED
    # One progress event per URL plus lifecycle events: none dropped.
    assert len(events) > 400

    # Consumer abandons early: producers blocked on a full queue must not deadlock.
    g = lifecycle._fetcher(tmp_path / "b", urls, concurrent=4)
    gen = g.run()
    await gen.__anext__()
    await asyncio.sleep(0.05)
    await asyncio.wait_for(gen.aclose(), 20)
