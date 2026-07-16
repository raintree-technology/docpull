"""Performance-contract tests for the shared HTTP transport."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from docpull.http.client import AsyncHttpClient


class _NullLimit:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _RateLimiter:
    def limit(self, _url: str) -> _NullLimit:
        return _NullLimit()


class _Content:
    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        yield b"content"


class _Response:
    status = 200
    headers = {"Content-Type": "text/html", "ETag": "test"}
    content = _Content()
    url = "https://docs.example.com/page"

    def raise_for_status(self) -> None:
        return None


class _DelayedRequest:
    def __init__(self, release: asyncio.Event) -> None:
        self._release = release

    async def __aenter__(self) -> _Response:
        await self._release.wait()
        return _Response()

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _CountingSession:
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> _DelayedRequest:
        self.calls.append((url, kwargs))
        return _DelayedRequest(self.release)


@pytest.mark.asyncio
async def test_identical_concurrent_gets_share_one_physical_request() -> None:
    release = asyncio.Event()
    session = _CountingSession(release)
    client = AsyncHttpClient(rate_limiter=_RateLimiter())  # type: ignore[arg-type]
    client._session = session  # type: ignore[assignment]

    first = asyncio.create_task(client.get("https://docs.example.com/page"))
    await asyncio.sleep(0)
    second = asyncio.create_task(client.get("https://docs.example.com/page", timeout=30.0, headers={}))
    await asyncio.sleep(0)

    assert len(session.calls) == 1
    release.set()
    first_response, second_response = await asyncio.gather(first, second)
    assert first_response.content == second_response.content == b"content"

    first_response.headers["X-Caller"] = "first"
    assert "X-Caller" not in second_response.headers


@pytest.mark.asyncio
async def test_request_headers_keep_concurrent_gets_isolated() -> None:
    release = asyncio.Event()
    session = _CountingSession(release)
    client = AsyncHttpClient(rate_limiter=_RateLimiter())  # type: ignore[arg-type]
    client._session = session  # type: ignore[assignment]

    plain = asyncio.create_task(client.get("https://docs.example.com/page"))
    conditional = asyncio.create_task(
        client.get(
            "https://docs.example.com/page",
            headers={"If-None-Match": '"cached"'},
        )
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(session.calls) == 2
    release.set()
    await asyncio.gather(plain, conditional)


@pytest.mark.asyncio
async def test_auth_header_override_spelling_does_not_coalesce_different_requests() -> None:
    release = asyncio.Event()
    session = _CountingSession(release)
    client = AsyncHttpClient(
        rate_limiter=_RateLimiter(),  # type: ignore[arg-type]
        auth_headers={"Authorization": "secret"},
    )
    client._session = session  # type: ignore[assignment]

    exact_override = asyncio.create_task(
        client.get("https://docs.example.com/page", headers={"Authorization": "override"})
    )
    differently_cased = asyncio.create_task(
        client.get("https://docs.example.com/page", headers={"authorization": "override"})
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert len(session.calls) == 2
    release.set()
    await asyncio.gather(exact_override, differently_cased)


@pytest.mark.asyncio
async def test_inflight_get_snapshots_mutable_request_headers() -> None:
    release = asyncio.Event()
    session = _CountingSession(release)
    client = AsyncHttpClient(rate_limiter=_RateLimiter())  # type: ignore[arg-type]
    client._session = session  # type: ignore[assignment]
    headers = {"If-None-Match": '"first"'}

    request = asyncio.create_task(client.get("https://docs.example.com/page", headers=headers))
    await asyncio.sleep(0)
    headers["If-None-Match"] = '"changed"'
    await asyncio.sleep(0)

    release.set()
    await request
    sent_headers = session.calls[0][1]["headers"]
    assert isinstance(sent_headers, dict)
    assert sent_headers["If-None-Match"] == '"first"'


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_get() -> None:
    release = asyncio.Event()
    session = _CountingSession(release)
    client = AsyncHttpClient(rate_limiter=_RateLimiter())  # type: ignore[arg-type]
    client._session = session  # type: ignore[assignment]

    cancelled = asyncio.create_task(client.get("https://docs.example.com/page"))
    survivor = asyncio.create_task(client.get("https://docs.example.com/page"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    release.set()

    response = await survivor
    assert response.content == b"content"
    assert len(session.calls) == 1
