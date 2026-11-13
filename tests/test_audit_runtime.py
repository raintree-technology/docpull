"""Regression tests for audited runtime fixes (budgets, jobs, monitor, contracts)."""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from typing import Any

import pytest

from docpull import monitor as monitor_mod
from docpull.compat_api import CompatASGIApp, Page
from docpull.models.config import RenderConfig
from docpull.monitor import init_monitor, run_monitor_once, set_monitor_paused
from docpull.output_contract import _read_records, ensure_agent_contract_sidecars
from docpull.rendering import (
    CloudSpendTracker,
    RenderedPage,
    RenderError,
    _ensure_cloud_budget,
    append_rendered_page_record,
    cloud_spend_tracker,
    estimate_cloud_render_cost_usd,
    estimate_cloud_run_cost_usd,
)
from tests.pack_fixtures import write_context_pack
from tests.test_compat_api import _post

# 1. Budget ---------------------------------------------------------------------


def test_preflight_estimate_scales_with_max_pages() -> None:
    config = RenderConfig(backend="vercel-sandbox")
    per = estimate_cloud_render_cost_usd("vercel-sandbox", config)
    assert estimate_cloud_run_cost_usd("vercel-sandbox", config, 10) == pytest.approx(per * 10, abs=1e-4)
    assert estimate_cloud_run_cost_usd("vercel-sandbox", config, None) == per


def test_cumulative_cloud_budget_refuses_once_exhausted() -> None:
    probe = RenderConfig(backend="vercel-sandbox")
    per = estimate_cloud_render_cost_usd("vercel-sandbox", probe)
    config = RenderConfig(backend="vercel-sandbox", cloud_max_estimated_cost_usd=per * 2.5)
    _ensure_cloud_budget("vercel-sandbox", config)
    _ensure_cloud_budget("vercel-sandbox", config)
    with pytest.raises(RenderError, match="cumulative"):
        _ensure_cloud_budget("vercel-sandbox", config)
    assert cloud_spend_tracker(config).renders == 2
    # A separate run (new config object) has its own tracker.
    other = RenderConfig(backend="vercel-sandbox", cloud_max_estimated_cost_usd=per * 2.5)
    _ensure_cloud_budget("vercel-sandbox", other)


def test_spend_tracker_is_thread_safe() -> None:
    tracker = CloudSpendTracker()
    accepted: list[int] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            tracker.reserve("vercel-sandbox", 1.0, 10.0)
        except RenderError:
            return
        with lock:
            accepted.append(1)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(accepted) == 10
    assert tracker.spent_usd == pytest.approx(10.0)


# 2. rendered_pages.ndjson dedupe ----------------------------------------------


def _page(url: str, html: bytes) -> RenderedPage:
    return RenderedPage(url=url, html=html, backend="agent-browser")


def test_rendered_pages_sidecar_dedupes_by_url(tmp_path: Path) -> None:
    config = RenderConfig()
    append_rendered_page_record(tmp_path, _page("https://a.test/", b"1"), config, source="t")
    append_rendered_page_record(tmp_path, _page("https://b.test/", b"2"), config, source="t")
    path = append_rendered_page_record(tmp_path, _page("https://a.test/", b"33"), config, source="t")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["url"] for row in rows] == ["https://a.test/", "https://b.test/"]
    assert rows[0]["html_bytes"] == 2


# 3. compat jobs ----------------------------------------------------------------


async def _settle(app: CompatASGIApp) -> None:
    for job in list(app.jobs._jobs.values()):
        await job.done.wait()


@pytest.mark.asyncio
async def test_crawl_idempotency_key_dedupes_retries() -> None:
    calls = 0

    async def crawl(url: str, limit: int, depth: int, inc: Any, exc: Any, on_page: Any) -> int:
        nonlocal calls
        calls += 1
        on_page(Page(url=url, markdown="# a\n", title="a", status_code=200))
        return 1

    app = CompatASGIApp(crawl=crawl)
    body = {"url": "https://example.com", "idempotencyKey": "retry-1"}
    _, first = await _post(app, "/v2/crawl", body)
    _, second = await _post(app, "/v2/crawl", body)
    await _settle(app)
    assert first["id"] == second["id"]
    assert calls == 1
    _, third = await _post(app, "/v2/crawl", {"url": "https://example.com"})
    assert third["id"] != first["id"]
    await _settle(app)


@pytest.mark.asyncio
async def test_cancel_does_not_overwrite_completed_and_cancelled_is_not_success() -> None:
    async def quick(url: str, *_args: Any) -> int:
        return 0

    app = CompatASGIApp(crawl=quick)
    _, created = await _post(app, "/v1/crawl", {"url": "https://example.com"})
    await _settle(app)
    _, cancelled = await _post(app, f"/v1/crawl/{created['id']}", {}, method="DELETE")
    assert cancelled["status"] == "completed"
    _, status = await _post(app, f"/v1/crawl/{created['id']}", {}, method="GET")
    assert status["status"] == "completed" and status["success"] is True

    gate = asyncio.Event()

    async def slow(*_args: Any) -> int:
        await gate.wait()
        return 0

    app2 = CompatASGIApp(crawl=slow)
    _, created = await _post(app2, "/v1/crawl", {"url": "https://example.com"})
    await _post(app2, f"/v1/crawl/{created['id']}", {}, method="DELETE")
    await _settle(app2)
    _, status = await _post(app2, f"/v1/crawl/{created['id']}", {}, method="GET")
    assert status["status"] == "cancelled" and status["success"] is False


