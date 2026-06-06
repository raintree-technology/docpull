from __future__ import annotations

import json
from datetime import timedelta

from docpull.cache import CacheManager
from docpull.time_utils import utc_now


def test_cache_state_persists_deterministically(tmp_path):
    cache = CacheManager(tmp_path)

    cache.mark_fetched("https://example.com/b")
    cache.mark_fetched("https://example.com/a")
    cache.mark_failed("https://example.com/d")
    cache.mark_failed("https://example.com/c")
    cache.flush()

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["fetched_urls"] == ["https://example.com/a", "https://example.com/b"]
    assert state["failed_urls"] == ["https://example.com/c", "https://example.com/d"]


def test_mark_fetched_clears_stale_failure_state(tmp_path):
    cache = CacheManager(tmp_path)

    cache.mark_failed("https://example.com/page")
    cache.mark_fetched("https://example.com/page")
    cache.flush()

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["fetched_urls"] == ["https://example.com/page"]
    assert state["failed_urls"] == []


def test_mark_failed_clears_stale_fetched_state(tmp_path):
    cache = CacheManager(tmp_path)

    cache.mark_fetched("https://example.com/page")
    cache.mark_failed("https://example.com/page")
    cache.flush()

    state = json.loads((tmp_path / "state.json").read_text())
    assert state["fetched_urls"] == []
    assert state["failed_urls"] == ["https://example.com/page"]


def test_update_cache_records_byte_size_for_text(tmp_path):
    cache = CacheManager(tmp_path)

    cache.update_cache("https://example.com/page", "snowman: \u2603", tmp_path / "page.md")

    assert cache.manifest["https://example.com/page"]["size"] == len("snowman: \u2603".encode())


def test_evict_expired_removes_matching_resume_state(tmp_path):
    cache = CacheManager(tmp_path, ttl_days=30)
    url = "https://example.com/old"
    old_timestamp = (utc_now() - timedelta(days=31)).isoformat()
    cache.manifest[url] = {
        "checksum": "abc",
        "file_path": "old.md",
        "fetched_at": old_timestamp,
        "size": 3,
    }
    cache.mark_fetched(url)

    evicted = cache.evict_expired()

    assert evicted == 1
    assert url not in cache.manifest
    assert url not in cache.get_fetched_urls()


def test_malformed_manifest_and_state_load_as_empty_cache(tmp_path):
    (tmp_path / "manifest.json").write_text('["not", "a", "manifest"]')
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "fetched_urls": "https://example.com/not-a-list",
                "failed_urls": [1, "https://example.com/failed"],
                "last_run": 123,
            }
        )
    )

    cache = CacheManager(tmp_path)

    assert cache.manifest == {}
    assert cache.get_fetched_urls() == set()
    cache.flush()


def test_discovered_urls_loader_ignores_non_string_urls(tmp_path):
    cache = CacheManager(tmp_path)
    (tmp_path / "discovered_urls.json").write_text(
        json.dumps(
            {
                "start_url": "https://example.com",
                "urls": ["https://example.com/a", 1, None, "https://example.com/b"],
            }
        )
    )

    assert cache.load_discovered_urls("https://example.com") == [
        "https://example.com/a",
        "https://example.com/b",
    ]


def test_discovered_urls_loader_rejects_mismatched_fingerprint(tmp_path):
    cache = CacheManager(tmp_path)
    (tmp_path / "discovered_urls.json").write_text(
        json.dumps(
            {
                "start_url": "https://example.com",
                "config_fingerprint": {"version": 1, "max_depth": 2},
                "urls": ["https://example.com/a"],
            }
        )
    )

    assert (
        cache.load_discovered_urls(
            "https://example.com",
            config_fingerprint={"version": 1, "max_depth": 3},
        )
        is None
    )


def test_discovered_urls_round_trip_persists_fingerprint(tmp_path):
    cache = CacheManager(tmp_path)
    fingerprint = {"version": 1, "max_depth": 3, "include_paths": ["/docs/*"]}

    cache.save_discovered_urls(
        ["https://example.com/a"],
        "https://example.com",
        config_fingerprint=fingerprint,
    )

    assert cache.load_discovered_urls("https://example.com", config_fingerprint=fingerprint) == [
        "https://example.com/a"
    ]
