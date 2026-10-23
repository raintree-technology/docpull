"""Async job routes for the compatible server: crawl, task runs, and research.

Jobs run as background tasks in the server process and live in memory, so they
are lost when the server stops.
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from .research import ResearchResult, answer_from_search

if TYPE_CHECKING:
    from .compat_api import CompatASGIApp, Page

# ponytail: in-memory store, capped and expiring; persist jobs if restarts must keep them.
MAX_JOBS = 200
JOB_TTL = timedelta(hours=24)
DEFAULT_CRAWL_PAGES = 50
MAX_CRAWL_PAGES = 500
CRAWL_CONCURRENCY = 4
RESULT_WAIT_SECONDS = 600

Crawler = Callable[[str, int, int, list[str], list[str], Callable[["Page"], None]], Awaitable[int]]


class JobRequestError(ValueError):
    """Invalid job request."""


@dataclass
class Job:
    id: str
    kind: str
    request: dict[str, Any]
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    modified_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "queued"
    pages: list[Page] = field(default_factory=list)
    total: int = 0
    result: ResearchResult | None = None
    error: str | None = None
    started: float = field(default_factory=time.perf_counter)
    finished: float | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def touch(self, status: str) -> None:
        self.status = status
        self.modified_at = datetime.now(UTC)

    @property
    def elapsed(self) -> float:
        return round((self.finished or time.perf_counter()) - self.started, 3)


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def add(self, kind: str, request: dict[str, Any], prefix: str) -> Job:
        self._expire()
        if len(self._jobs) >= MAX_JOBS:
            raise JobRequestError(f"Too many running jobs; at most {MAX_JOBS} are kept")
        job = Job(id=f"{prefix}{uuid.uuid4().hex}", kind=kind, request=request)
        self._jobs[job.id] = job
        return job

    def get(self, job_id: str, kind: str) -> Job | None:
        self._expire()
        job = self._jobs.get(job_id)
        return job if job and job.kind == kind else None

    def _expire(self) -> None:
        now = datetime.now(UTC)
        for job_id, job in list(self._jobs.items()):
            if job.done.is_set() and now - job.modified_at > JOB_TTL:
                del self._jobs[job_id]
        finished = [job for job in self._jobs.values() if job.done.is_set()]
        for job in sorted(finished, key=lambda item: item.modified_at)[
            : max(0, len(self._jobs) - MAX_JOBS + 1)
        ]:
            del self._jobs[job.id]


def start(job: Job, work: Callable[[Job], Awaitable[None]]) -> None:
    async def run() -> None:
        job.touch("running")
        try:
            await work(job)
            job.touch("completed")
        except Exception as err:  # noqa: BLE001 - the job reports any failure as its status
            job.error = str(err) or type(err).__name__
            job.touch("failed")

    def finish(task: asyncio.Task[None]) -> None:
        # Runs even when the task is cancelled before its body starts.
        if task.cancelled():
            job.touch("cancelled")
        job.finished = time.perf_counter()
        job.done.set()

    job.task = asyncio.create_task(run())
    job.task.add_done_callback(finish)


async def crawl_site(
    url: str,
    limit: int,
    depth: int,
    include: list[str],
    exclude: list[str],
    on_page: Callable[[Page], None],
) -> int:
    """Discover up to ``limit`` URLs from ``url`` and fetch them locally; returns the total."""
    from .compat_api import page_from_context
    from .core.fetcher import Fetcher
    from .models.config import CrawlConfig, DocpullConfig

    config = DocpullConfig(url=url, crawl=CrawlConfig(max_pages=limit * 4, max_depth=depth))
    async with Fetcher(config) as fetcher:
        discovered = await fetcher.discover()
        urls = [item for item in dict.fromkeys([url, *discovered]) if _path_allowed(item, include, exclude)]
        urls = urls[:limit]
        semaphore = asyncio.Semaphore(CRAWL_CONCURRENCY)

        async def one(item: str) -> None:
            async with semaphore:
                ctx = await fetcher.fetch_one(item, save=False)
            on_page(page_from_context(item, ctx))

        await asyncio.gather(*(one(item) for item in urls))
    return len(urls)


def _path_allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    from urllib.parse import urlsplit

    path = urlsplit(url).path or "/"
    if include and not any(re.search(pattern, path) for pattern in include):
        return False
    return not any(re.search(pattern, path) for pattern in exclude)


def _patterns(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise JobRequestError(f"{name} must be a list of regular expressions")
    for pattern in value:
        try:
            re.compile(pattern)
        except re.error as err:
            raise JobRequestError(f"Invalid {name} pattern {pattern!r}: {err}") from err
    return value


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


# Firecrawl /v1/crawl and /v2/crawl ------------------------------------------------


async def create_crawl(app: CompatASGIApp, body: dict[str, Any], base: str) -> tuple[int, dict[str, Any]]:
    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        raise JobRequestError("url is required")
    limit = body.get("limit", DEFAULT_CRAWL_PAGES)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_CRAWL_PAGES:
        raise JobRequestError(f"limit must be an integer from 1 to {MAX_CRAWL_PAGES}")
    raw_depth = body.get("maxDiscoveryDepth", body.get("maxDepth", 2))
    if isinstance(raw_depth, bool) or not isinstance(raw_depth, int) or raw_depth < 0:
        raise JobRequestError("maxDiscoveryDepth must be a non-negative integer")
    include = _patterns(body.get("includePaths"), "includePaths")
    exclude = _patterns(body.get("excludePaths"), "excludePaths")
    job = app.jobs.add("crawl", body, "")

    async def work(job: Job) -> None:
        job.total = await app.crawl(url.strip(), limit, max(1, raw_depth), include, exclude, job.pages.append)

    start(job, work)
    return 200, {"success": True, "id": job.id, "url": f"{base}/{job.id}"}


def crawl_status(app: CompatASGIApp, job_id: str) -> tuple[int, dict[str, Any]]:
    job = app.jobs.get(job_id, "crawl")
    if job is None:
        return 404, {"success": False, "error": "Crawl job not found"}
    status = {"queued": "scraping", "running": "scraping"}.get(job.status, job.status)
    fetched = [page for page in job.pages if not page.error]
    data = [
        {
            "markdown": page.markdown,
            "metadata": {
                "sourceURL": page.url,
                "url": page.url,
                "title": page.title,
                "statusCode": page.status_code,
            },
        }
        for page in fetched
    ]
    payload: dict[str, Any] = {
        "success": job.status != "failed",
        "status": status,
        "total": max(job.total, len(job.pages)),
        "completed": len(fetched),
        "creditsUsed": 0,
        "expiresAt": _iso(job.created_at + JOB_TTL),
        "next": None,
        "data": data,
    }
    if job.error:
        payload["error"] = job.error
    return 200, payload


def cancel_crawl(app: CompatASGIApp, job_id: str) -> tuple[int, dict[str, Any]]:
    job = app.jobs.get(job_id, "crawl")
    if job is None:
        return 404, {"success": False, "error": "Crawl job not found"}
    if job.task and not job.done.is_set():
        job.task.cancel()
    return 200, {"status": "cancelled"}


# Shared research work for Parallel task runs and Tavily research ---------------


def _research_work(
    app: CompatASGIApp, query: str, include: Any = None, exclude: Any = None
) -> Callable[[Job], Awaitable[None]]:
    async def work(job: Job) -> None:
        response = await app.run_search(query, 5, include, exclude, True)
        job.result = await answer_from_search(response.query, response, llm=app.llm)

    return work


def _question(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict) and value:
        return " ".join(f"{key}: {item}" for key, item in value.items())
    raise JobRequestError("input must be a non-empty string or object")


# Parallel /v1/tasks/runs -------------------------------------------------------


def _task_run(job: Job) -> dict[str, Any]:
    return {
        "run_id": job.id,
        "interaction_id": job.id,
        "status": job.status,
        "is_active": not job.done.is_set(),
        "processor": job.request.get("processor", "docpull"),
        "metadata": job.request.get("metadata"),
        "error": {"message": job.error} if job.error else None,
        "warnings": None,
        "created_at": _iso(job.created_at),
        "modified_at": _iso(job.modified_at),
    }


async def create_task_run(app: CompatASGIApp, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    query = _question(body.get("input"))
    app.require_search()
    job = app.jobs.add("task", body, "trun_")
    start(job, _research_work(app, query))
    return 200, _task_run(job)


def task_run(app: CompatASGIApp, job_id: str) -> tuple[int, dict[str, Any]]:
    job = app.jobs.get(job_id, "task")
    return (200, _task_run(job)) if job else (404, {"error": "Task run not found"})


async def task_run_result(app: CompatASGIApp, job_id: str, timeout: float) -> tuple[int, dict[str, Any]]:
    job = app.jobs.get(job_id, "task")
    if job is None:
        return 404, {"error": "Task run not found"}
    try:
        await asyncio.wait_for(job.done.wait(), timeout=timeout)
    except TimeoutError:
        return 408, {"error": "Task run is still active", "run": _task_run(job)}
    if job.result is None:
        return 422, {"error": job.error or "Task run failed", "run": _task_run(job)}
    citations = [
        {
            "url": source["url"],
            "title": source["title"],
            "excerpts": [e.text for e in job.result.evidence if e.source_id == source["id"]],
        }
        for source in job.result.sources
    ]
    basis = [
        {
            "field": "output",
            "citations": citations,
            "reasoning": f"Answer built from {len(job.result.evidence)} ranked passages ({job.result.mode}).",
            "confidence": None,
        }
    ]
    output = {"type": "text", "content": job.result.answer, "basis": basis}
    return 200, {"run": _task_run(job), "output": output}


# Tavily /research ---------------------------------------------------------------


async def create_research(app: CompatASGIApp, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    query = _question(body.get("input"))
    app.require_search()
    job = app.jobs.add("research", body, "")
    start(job, _research_work(app, query, body.get("include_domains"), body.get("exclude_domains")))
    return 201, {
        "request_id": job.id,
        "created_at": _iso(job.created_at),
        "status": "pending",
        "input": query,
        "model": body.get("model", "docpull"),
        "response_time": 0,
    }


def research_status(app: CompatASGIApp, job_id: str) -> tuple[int, dict[str, Any]]:
    job = app.jobs.get(job_id, "research")
    if job is None:
        return 404, {"error": "Research task not found"}
    if not job.done.is_set():
        status = "pending" if job.status == "queued" else "in_progress"
        return 202, {"request_id": job.id, "status": status, "response_time": job.elapsed}
    payload: dict[str, Any] = {
        "request_id": job.id,
        "created_at": _iso(job.created_at),
        "status": job.status,
        "response_time": job.elapsed,
    }
    if job.result is not None:
        payload["content"] = job.result.answer
        payload["sources"] = [
            {"url": s["url"], "title": s["title"], "favicon": None} for s in job.result.sources
        ]
    else:
        payload["error"] = job.error
    return 200, payload
