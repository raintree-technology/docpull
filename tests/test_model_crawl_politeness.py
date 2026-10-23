"""Explicit-state models of the crawl frontier, redirect safety and politeness protocols.

Each model mirrors real branches (see the code references in each section). Flags re-enable a
historical or still-open defect so the checker can prove it would have been caught.
Chart: docs/formal/crawl-politeness.md.
"""

from __future__ import annotations

from typing import NamedTuple

import pytest

from tests.model_check import check

# --------------------------------------------------------------------------------------
# Model 1: discovery -> frontier -> workers, with crash and resume
#   src/docpull/discovery/crawler.py, cache/frontier.py, cache/manager.py, core/fetcher.py
# --------------------------------------------------------------------------------------

N = 4
DEPTH = (0, 1, 1, 2)
PARENT = (None, 0, 0, 1)
MAX_DEPTH = 1
MAX_PAGES = 3
WORKERS = 2
MAX_CRASHES = 2
NONE, QUEUED, PROCESSING, SUCCEEDED, SKIPPED, FAILED = range(6)
TERMINAL = (SUCCEEDED, SKIPPED)
RUNNING, RESUMED, CRASHED = range(3)


class Crawl(NamedTuple):
    phase: int = RUNNING
    disc: tuple[int, ...] = ()  # in-memory discovered list (lost on crash)
    queue: tuple[int, ...] = ()  # in-memory worker queue (lost on crash)
    workers: tuple[int | None, ...] = (None,) * WORKERS
    disc_done: bool = False
    fr: tuple[int, ...] = (NONE,) * N  # persisted frontier state per URL
    att: tuple[int, ...] = (0,) * N  # persisted attempts
    ok: tuple[int, ...] = (0,) * N  # ground truth: completed fetches per URL this crawl
    saved: tuple[bool, ...] = (False,) * N  # ground truth: URL reached disk
    inited: bool = False
    crashes: int = 0
    bad: str = ""


def _set(t: tuple, i: int, v) -> tuple:
    return t[:i] + (v,) + t[i + 1 :]


def crawl_model(*, wipe_on_first_snapshot: bool = False):
    def discover(s: Crawl):
        if s.phase != RUNNING or s.disc_done or len(s.disc) >= MAX_PAGES:
            return
        for u in range(N):
            parent_ok = PARENT[u] is None or PARENT[u] in s.disc
            if u not in s.disc and DEPTH[u] <= MAX_DEPTH and parent_ok:
                yield s._replace(disc=s.disc + (u,), queue=s.queue + (u,))

    def snapshot(s: Crawl, *, end: bool = False):
        if s.phase != RUNNING or s.disc_done:
            return
        fr, saved = s.fr, s.saved
        if not s.inited and wipe_on_first_snapshot:
            fr = (NONE,) * N  # FrontierStore.initialize cleared entries finished before the snapshot
        for u in s.disc:
            if fr[u] == NONE:
                fr = _set(fr, u, QUEUED)
            saved = _set(saved, u, True)
        yield s._replace(fr=fr, saved=saved, inited=True, disc_done=end or s.disc_done)

    def save(s: Crawl):
        return snapshot(s)

    def finish_discovery(s: Crawl):
        return snapshot(s, end=True)

    def take(s: Crawl):
        if not s.queue:
            return
        u = s.queue[0]
        for w, slot in enumerate(s.workers):
            if slot is None:
                bad = s.bad or ("refetch after success" if s.ok[u] else "")
                yield s._replace(
                    queue=s.queue[1:],
                    workers=_set(s.workers, w, u),
                    fr=_set(s.fr, u, PROCESSING),
                    att=_set(s.att, u, s.att[u] + 1),
                    saved=_set(s.saved, u, True),
                    bad=bad,
                )
                return  # workers are interchangeable

    def finish(s: Crawl):
        for w, u in enumerate(s.workers):
            if u is None:
                continue
            base = s._replace(workers=_set(s.workers, w, None))
            yield base._replace(fr=_set(s.fr, u, SUCCEEDED), ok=_set(s.ok, u, s.ok[u] + 1))
            yield base._replace(fr=_set(s.fr, u, FAILED))
            yield base._replace(fr=_set(s.fr, u, SKIPPED))
            return

    def crash(s: Crawl):
        if s.phase != CRASHED and s.crashes < MAX_CRASHES:
            yield s._replace(
                phase=CRASHED,
                disc=(),
                queue=(),
                workers=(None,) * WORKERS,
                crashes=s.crashes + 1,
            )

    def resume(s: Crawl):
        if s.phase != CRASHED:
            return
        if s.inited:
            pending = tuple(u for u in range(N) if s.fr[u] not in (NONE, *TERMINAL))
            yield s._replace(phase=RESUMED, queue=pending)
        else:  # no discovered-URL record: a fresh crawl starts over
            yield Crawl(crashes=s.crashes)

    actions = {
        "discover": discover,
        "save": save,
        "finish_discovery": finish_discovery,
        "take": take,
        "finish": finish,
        "crash": crash,
        "resume": resume,
    }

    def complete(s: Crawl) -> bool:
        drained = not s.queue and all(w is None for w in s.workers)
        return drained and (s.phase == RESUMED or (s.phase == RUNNING and s.disc_done))

    invariants = {
        "no URL fetched twice after success": lambda s: s.bad == "" and all(c <= 1 for c in s.ok),
        "page limit": lambda s: sum(f != NONE for f in s.fr) <= MAX_PAGES,
        "depth limit": lambda s: all(DEPTH[u] <= MAX_DEPTH for u in range(N) if s.fr[u] != NONE),
        "retries bounded by resumes": lambda s: all(a <= 1 + s.crashes for a in s.att),
        "completed crawl leaves nothing queued or in flight": lambda s: (
            not complete(s) or all(s.fr[u] in (NONE, FAILED, *TERMINAL) for u in range(N))
        ),
        "completed crawl covered every URL that reached disk": lambda s: (
            not complete(s) or all(s.fr[u] != NONE for u in range(N) if s.saved[u])
        ),
    }
    return [Crawl()], actions, invariants


