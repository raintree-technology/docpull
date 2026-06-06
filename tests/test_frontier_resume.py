"""Regression tests for durable crawl frontier state."""

from __future__ import annotations

from docpull.cache.frontier import FrontierState, FrontierStore


def test_frontier_persists_terminal_state_immediately(tmp_path):
    path = tmp_path / "frontier.json"
    fingerprint = {"schema_version": 1, "start_url": "https://example.com"}
    store = FrontierStore(path)
    store.initialize(start_url="https://example.com", run_fingerprint=fingerprint)
    store.add_many(["https://example.com/a", "https://example.com/b"], source="discovery")
    store.save()

    store.mark_processing("https://example.com/a")
    store.mark_succeeded("https://example.com/a")

    reloaded = FrontierStore(path)
    assert reloaded.compatible(start_url="https://example.com", run_fingerprint=fingerprint)
    assert reloaded.entries["https://example.com/a"].state == FrontierState.SUCCEEDED
    assert reloaded.pending_urls() == ["https://example.com/b"]


def test_frontier_reinitializes_on_fingerprint_change(tmp_path):
    path = tmp_path / "frontier.json"
    store = FrontierStore(path)
    store.initialize(start_url="https://example.com", run_fingerprint={"max_pages": 1})
    store.add("https://example.com/a")
    store.save()

    store.initialize(start_url="https://example.com", run_fingerprint={"max_pages": 2})

    assert store.entries == {}
    assert store.compatible(start_url="https://example.com", run_fingerprint={"max_pages": 2})
