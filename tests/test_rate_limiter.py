"""Tests for per-host rate limiter state and adaptive backoff."""

from __future__ import annotations

import pytest

from docpull.http.rate_limiter import AdaptiveRateLimiter, PerHostRateLimiter


@pytest.mark.asyncio
async def test_per_host_rate_limiter_tracks_hosts_and_custom_config() -> None:
    limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)

    async with limiter.limit("https://docs.example.com/page"):
        pass

    limiter.update_host_config("api.example.com", delay=1.5, concurrent=2)

    assert limiter._get_host("https://docs.example.com/page") == "docs.example.com"
    assert limiter._get_config("api.example.com") == (1.5, 2)
    assert limiter.get_stats() == {"hosts_tracked": 1, "custom_configs": 1}


@pytest.mark.asyncio
async def test_adaptive_rate_limiter_backs_off_and_recovers() -> None:
    limiter = AdaptiveRateLimiter(
        default_delay=1.0,
        min_delay=0.25,
        max_delay=10.0,
        backoff_factor=2.0,
        success_threshold=2,
    )
    url = "https://api.example.com/v1/search"

    await limiter.record_rate_limit(url)
    assert limiter._get_config("api.example.com") == (2.0, 3)
    assert limiter.get_stats()["adapted_hosts"] == 1

    await limiter.record_success(url)
    assert limiter._get_config("api.example.com") == (2.0, 3)

    await limiter.record_success(url)
    assert limiter._get_config("api.example.com") == (1.0, 3)

    await limiter.record_rate_limit(url, retry_after=99)
    assert limiter._get_config("api.example.com") == (10.0, 3)
