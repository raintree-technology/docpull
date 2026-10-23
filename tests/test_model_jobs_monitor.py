"""Models of the job lifecycle, monitor runs, and render opt-in gates.

Each action mirrors a branch in the real code:

- jobs: ``compat_jobs.start`` / ``cancel_crawl`` / ``JobStore`` (src/docpull/compat_jobs.py)
- monitor: ``local_workflows.refresh_pack`` + ``monitor.run_monitor_once``
- gates: MCP ``render_url`` and ``paid_action_blocked`` (src/docpull/mcp/server.py)
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

from tests.model_check import check

# ---------------------------------------------------------------- job lifecycle

RANK = {"queued": 0, "running": 1, "completed": 2, "failed": 2, "cancelled": 2}
TERMINAL = {"completed", "failed", "cancelled"}


class Job(NamedTuple):
    status: str = "queued"
    cancel_req: bool = False  # task.cancel() called; CancelledError lands at the next await
    done: bool = False  # asyncio.Event set by the done callback
    pages: int = 0
    pages_at_end: int = -1  # page count when the status went terminal
    result: bool = False
    last_seen: str = "none"  # status the last poll returned
    served: bool = False  # /result returned 200 with an answer
    gone: bool = False  # expired, evicted, or lost in a restart


def _live(s: Job) -> bool:
    return not s.gone and not s.done


def _end(s: Job, status: str, **kw: object) -> Job:
    # touch(status) happens in run(); done is set by the callback in a later step.
    return s._replace(status=status, pages_at_end=s.pages, **kw)  # type: ignore[arg-type]


def j_start(s: Job):
    if _live(s) and s.status == "queued" and not s.cancel_req:
        yield s._replace(status="running")


def j_page(s: Job):
    if _live(s) and s.status == "running" and not s.cancel_req and s.pages < 2:
        yield s._replace(pages=s.pages + 1)


def j_finish_ok(s: Job):
    if _live(s) and s.status == "running" and not s.cancel_req:
        yield _end(s, "completed", result=True)


def j_finish_err(s: Job):
    if _live(s) and s.status == "running" and not s.cancel_req:
        yield _end(s, "failed")


def j_cancel(s: Job):
    # cancel_crawl: only while the task exists and done is not set.
    if _live(s) and not s.cancel_req:
        yield s._replace(cancel_req=True)


def j_cancel_lands(s: Job):
    # Task dies with CancelledError (also before its body ever ran); finish() marks cancelled.
    if _live(s) and s.cancel_req and s.status not in TERMINAL:
        yield _end(s, "cancelled")


def j_done_callback(s: Job):
    if not s.gone and not s.done and s.status in TERMINAL:
        yield s._replace(done=True)


def j_poll(s: Job):
    seen = "404" if s.gone else s.status
    yield s._replace(last_seen=seen)


def j_result(s: Job):
    if not s.gone and s.done and s.result:
        yield s._replace(served=True)


def j_expire(s: Job):
    if not s.gone and s.done:
        yield s._replace(gone=True)


def j_restart(s: Job):
    # In-memory store: every job vanishes, running tasks die with the process.
    yield s._replace(gone=True)


JOB_ACTIONS = {
    "start": j_start,
    "page": j_page,
    "finish_ok": j_finish_ok,
    "finish_err": j_finish_err,
    "cancel": j_cancel,
    "cancel_lands": j_cancel_lands,
    "done_callback": j_done_callback,
    "poll": j_poll,
    "result": j_result,
    "expire": j_expire,
    "restart": j_restart,
}

JOB_INVARIANTS = {
    "polls never go backward or leave terminal": lambda s: (
        s.gone
        or s.last_seen in ("none", "404")
        or (RANK[s.status] >= RANK[s.last_seen] and (s.last_seen not in TERMINAL or s.status == s.last_seen))
    ),
    "done implies terminal": lambda s: not s.done or s.status in TERMINAL,
    "cancelled never reports success": lambda s: s.status != "cancelled" or not s.result,
    "only completed jobs serve results": lambda s: not s.served or s.status == "completed",
    "no pages written after the job ended": lambda s: s.pages_at_end in (-1, s.pages),
    "result only on completed": lambda s: not s.result or s.status == "completed",
}


def test_job_lifecycle_model() -> None:
    states = check([Job()], JOB_ACTIONS, JOB_INVARIANTS)
    assert states > 50


# --------------------------------------------------------------- monitor runs

# What one URL's re-fetch yields: same content, new content, hard error, or a skip.
FETCH = ("same", "new", "err", "skip")


class Mon(NamedTuple):
    live: str = "same"  # what a fetch would return right now
    inflight: tuple[str, ...] = ()  # fetches taken by runs that have not reported yet
    reports: tuple[tuple[str, str], ...] = ()  # (fetch outcome, event) per finished run
    baseline: int = 0  # pack the monitor compares against


def monitor_model(*, fixed: bool):
    def source_changes(s: Mon):
        for live in FETCH:
            if live != s.live:
                yield s._replace(live=live)

    def run_fetch(s: Mon):
        if len(s.inflight) < 2 and len(s.reports) + len(s.inflight) < 3:
            yield s._replace(inflight=(*s.inflight, s.live))

    def run_report(s: Mon):
        for i, outcome in enumerate(s.inflight):
            if outcome == "new":
                event = "changed"
            elif outcome in ("err", "skip") and not fixed:
                event = "removed"  # old behaviour: URL missing from refreshed pack
            else:
                event = "none"
            rest = s.inflight[:i] + s.inflight[i + 1 :]
            yield s._replace(inflight=rest, reports=(*s.reports, (outcome, event)))

    invariants = {
        "failed fetch never reported as removed": lambda s: all(
            event != "removed" for outcome, event in s.reports if outcome in ("err", "skip")
        ),
        "changed reported iff the fetch saw new content": lambda s: all(
            (event == "changed") == (outcome == "new") for outcome, event in s.reports
        ),
        "baseline advances only on success (never advanced)": lambda s: s.baseline == 0,
        # change_events use a key of (url, old hash, new hash): the same change always has
        # the same key, so a consumer sees each distinct change once.
        "one distinct event key per change": lambda s: (
            len({event for _, event in s.reports if event != "none"}) <= 1
        ),
    }
    return {"source_changes": source_changes, "run_fetch": run_fetch, "run_report": run_report}, invariants


def test_monitor_model_with_fix() -> None:
    actions, invariants = monitor_model(fixed=True)
    assert check([Mon()], actions, invariants) > 20


def test_monitor_model_catches_the_old_behaviour() -> None:
    actions, invariants = monitor_model(fixed=False)
    with pytest.raises(AssertionError, match="failed fetch never reported as removed"):
        check([Mon()], actions, invariants)


# ------------------------------------------------------------- opt-in gates

COST = 1  # estimated cloud render cost, whole units


class Gate(NamedTuple):
    called: str = "none"  # none | local | vercel | e2b | website_default
    budget: int | None = None
    trusted_env: bool = False
    browser_launched: bool = False
    paid_spent: bool = False


def gate_actions(s: Gate):
    if s.called != "none":
        return
    # render_url runtime=...
    yield s._replace(called="local", browser_launched=True)
    for budget in (None, 0, 2):
        for runtime in ("vercel", "e2b"):
            blocked = budget is not None and (budget < COST or budget <= 0)  # paid_action_blocked
            yield s._replace(
                called=runtime,
                budget=budget,
                browser_launched=not blocked,
                paid_spent=not blocked,
            )
    # website-pack with default options: key_page_visuals asks for screenshots.
    for env in (False, True):
        yield s._replace(called="website_default", trusted_env=env, browser_launched=env)


GATE_INVARIANTS = {
    "paid spend needs an explicit cloud runtime": lambda s: not s.paid_spent or s.called in ("vercel", "e2b"),
    "paid spend never exceeds budget": lambda s: not s.paid_spent or s.budget is None or s.budget >= COST,
    "default workflows launch a browser only with the trusted env": lambda s: (
        s.called != "website_default" or s.browser_launched == s.trusted_env
    ),
    "no browser or spend without a call": lambda s: (
        s.called != "none" or not (s.browser_launched or s.paid_spent)
    ),
}


def test_render_gate_model() -> None:
    assert check([Gate()], {"call": gate_actions}, GATE_INVARIANTS) == 10
