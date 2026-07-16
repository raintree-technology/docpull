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


def test_frontier_transitions_append_then_compact(tmp_path):
    path = tmp_path / "frontier.json"
    store = FrontierStore(path)
    store.initialize(start_url="https://example.com", run_fingerprint={"max_pages": 2})
    store.add_many(["https://example.com/a", "https://example.com/b"])
    store.save()
    snapshot_before = path.read_bytes()

    store.mark_processing("https://example.com/a")
    store.mark_succeeded("https://example.com/a")

    assert path.read_bytes() == snapshot_before
    assert store.journal_path.exists()
    assert len(store.journal_path.read_text(encoding="utf-8").splitlines()) == 2
    assert FrontierStore(path).entries["https://example.com/a"].state == FrontierState.SUCCEEDED

    store.flush()

    assert not store.journal_path.exists()
    assert FrontierStore(path).entries["https://example.com/a"].state == FrontierState.SUCCEEDED


def test_frontier_recovers_after_truncated_journal_tail(tmp_path):
    path = tmp_path / "frontier.json"
    store = FrontierStore(path)
    store.initialize(start_url="https://example.com", run_fingerprint={"max_pages": 2})
    store.add_many(["https://example.com/a", "https://example.com/b"])
    store.save()
    store.mark_succeeded("https://example.com/a")

    with store.journal_path.open("a", encoding="utf-8") as journal:
        journal.write('{"schema_version":1,"entry":')

    recovered = FrontierStore(path)
    recovered.mark_succeeded("https://example.com/b")
    reloaded = FrontierStore(path)

    assert reloaded.entries["https://example.com/a"].state == FrontierState.SUCCEEDED
    assert reloaded.entries["https://example.com/b"].state == FrontierState.SUCCEEDED


def test_frontier_ignores_journal_records_from_other_schema_versions(tmp_path):
    path = tmp_path / "frontier.json"
    store = FrontierStore(path)
    store.initialize(start_url="https://example.com", run_fingerprint={"max_pages": 2})
    store.add("https://example.com/a")
    store.save()
    store.journal_path.write_text(
        '{"schema_version":999,"entry":{"url":"https://example.com/a","state":"succeeded"}}\n',
        encoding="utf-8",
    )

    assert FrontierStore(path).entries["https://example.com/a"].state == FrontierState.QUEUED
