from __future__ import annotations

import pytest

from docpull_bench import tokens


def test_fallback_estimator_is_deterministic_and_labeled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tokens, "_encoder", None)
    estimate = tokens.estimate_tokens("four words here now")
    assert estimate.tokens == 4
    assert estimate.estimator == tokens.HEURISTIC_ESTIMATOR
    assert tokens.estimate_tokens("four words here now") == estimate


def test_fallback_estimator_uses_character_floor_for_dense_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tokens, "_encoder", None)
    dense = "x" * 400
    assert tokens.estimate_tokens(dense).tokens == 100
    assert tokens.estimate_tokens("").tokens == 0