@pytest.mark.asyncio
async def test_crawl_site_records_per_page_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from docpull import compat_jobs
    from docpull.core import fetcher as fetcher_mod

    class FakeCtx:
        error = None
        should_skip = False
        skip_reason = None
        markdown = "# ok\n"
        title = "ok"
        status_code = 200
        metadata: dict[str, Any] = {}

    class FakeFetcher:
        def __init__(self, _config: Any) -> None:
            pass

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: Any) -> None:
            return None

        async def discover(self) -> list[str]:
            return ["https://example.com/bad", "https://example.com/good"]

        async def fetch_one(self, url: str, save: bool = False) -> Any:
            if url.endswith("bad"):
                raise RuntimeError("boom")
            return FakeCtx()

    monkeypatch.setattr(fetcher_mod, "Fetcher", FakeFetcher)
    monkeypatch.setattr(
        "docpull.compat_api.page_from_context", lambda url, ctx: Page(url=url, markdown="# ok\n")
    )
    pages: list[Page] = []
    total = await compat_jobs.crawl_site("https://example.com", 10, 1, [], [], pages.append)
    assert total == 3
    errors = {page.url: page.error for page in pages if page.error}
    assert errors == {"https://example.com/bad": "RuntimeError: boom"}
    assert len(pages) == 3


# 4. monitor --------------------------------------------------------------------


def _fake_refresh(changed: list[str]) -> Any:
    def refresh(pack_dir: Path, *, output_dir: Path, dry_run: bool, markdown_path: Path) -> dict[str, Any]:
        shutil.copytree(pack_dir, output_dir)
        return {"diff": {"changed_urls": changed, "added_urls": [], "removed_urls": [], "unchanged_urls": []}}

    return refresh


def test_monitor_advances_baseline_and_dedupes_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack_dir = tmp_path / "pack"
    state_dir = tmp_path / "monitors"
    write_context_pack(pack_dir)
    init_monitor("docs", pack_dir, state_dir=state_dir)
    monkeypatch.setattr(monitor_mod, "refresh_pack", _fake_refresh(["https://example.com/a"]))
    issue = tmp_path / "issue.md"

    first = run_monitor_once("docs", state_dir=state_dir, github_issue_file=issue)
    assert first["summary"]["deduplicated"] is False
    assert "github_issue_file" in first["notification_outputs"]
    issue.unlink()

    second = run_monitor_once("docs", state_dir=state_dir, github_issue_file=issue)
    assert second["baseline_pack_dir"] == str(Path(first["run_dir"], "refreshed-pack").resolve())
    assert second["pack_dir"] == str(pack_dir.resolve())
    assert second["summary"]["deduplicated"] is True
    assert not issue.exists()


def test_monitor_rechecks_pause_before_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pack_dir = tmp_path / "pack"
    state_dir = tmp_path / "monitors"
    write_context_pack(pack_dir)
    init_monitor("docs", pack_dir, state_dir=state_dir)
    inner = _fake_refresh([])

    def pausing(*args: Any, **kwargs: Any) -> dict[str, Any]:
        set_monitor_paused("docs", True, state_dir=state_dir)
        return inner(*args, **kwargs)

    monkeypatch.setattr(monitor_mod, "refresh_pack", pausing)
    issue = tmp_path / "issue.md"
    with pytest.raises(monitor_mod.MonitorError, match="paused"):
        run_monitor_once("docs", state_dir=state_dir, github_issue_file=issue)
    assert not issue.exists()


# 5. output contract ------------------------------------------------------------


def test_read_records_accepts_jsonl_and_reports_pack_load_failure(tmp_path: Path) -> None:
    jsonl = tmp_path / "jsonl"
    jsonl.mkdir()
    (jsonl / "documents.jsonl").write_text(json.dumps({"url": "https://a.test/"}) + "\n")
    assert _read_records(jsonl) == [{"url": "https://a.test/"}]

    broken = tmp_path / "broken"
    broken.mkdir()
    issues: list[dict[str, Any]] = []
    assert _read_records(broken, issues=issues) == []
    assert issues and issues[0]["code"] == "pack_load_failed"
    ensure_agent_contract_sidecars(broken)
    coverage = json.loads((broken / "coverage.report.json").read_text())
    assert coverage["issues"][0]["code"] == "pack_load_failed"


# 6. redaction fails closed -----------------------------------------------------


def test_redaction_drops_unscannable_text_and_cleans_up_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from docpull import redaction

    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "ok.md").write_text("contact a@b.com\n")
    (pack / "bad.md").write_bytes(b"\xff\xfe secret a@b.com")
    out = tmp_path / "out"
    report = redaction.redact_pack(pack, policy_path=None, output_dir=out)
    assert report["dropped_unreadable_files"] == ["bad.md"]
    assert not (out / "bad.md").exists()

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise redaction.RedactionError("detector failed")

    monkeypatch.setattr(redaction, "_matches_for_text", explode)
    out2 = tmp_path / "out2"
    with pytest.raises(redaction.RedactionError):
        redaction.redact_pack(pack, policy_path=None, output_dir=out2)
    assert not out2.exists()


# 7. MCP error text -------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_tool_error_includes_exception_type(monkeypatch: pytest.MonkeyPatch) -> None:
    from docpull.mcp import server

    def boom(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("kaput")

    monkeypatch.setattr(server, "_require_str", boom)
    result = await server._dispatch_tool("fetch_url", {"url": "https://example.com"})
    assert result.is_error
    assert "RuntimeError: kaput" in str(getattr(result, "text", result))


# 9. project sync exit code -----------------------------------------------------