def test_crawl_resume_model_holds():
    states = check(*crawl_model())
    assert states > 1_000


def test_crawl_model_catches_first_snapshot_wipe():
    with pytest.raises(AssertionError, match="fetched twice|leaves nothing queued"):
        check(*crawl_model(wipe_on_first_snapshot=True))


# --------------------------------------------------------------------------------------
# Model 2: redirect chain safety   src/docpull/http/client.py _next_redirect / _get_uncached
# --------------------------------------------------------------------------------------

KINDS = ("public", "public", "private", "robots-denied")
MAX_REDIRECTS = 2


class Hop(NamedTuple):
    cur: int | None = None  # node of the request in flight
    hops: int = 0
    sent: tuple[int, ...] = ()  # nodes a GET was actually sent to
    ended: bool = False


def redirect_model(*, robots_on_hops: bool):
    def start(s: Hop):
        if s.cur is None and not s.ended:
            for u in (0, 1):  # ValidateStep admitted only public, robots-allowed starts
                yield s._replace(cur=u, sent=s.sent + (u,))

    def answer_200(s: Hop):
        if s.cur is not None:
            yield s._replace(cur=None, ended=True)

    def redirect_to(s: Hop):
        if s.cur is None:
            return
        for t in range(len(KINDS)):
            if s.hops >= MAX_REDIRECTS:
                yield s._replace(cur=None, ended=True)  # "Too many redirects"
            elif KINDS[t] == "private" or (robots_on_hops and KINDS[t] == "robots-denied"):
                yield s._replace(cur=None, ended=True)  # hop rejected before any GET
            else:
                yield s._replace(cur=t, hops=s.hops + 1, sent=s.sent + (t,))

    invariants = {
        "private target never requested": lambda s: all(KINDS[t] != "private" for t in s.sent),
        "robots-denied target never requested": lambda s: all(KINDS[t] != "robots-denied" for t in s.sent),
        "redirect hops bounded": lambda s: s.hops <= MAX_REDIRECTS,
    }
    return [Hop()], {"start": start, "200": answer_200, "redirect": redirect_to}, invariants


def test_redirect_model_ssrf_and_hop_bound_hold():
    model = redirect_model(robots_on_hops=True)
    assert check(*model) > 10


def test_model_catches_missing_robots_check_on_redirects():
    """Pre-fix behaviour: SSRF was re-validated per hop but robots.txt was not."""
    with pytest.raises(AssertionError, match="robots-denied target never requested"):
        check(*redirect_model(robots_on_hops=False))


