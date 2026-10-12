"""Tests for the async crawl, task-run, and research job routes."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from docpull.compat_api import CompatASGIApp, Page
from docpull.compat_jobs import _path_allowed
from docpull.web_search import SearchHit, SearchResponse
from tests.test_compat_api import _post

DOC = "# Limits\n\nThe API allows 100 requests per minute per key.\n"


async def _search(query: str, max_results: int, include: list[str], exclude: list[str], content: bool):
    return SearchResponse(
        query=query,
        backend="fake",
        hits=[SearchHit(url="https://docs.example.com/limits", title="Limits", content=DOC)],
    )


async def _crawl(
    url: str, limit: int, depth: int, include: list[str], exclude: list[str], on_page: Any
) -> int:
    urls = [f"{url}/a", f"{url}/b", f"{url}/missing"][:limit]
    for item in urls:
        await asyncio.sleep(0)
        error = "HTTP 404" if item.endswith("missing") else None
        on_page(
            Page(url=item, markdown="" if error else f"# {item}\n", title=item, status_code=200, error=error)
        )
    return len(urls)


async def _settle(app: CompatASGIApp) -> None:
    for job in list(app.jobs._jobs.values()):
        await job.done.wait()


@pytest.mark.asyncio
async def test_firecrawl_crawl_job_lifecycle() -> None:
    app = CompatASGIApp(crawl=_crawl)
    headers = [(b"host", b"127.0.0.1:8765")]

    status, created = await _post(
        app, "/firecrawl/v2/crawl", {"url": "https://example.com", "limit": 3}, headers=headers
    )
    assert status == 200 and created["success"] is True
    assert created["url"] == f"http://127.0.0.1:8765/firecrawl/v2/crawl/{created['id']}"

    await _settle(app)
    status, done = await _post(app, f"/v2/crawl/{created['id']}", {}, method="GET")
    assert status == 200
    assert done["status"] == "completed"
    assert done["total"] == 3 and done["completed"] == 2
    assert done["data"][0]["metadata"]["sourceURL"] == "https://example.com/a"
    assert done["creditsUsed"] == 0

    assert (await _post(app, "/v1/crawl/unknown", {}, method="GET"))[0] == 404
    assert (await _post(app, "/v2/crawl", {"url": "https://example.com", "limit": 9999}))[0] == 400
    assert (await _post(app, "/v2/crawl", {"url": "https://example.com", "includePaths": ["("]}))[0] == 400


@pytest.mark.asyncio
async def test_crawl_cancel_and_failure_status() -> None:
    gate = asyncio.Event()

    async def slow(*_args: Any) -> int:
        await gate.wait()
        return 0

    app = CompatASGIApp(crawl=slow)
    _, created = await _post(app, "/v1/crawl", {"url": "https://example.com"})
    _, running = await _post(app, f"/v1/crawl/{created['id']}", {}, method="GET")
    assert running["status"] == "scraping"
    _, cancelled = await _post(app, f"/v1/crawl/{created['id']}", {}, method="DELETE")
    assert cancelled == {"status": "cancelled"}
    await _settle(app)
    assert (await _post(app, f"/v1/crawl/{created['id']}", {}, method="GET"))[1]["status"] == "cancelled"

    async def broken(*_args: Any) -> int:
        raise RuntimeError("discovery failed")

    failing = CompatASGIApp(crawl=broken)
    _, created = await _post(failing, "/v2/crawl", {"url": "https://example.com"})
    await _settle(failing)
    _, failed = await _post(failing, f"/v2/crawl/{created['id']}", {}, method="GET")
    assert failed["status"] == "failed" and failed["error"] == "discovery failed"


@pytest.mark.asyncio
async def test_parallel_task_run_lifecycle() -> None:
    app = CompatASGIApp(search=_search)

    status, run = await _post(app, "/v1/tasks/runs", {"input": "requests per minute", "processor": "base"})
    assert status == 200
    assert run["run_id"].startswith("trun_") and run["status"] == "queued" and run["is_active"] is True
    assert run["processor"] == "base"

    status, result = await _post(app, f"/v1/tasks/runs/{run['run_id']}/result", {}, method="GET")
    assert status == 200
    assert result["run"]["status"] == "completed" and result["run"]["is_active"] is False
    assert result["output"]["type"] == "text" and "[S1]" in result["output"]["content"]
    citation = result["output"]["basis"][0]["citations"][0]
    assert citation["url"] == "https://docs.example.com/limits"
    assert "100 requests per minute" in citation["excerpts"][0]

    assert (await _post(app, f"/v1/tasks/runs/{run['run_id']}", {}, method="GET"))[1]["status"] == "completed"
    assert (await _post(app, "/v1/tasks/runs", {"input": ""}))[0] == 400
    assert (await _post(CompatASGIApp(), "/v1/tasks/runs", {"input": "q"}))[0] == 501


@pytest.mark.asyncio
async def test_task_run_result_times_out_while_active() -> None:
    gate = asyncio.Event()

    async def blocked(*_args: Any) -> SearchResponse:
        await gate.wait()
        return await _search("q", 5, [], [], True)

    app = CompatASGIApp(search=blocked)
    _, run = await _post(app, "/v1/tasks/runs", {"input": "requests per minute"})
    messages: list[Any] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/v1/tasks/runs/{run['run_id']}/result",
        "query_string": b"timeout=0.01",
        "headers": [],
    }
    await app(scope, receive, send)
    assert messages[0]["status"] == 408
    gate.set()
    await _settle(app)


@pytest.mark.asyncio
async def test_tavily_research_lifecycle() -> None:
    app = CompatASGIApp(search=_search)

    status, created = await _post(app, "/research", {"input": "requests per minute", "model": "mini"})
    assert status == 201 and created["status"] == "pending" and created["model"] == "mini"

    status, pending = await _post(app, f"/research/{created['request_id']}", {}, method="GET")
    assert status == 202 and pending["status"] in {"pending", "in_progress"}

    await _settle(app)
    status, done = await _post(app, f"/tavily/research/{created['request_id']}", {}, method="GET")
    assert status == 200 and done["status"] == "completed"
    assert "[S1]" in done["content"]
    assert done["sources"] == [{"url": "https://docs.example.com/limits", "title": "Limits", "favicon": None}]
    assert (await _post(app, "/research/nope", {}, method="GET"))[0] == 404


@pytest.mark.asyncio
async def test_job_routes_require_the_api_key() -> None:
    app = CompatASGIApp(search=_search, crawl=_crawl, api_key="k")
    assert (await _post(app, "/v1/tasks/runs", {"input": "q"}))[0] == 401
    assert (await _post(app, "/research/x", {}, method="GET"))[0] == 401


def test_crawl_path_filters_are_regular_expressions() -> None:
    assert _path_allowed("https://e.com/docs/a", [r"^/docs/"], [])
    assert not _path_allowed("https://e.com/blog/a", [r"^/docs/"], [])
    assert not _path_allowed("https://e.com/docs/private/x", [r"^/docs/"], [r"private"])
