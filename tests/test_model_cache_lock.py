"""Explicit-state model of project sync / lock / frozen install (see docs/formal/cache-lock.md).

Each ``Impl`` flag is one real code behavior. FIXED mirrors src/docpull/project.py today;
every mutant re-enables one bug found by this model and must violate an invariant, which
proves the invariants can see it. The matching real-code reproductions live in
tests/test_cache_lock_regressions.py.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import NamedTuple

import pytest

from tests.model_check import check

SOURCES = ("a", "b")
MAX_VER = 2  # upstream content versions per source
MAX_RUNS = 2


@dataclass(frozen=True)
class Impl:
    atomic_writes: bool = True  # _replace_atomically in save_project_config/_write_json
    gate_on_success: bool = True  # _finalize_project_run: only status == "success" moves the lock
    gate_on_complete: bool = True  # scoped / source-dropping runs keep the old pin
    dry_run_pure: bool = True  # dry run neither refreshes discovery nor saves config
    export_keeps_pin: bool = True  # export_context_pack records the export without repinning


FIXED = Impl()
MUTANTS = {
    "non-atomic writes": replace(FIXED, atomic_writes=False),
    "failed runs move lock": replace(FIXED, gate_on_success=False),
    "scoped/degraded runs move lock": replace(FIXED, gate_on_complete=False),
    "dry run writes config": replace(FIXED, dry_run_pure=False),
    "export repins lock": replace(FIXED, export_keeps_pin=False),
}


class Run(NamedTuple):
    rid: int
    kind: str  # good | partial (scoped or lost a source) | failed | dry
    records: frozenset  # {(source, version)}


class Lock(NamedTuple):
    rid: int
    records: frozenset
    sources: frozenset
    exports: frozenset = frozenset()
    torn: bool = False


class Pending(NamedTuple):
    """A sync in flight: writes happen in this order, a crash may stop after any of them."""

    run: Run
    step: int  # 0 run dir written, 1 artifact archived, 2 lock written, 3 latest written
    pin: bool


class S(NamedTuple):
    cfg: frozenset = frozenset(SOURCES)
    cfg_torn: bool = False
    up: tuple = (1, 1)  # upstream version per source, in SOURCES order
    cache: tuple = (None, None)  # cached version per source (304 path)
    runs: tuple = ()
    artifacts: frozenset = frozenset()  # {(rid, records)} archived zips
    lock: Lock | None = None
    latest: int | None = None
    pending: Pending | None = None
    net: int = 0
    last: str = ""
    viol: str = ""  # set by a transition that breaks a rule about that transition itself


def _up(s: S, src: str) -> int:
    return s.up[SOURCES.index(src)]


def build(impl: Impl):
    def step(s: S, last: str, **changes) -> S:
        return s._replace(last=last, viol="", **changes)

    # --- upstream world ---------------------------------------------------
    def upstream_change(s: S):
        for i in range(len(SOURCES)):
            if s.up[i] < MAX_VER:
                up = list(s.up)
                up[i] += 1
                yield step(s, "upstream_change", up=tuple(up))

    # --- config edits (docpull add, editing docpull.yaml) -----------------
    def edit_config(s: S):
        if s.pending or s.cfg_torn:
            return
        for src in SOURCES:
            new = s.cfg | {src} if src not in s.cfg else s.cfg - {src}
            if new:
                yield step(s, "edit_config", cfg=new)
        # crash mid-save: atomic write leaves the old file, a plain write_text tears it
        if not impl.atomic_writes:
            yield step(s, "crash_save_config", cfg_torn=True)

    # --- sync ---------------------------------------------------------------
    def start_sync(s: S):
        if s.pending or s.cfg_torn or len(s.runs) >= MAX_RUNS:
            return
        rid = len(s.runs) + 1
        scopes = [None, *sorted(s.cfg)] if len(s.cfg) > 1 else [None]
        for scope in scopes:
            selected = sorted(s.cfg if scope is None else {scope})
            for outcome in _outcomes(selected):
                records = frozenset((src, _up(s, src)) for src in selected if outcome[src])
                lost = any(not ok for ok in outcome.values())
                if not records and lost:
                    kind = "failed"
                elif scope is not None or lost:
                    kind = "partial"
                else:
                    kind = "good"
                pin = kind == "good" or (not impl.gate_on_success and kind == "failed")
                if not impl.gate_on_complete and kind in {"partial", "good"} and records:
                    pin = True
                # a 304 keeps the cached content; a 200 replaces it; neither ever empties it
                cache = list(s.cache)
                for src in selected:
                    if outcome[src]:
                        cache[SOURCES.index(src)] = _up(s, src)
                run = Run(rid, kind, records)
                yield step(
                    s,
                    f"sync[{scope or 'all'}]",
                    cache=tuple(cache),
                    net=min(s.net + len(selected), 3),
                    runs=(*s.runs, run),
                    pending=Pending(run, 0, pin),
                )

    def dry_run(s: S):
        if s.pending or s.cfg_torn or len(s.runs) >= MAX_RUNS:
            return
        run = Run(len(s.runs) + 1, "dry", frozenset())
        # discovery refresh would hit the network and rewrite docpull.yaml
        changes = {} if impl.dry_run_pure else {"net": min(s.net + 1, 3)}
        viol = "" if impl.dry_run_pure else "dry run refreshed discovery and rewrote docpull.yaml"
        yield step(s, "dry_run", runs=(*s.runs, run), pending=Pending(run, 0, False), **changes)._replace(
            viol=viol
        )

    def advance(s: S):
        p = s.pending
        if p is None:
            return
        run, n = p.run, p.step
        if n == 0:  # run dir + documents written; the archive is only needed to pin
            yield step(s, "write_run_dir", pending=p._replace(step=1))
        elif n == 1:
            arts = s.artifacts | {(run.rid, run.records)} if p.pin else s.artifacts
            yield step(s, "write_artifact", artifacts=arts, pending=p._replace(step=2))
        elif n == 2:
            if not p.pin:
                yield step(s, "skip_lock", pending=p._replace(step=3))
                return
            lock = Lock(run.rid, run.records, s.cfg, s.lock.exports if s.lock else frozenset())
            yield step(s, "write_lock", lock=lock, pending=p._replace(step=3))
            if not impl.atomic_writes:
                yield step(s, "crash_in_lock", lock=lock._replace(torn=True), pending=None)
        else:
            latest = run.rid if run.kind in {"good", "partial"} else s.latest
            yield step(s, "write_latest", latest=latest, pending=None)

    def crash(s: S):
        if s.pending:
            yield step(s, "crash", pending=None)

    # --- exports ------------------------------------------------------------
    def export(s: S):
        if s.pending or s.cfg_torn:
            return
        for run in s.runs:
            if run.kind == "dry":
                continue
            old = s.lock or Lock(0, frozenset(), s.cfg)  # no lock yet: a skeleton, rid 0
            exports = old.exports | {run.rid}
            if impl.export_keeps_pin:
                yield step(s, f"export[{run.rid}]", lock=old._replace(exports=exports))
            else:  # export_context_pack used to repin the lock to the exported run
                lock = Lock(run.rid, run.records, s.cfg, exports)
                arts = s.artifacts | {(run.rid, run.records)}
                back = old.rid > run.rid
                yield step(s, f"export[{run.rid}]", lock=lock, artifacts=arts)._replace(
                    viol="export moved the pin backwards" if back else ""
                )

    # --- frozen install -----------------------------------------------------
    def frozen(s: S):
        lock = s.lock
        if s.pending or s.cfg_torn or not lock or lock.torn or lock.sources != s.cfg:
            return
        if (lock.rid, lock.records) not in s.artifacts:
            return  # real code raises "Missing frozen artifact" before touching anything
        after = step(s, "frozen_install", latest=lock.rid)  # restores run dir only
        viol = (
            ""
            if (after.lock, after.net, after.cfg) == (s.lock, s.net, s.cfg)
            else "frozen install mutated state"
        )
        yield after._replace(viol=viol)

    actions = {
        "upstream_change": upstream_change,
        "edit_config": edit_config,
        "start_sync": start_sync,
        "dry_run": dry_run,
        "advance": advance,
        "crash": crash,
        "export": export,
        "frozen": frozen,
    }

    # --- invariants ----------------------------------------------------------
    def run_of(s: S, rid: int):
        return next((r for r in s.runs if r.rid == rid), None)

    def lock_matches_cached_content(s: S) -> bool:
        lock = s.lock
        if lock is None or lock.rid == 0 or s.pending:
            return True
        run = run_of(s, lock.rid)
        return run is not None and lock.records == run.records and (lock.rid, lock.records) in s.artifacts

    def crash_never_leaves_lock_unreadable(s: S) -> bool:
        return not (s.lock and s.lock.torn) and not s.cfg_torn

    def lock_covers_every_locked_source(s: S) -> bool:
        lock = s.lock
        if lock is None or lock.rid == 0:
            return True
        return {src for src, _ in lock.records} >= lock.sources

    def removed_source_leaves_lock_on_next_full_sync(s: S) -> bool:
        lock = s.lock
        return lock is None or {src for src, _ in lock.records} <= lock.sources

    def only_complete_successful_runs_pin(s: S) -> bool:
        # A crash before the lock write may leave an older good pin; it must never be a bad run.
        good = {r.rid for r in s.runs if r.kind == "good"}
        return s.lock is None or s.lock.rid in good | {0}

    def transitions_obey_their_own_rules(s: S) -> bool:
        return not s.viol

    def failed_and_dry_runs_leave_lock(s: S) -> bool:
        if s.last in {"write_lock", "crash_in_lock"} and s.pending is not None:
            return s.pending.run.kind == "good" or s.pending.pin is False
        return True

    def cache_never_emptied_and_runs_fresh(s: S) -> bool:
        # 304 or failure leaves the cached version alone; a run only records upstream-current content
        return all(ver <= s.up[SOURCES.index(src)] for run in s.runs for src, ver in run.records) and all(
            c is None or c >= 1 for c in s.cache
        )

    invariants = {
        "lock hash matches the cached content it points to": lock_matches_cached_content,
        "crash never leaves lock or config unreadable": crash_never_leaves_lock_unreadable,
        "every locked source has pinned evidence": lock_covers_every_locked_source,
        "removed sources leave lock and outputs": removed_source_leaves_lock_on_next_full_sync,
        "only complete successful runs pin the lock": only_complete_successful_runs_pin,
        "export never repins; dry runs and frozen installs never write config, lock or network": (
            transitions_obey_their_own_rules
        ),
        "failed and dry runs do not move the lock": failed_and_dry_runs_leave_lock,
        "304 and stale never replace or outlive upstream": cache_never_emptied_and_runs_fresh,
    }
    return [S()], actions, invariants


def _outcomes(selected: list[str]):
    """Each selected source either syncs or raises source_sync_failed."""
    if len(selected) == 1:
        yield {selected[0]: True}
        yield {selected[0]: False}
        return
    for a in (True, False):
        for b in (True, False):
            yield {selected[0]: a, selected[1]: b}


def test_fixed_implementation_satisfies_every_invariant() -> None:
    init, actions, invariants = build(FIXED)
    states = check(init, actions, invariants, max_states=300_000)
    print(f"cache-lock model: {states} states, {len(invariants)} invariants")
    assert states > 1_000


@pytest.mark.parametrize("name", MUTANTS)
def test_each_known_bug_is_caught_by_an_invariant(name: str) -> None:
    init, actions, invariants = build(MUTANTS[name])
    with pytest.raises(AssertionError, match="violated") as caught:
        check(init, actions, invariants, max_states=300_000)
    print(f"{name}: {str(caught.value).splitlines()[0]}")