# --------------------------------------------------------------------------------------
# Model 3: per-host concurrency, spacing and adaptive floor   src/docpull/http/rate_limiter.py
# --------------------------------------------------------------------------------------

CONCURRENT = 1
DELAY = 2  # robots Crawl-delay applied via update_host_config
TICKS = 4
SPELLINGS = ("docs.example.com", "Docs.Example.com")  # same real host


class Limit(NamedTuple):
    clock: int = 0
    inflight: tuple[int, ...] = (0, 0)  # per spelling
    last: tuple[int, ...] = (-DELAY, -DELAY)  # per spelling
    delay: int = DELAY
    min_gap: int = DELAY  # smallest observed gap between request starts to the real host
    last_real: int = -DELAY


def limiter_model(*, normalize_host: bool, floor: bool):
    def key(i: int) -> int:
        return 0 if normalize_host else i

    def tick(s: Limit):
        if s.clock < TICKS:
            yield s._replace(clock=s.clock + 1)

    def start(s: Limit):
        for i in range(len(SPELLINGS)):
            k = key(i)
            same = [j for j in range(len(SPELLINGS)) if key(j) == k]
            if sum(s.inflight[j] for j in same) >= CONCURRENT:
                continue
            if s.clock - max(s.last[j] for j in same) < s.delay:
                continue
            gap = min(s.min_gap, s.clock - s.last_real)
            yield s._replace(
                inflight=_set(s.inflight, i, s.inflight[i] + 1),
                last=_set(s.last, i, s.clock),
                min_gap=gap,
                last_real=s.clock,
            )

    def finish(s: Limit):
        for i, n in enumerate(s.inflight):
            if n:
                yield s._replace(inflight=_set(s.inflight, i, n - 1))

    def adapt_down(s: Limit):  # record_success after the threshold
        lowest = DELAY if floor else 0
        if s.delay > lowest:
            yield s._replace(delay=max(s.delay - 1, lowest))

    def rate_limited(s: Limit):  # record_rate_limit with a short Retry-After
        yield s._replace(delay=max(1, DELAY if floor else 0))

    invariants = {
        "per-host concurrency bound": lambda s: sum(s.inflight) <= CONCURRENT,
        "crawl-delay spacing between starts": lambda s: s.min_gap >= DELAY,
    }
    actions = {"tick": tick, "start": start, "finish": finish, "adapt": adapt_down, "429": rate_limited}
    return [Limit()], actions, invariants


def test_limiter_model_holds():
    assert check(*limiter_model(normalize_host=True, floor=True)) > 10


@pytest.mark.parametrize(
    ("kwargs", "violated"),
    [
        ({"normalize_host": False, "floor": True}, "per-host concurrency bound"),
        ({"normalize_host": True, "floor": False}, "crawl-delay spacing between starts"),
    ],
)
def test_limiter_model_catches_fixed_bugs(kwargs, violated):
    with pytest.raises(AssertionError, match=violated):
        check(*limiter_model(**kwargs))


# --------------------------------------------------------------------------------------
# Model 4: sitemap index traversal   src/docpull/discovery/sitemap.py
# --------------------------------------------------------------------------------------

SITEMAPS = 3
SITEMAP_DEPTH = 2  # stands in for MAX_SITEMAP_DEPTH


class Walk(NamedTuple):
    pending: tuple[tuple[int, int], ...] = ((0, 0),)
    seen: frozenset[int] = frozenset()
    fetches: tuple[int, ...] = (0,) * SITEMAPS


def sitemap_model(*, visited_set: bool):
    def step(s: Walk):
        if not s.pending:
            return
        (sm, depth), rest = s.pending[0], s.pending[1:]
        if depth > SITEMAP_DEPTH or (visited_set and sm in s.seen):
            yield s._replace(pending=rest)
            return
        children = tuple((c, depth + 1) for c in range(SITEMAPS))  # every index lists every sitemap
        yield Walk(rest + children, s.seen | {sm}, _set(s.fetches, sm, s.fetches[sm] + 1))

    invariants = {"each sitemap fetched at most once": lambda s: all(f <= 1 for f in s.fetches)}
    return [Walk()], {"step": step}, invariants


def test_sitemap_walk_model_holds():
    assert check(*sitemap_model(visited_set=True)) > 3


def test_sitemap_walk_model_catches_reference_cycle():
    with pytest.raises(AssertionError, match="fetched at most once"):
        check(*sitemap_model(visited_set=False))
