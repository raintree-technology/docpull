"""Tiny explicit-state model checker for the protocol models in tests/test_model_*.py.

Breadth-first search over every reachable state, TLC style. A model is:

- ``init``: iterable of hashable initial states
- ``actions``: name -> fn(state) -> iterable of next states (empty when disabled)
- ``invariants``: name -> fn(state) -> bool

On a violation, raises AssertionError with the shortest trace of action names.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Hashable, Iterable, Mapping

State = Hashable


def check(
    init: Iterable[State],
    actions: Mapping[str, Callable[[State], Iterable[State]]],
    invariants: Mapping[str, Callable[[State], bool]],
    *,
    max_states: int = 200_000,
) -> int:
    """Explore all reachable states; return how many were visited."""
    parent: dict[State, tuple[State, str] | None] = {}
    queue: deque[State] = deque()
    for state in init:
        if state not in parent:
            parent[state] = None
            queue.append(state)
    while queue:
        state = queue.popleft()
        for name, holds in invariants.items():
            if not holds(state):
                raise AssertionError(f"invariant {name!r} violated:\n{_trace(parent, state)}")
        for action, step in actions.items():
            for nxt in step(state):
                if nxt not in parent:
                    parent[nxt] = (state, action)
                    queue.append(nxt)
        if len(parent) > max_states:
            raise AssertionError(f"state space exceeded {max_states}; shrink the model bounds")
    return len(parent)


def _trace(parent: Mapping[State, tuple[State, str] | None], state: State) -> str:
    steps: list[str] = []
    while (link := parent[state]) is not None:
        prev, action = link
        steps.append(f"  --{action}--> {state!r}")
        state = prev
    steps.append(f"  init {state!r}")
    return "\n".join(reversed(steps))


if __name__ == "__main__":
    # Self-check: a counter that must stay below 3 fails after three increments.
    try:
        check([0], {"inc": lambda n: [n + 1] if n < 5 else []}, {"lt3": lambda n: n < 3})
    except AssertionError as exc:
        assert "--inc--> 3" in str(exc), exc
    else:
        raise SystemExit("model checker missed a reachable violation")
    assert check([0], {"inc": lambda n: [n + 1] if n < 2 else []}, {"lt3": lambda n: n < 3}) == 3
    print("ok")
