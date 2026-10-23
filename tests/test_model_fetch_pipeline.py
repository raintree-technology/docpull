"""Explicit-state model of Fetcher._run_streaming plus the sequential resume loop.

Mirrors core/fetcher.py and pipeline/base.py (see docs/formal/fetch-pipeline.md).
Three URLs, two workers, a queue of two (plus a 4 URL/1 worker run for full-queue
cases): small enough to explore every interleaving of discovery, workers, cancel, and failures.

``Flags`` switch individual pre-fix behaviours back on; the last tests prove the
model would have caught each bug that was fixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import pytest

from tests.model_check import check

SENTINEL = -1

# per-URL outcome
NEW, QUEUED, BUSY, RECORDING, SAVED, SKIPPED, FAILED = (
    "new",
    "queued",
    "busy",
    "rec",
    "saved",
    "skipped",
    "failed",
)
# frontier state
F_NONE, F_PENDING, F_PROC, F_OK, F_SKIP, F_FAIL = "none", "pending", "proc", "ok", "skip", "fail"


@dataclass(frozen=True)
class Flags:
    n_urls: int = 3
    n_workers: int = 2
    qmax: int = 2
    double_fail_event: bool = False  # pipeline/base.py emitted a 2nd FETCH_FAILED
    clear_on_cancel: bool = False  # cancelled run wiped resume state
    worker_exits_on_cancel: bool = False  # worker returned instead of draining
    sentinel_after_error_missing: bool = False  # wait_for_drain skipped the None sentinel
    no_mark_without_markdown: bool = False  # html-only page left frontier "proc"


class S(NamedTuple):
    disc: tuple  # ("run", k) | ("fin", sentinels_left) | ("done",)
    disc_err: bool
    queue: tuple
    workers: tuple  # per worker: None idle | ("busy"|"rec", url, result) | "exit"
    out: tuple  # per URL outcome
    front: tuple  # per URL frontier state
    disk: tuple  # per URL: file written
    cache: tuple  # per URL: cache manifest entry present
    fail_ev: tuple  # per URL: FETCH_FAILED events emitted
    saved: int
    skipped: int
    failed: int
    cancelled: bool
    worker_err: bool
    term: tuple  # terminal run events emitted, in order
    cleared: bool


def _set(t: tuple, i: int, v) -> tuple:
    return t[:i] + (v,) + t[i + 1 :]


def init_state(f: Flags) -> S:
    n = f.n_urls
    return S(
        disc=("run", 0), disc_err=False, queue=(), workers=(None,) * f.n_workers,
        out=(NEW,) * n, front=(F_NONE,) * n, disk=(False,) * n, cache=(False,) * n,
        fail_ev=(0,) * n, saved=0, skipped=0, failed=0, cancelled=False, worker_err=False,
        term=(), cleared=False,
    )  # fmt: skip


def build(flags: Flags):
    def running(s: S) -> bool:
        return not s.term

    def cancel(s):
        if running(s) and not s.cancelled:
            yield s._replace(cancelled=True)

    def discover(s):
        if not running(s):
            return
        if s.disc[0] == "hold":  # blocked in `await url_queue.put(url)`; cancel no longer matters
            k = s.disc[1]
            if len(s.queue) < flags.qmax:
                yield s._replace(
                    disc=("run", k + 1),
                    queue=s.queue + (k,),
                    out=_set(s.out, k, QUEUED),
                    front=_set(s.front, k, F_PENDING),  # save_discovered_urls -> frontier.add
                )
            return
        if s.disc[0] != "run":
            return
        k = s.disc[1]
        yield s._replace(disc=("fin", flags.n_workers), disc_err=True)  # discoverer raises
        if k == flags.n_urls or s.cancelled:  # exhausted, or `if self._cancelled: break`
            yield s._replace(disc=("fin", flags.n_workers))
        else:
            yield s._replace(disc=("hold", k))

    def sentinel(s):
        if s.disc[0] == "fin" and s.disc[1] > 0 and len(s.queue) < flags.qmax:
            left = s.disc[1] - 1
            yield s._replace(disc=("fin", left) if left else ("done",), queue=s.queue + (SENTINEL,))

    def get(s):
        if not s.queue:
            return
        item, rest = s.queue[0], s.queue[1:]
        for i, w in enumerate(s.workers):
            if w is not None:
                continue
            if item == SENTINEL:
                yield s._replace(queue=rest, workers=_set(s.workers, i, "exit"))
            elif s.cancelled:
                nxt = _set(s.workers, i, "exit") if flags.worker_exits_on_cancel else s.workers
                yield s._replace(queue=rest, workers=nxt)  # URL dropped; frontier keeps it pending
            else:
                yield s._replace(
                    queue=rest,
                    workers=_set(s.workers, i, ("busy", item, None)),
                    out=_set(s.out, item, BUSY),
                    front=_set(s.front, item, F_PROC),  # mark_processing
                )

    def pipeline(s):
        for i, w in enumerate(s.workers):
            if not (isinstance(w, tuple) and w[0] == "busy"):
                continue
            u = w[1]
            for result in ("saved", "html_only", "skipped", "failed"):
                disk = _set(s.disk, u, True) if result in ("saved", "html_only") else s.disk
                fe = s.fail_ev
                if result == "failed":
                    fe = _set(fe, u, fe[u] + (2 if flags.double_fail_event else 1))
                yield s._replace(workers=_set(s.workers, i, ("rec", u, result)), disk=disk, fail_ev=fe)

    def record(s):
        for i, w in enumerate(s.workers):
            if not (isinstance(w, tuple) and w[0] == "rec"):
                continue
            _, u, result = w
            idle = _set(s.workers, i, None)
            # _record_result raises (e.g. frontier journal OSError): worker keeps draining, run fails.
            yield s._replace(workers=idle, worker_err=True, cancelled=True, out=_set(s.out, u, RECORDING))
            if result == "failed":
                out, front = _set(s.out, u, FAILED), _set(s.front, u, F_FAIL)
                yield s._replace(workers=idle, out=out, front=front, failed=s.failed + 1)
            elif result == "skipped":
                out, front = _set(s.out, u, SKIPPED), _set(s.front, u, F_SKIP)
                yield s._replace(workers=idle, out=out, front=front, skipped=s.skipped + 1)
            else:
                html_only = result == "html_only"
                front = s.front if (html_only and flags.no_mark_without_markdown) else _set(s.front, u, F_OK)
                cache = s.cache if html_only else _set(s.cache, u, True)
                yield s._replace(
                    workers=idle, out=_set(s.out, u, SAVED), front=front, cache=cache, saved=s.saved + 1
                )  # fmt: skip

    def finish(s):
        """Consumer: all workers exited and discovery done -> exactly one terminal event."""
        if flags.sentinel_after_error_missing and s.disc_err:
            return  # wait_for_drain raised before event_queue.put(None): consumer waits forever
        if s.term or s.disc != ("done",) or any(w != "exit" for w in s.workers):
            return
        pending = any(f in (F_PENDING, F_PROC, F_FAIL) for f in s.front)
        if s.disc_err or s.worker_err:  # run() emits FAILED and re-raises; state kept only if work is pending
            yield s._replace(term=("failed",), cleared=not pending)
            return
        if s.cancelled:
            yield s._replace(
                term=("cancelled",), cleared=flags.clear_on_cancel and s.failed == 0 or not pending
            )
        else:
            yield s._replace(term=("completed",), cleared=s.failed == 0)

    actions = {
        "cancel": cancel, "discover": discover, "sentinel": sentinel, "get": get,
        "pipeline": pipeline, "record": record, "finish": finish,
    }  # fmt: skip

    def pending_urls(s):
        return {u for u in range(flags.n_urls) if s.front[u] in (F_PENDING, F_PROC, F_FAIL)}

    def inv_deadlock(s):  # run always terminates
        return bool(s.term) or any(list(a(s)) for a in actions.values())

    def inv_one_terminal(s):
        return len(s.term) <= 1

    def inv_one_fail_event(s):  # FETCH_FAILED count is exactly 1 for a failed URL, else 0
        return all(
            s.fail_ev[u] <= 1 and (s.out[u] != FAILED or s.fail_ev[u] == 1) for u in range(flags.n_urls)
        )

    def inv_counters(s):
        c = lambda kind: sum(1 for o in s.out if o == kind)  # noqa: E731
        return (s.saved, s.skipped, s.failed) == (c(SAVED), c(SKIPPED), c(FAILED))

    def inv_disk(s):  # manifest/cache/frontier never claim an unwritten file
        return all(s.disk[u] for u in range(flags.n_urls) if s.cache[u] or s.front[u] == F_OK)

    def inv_saved_is_done(s):  # a saved page is never re-fetched on resume
        return all(s.front[u] == F_OK for u in range(flags.n_urls) if s.out[u] == SAVED)

    def inv_resume(s):
        if not s.term:
            return True
        pend = pending_urls(s)
        if s.cleared:  # clearing must never drop unfinished work
            return not pend
        discovered = {u for u in range(flags.n_urls) if s.front[u] != F_NONE}
        done = {u for u in discovered if s.front[u] in (F_OK, F_SKIP)}
        # kept state resumes exactly the unfinished URLs (never a done one, never an empty "resume")
        return bool(pend) and pend == discovered - done

    invariants = {
        "run always terminates": inv_deadlock,
        "at most one terminal run event": inv_one_terminal,
        "one FETCH_FAILED per failed URL": inv_one_fail_event,
        "counters equal outcomes": inv_counters,
        "no claim on unwritten file": inv_disk,
        "saved page is done in frontier": inv_saved_is_done,
        "resume keeps pending, skips done": inv_resume,
    }
    return actions, invariants


def run_model(flags: Flags) -> int:
    actions, invariants = build(flags)
    return check([init_state(flags)], actions, invariants)


@pytest.mark.parametrize("flags", [Flags(), Flags(n_urls=4, n_workers=1)])
def test_streaming_run_model_holds(flags):
    states = run_model(flags)
    print(f"streaming model {flags.n_urls} urls/{flags.n_workers} workers: {states} states")
    assert states > 1000


@pytest.mark.parametrize(
    ("flags", "invariant"),
    [
        (Flags(double_fail_event=True), "one FETCH_FAILED"),
        (Flags(clear_on_cancel=True), "resume keeps pending"),
        (Flags(worker_exits_on_cancel=True, n_urls=4, n_workers=1), "terminates"),
        (Flags(sentinel_after_error_missing=True), "terminates"),
        (Flags(no_mark_without_markdown=True), "saved page is done"),
    ],
)
def test_model_catches_each_fixed_bug(flags, invariant):
    with pytest.raises(AssertionError, match=invariant):
        run_model(flags)


# --- sequential loop (legacy mode and resume): _fetch_collected inside run()'s try ---


def test_sequential_resume_loop_model():
    """URLs are processed in order; cancel/error may hit between any two; always one terminal event."""
    # state: (index, cancelled, failed_count, error, terms)
    init = (0, False, 0, False, ())

    def step(s):
        i, c, f, e, t = s
        if t:
            return
        if not c:
            yield (i, True, f, e, t)
        yield (i, c, f, True, t)  # unexpected error (frontier I/O); run() now catches it
        if e:
            yield (i, c, f, e, ("failed",))
        elif c:
            yield (i, c, f, e, ("cancelled",))
        elif i == 2:
            yield (i, c, f, e, ("completed",))
        else:
            yield (i + 1, c, f, e, t)
            yield (i + 1, c, f + 1, e, t)

    n = check(
        [init],
        {"step": step},
        {
            "one terminal": lambda s: len(s[4]) <= 1,
            "terminates": lambda s: bool(s[4]) or bool(list(step(s))),
        },
    )
    print(f"sequential model: {n} states")
    assert n > 10
