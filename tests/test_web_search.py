"""Tests for web search backends and the compatible /search routes."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from aiohttp import web

from docpull import web_search
from docpull.compat_api import CompatASGIApp, Page
from docpull.web_search import SearchHit, SearchResponse, WebSearchError, run_search_cli, search_web
from tests.test_compat_api import _post

SEARXNG_PAGE = {
    "query": "q",
    "results": [
        {
            "url": "https://docs.example.com/a",
            "title": "A",
            "content": "alpha",
            "score": 2.5,
            "publishedDate": None,
        },
        {"url": "https://blog.other.org/b", "title": "B", "content": "beta", "score": 1.0},
        {"url": "https://docs.example.com/a", "title": "A dup", "content": "dup"},
        {"url": "ftp://example.com/file", "title": "not web"},
    ],
}


@pytest.fixture
async def fake_backends(monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    async def searxng(request: web.Request) -> web.Response:
        seen.setdefault("searxng", []).append(dict(request.query))
        if request.query.get("pageno") != "1":
            return web.json_response({"results": []})
        return web.json_response(SEARXNG_PAGE)

    async def marginalia(request: web.Request) -> web.Response:
        seen["marginalia"] = {"query": dict(request.query), "key": request.headers.get("API-Key")}
        return web.json_response(
            {
                "license": "CC-BY-NC-SA 4.0",
                "results": [
                    {"url": "https://small.example.net/post", "title": "Post", "description": "desc"}
                ],
            }
        )

    async def refuse(_request: web.Request) -> web.Response:
        return web.Response(status=403)

    app = web.Application()
    app.router.add_get("/searx/search", searxng)
    app.router.add_get("/marginalia", marginalia)
    app.router.add_get("/nojson/search", refuse)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"  # type: ignore[union-attr]
    monkeypatch.setattr(web_search, "MARGINALIA_URL", f"{base}/marginalia")
    yield base, seen
    await runner.cleanup()


@pytest.mark.asyncio
async def test_searxng_dedupes_filters_and_paginates(fake_backends: Any) -> None:
    base, seen = fake_backends
    response = await search_web(
        "q", backend="searxng", searxng_url=f"{base}/searx", include_domains=["https://example.com"]
    )

    assert [hit.url for hit in response.hits] == ["https://docs.example.com/a"]
    assert response.hits[0].score == 2.5
    assert seen["searxng"][0]["q"] == "q site:example.com"
    assert seen["searxng"][0]["format"] == "json"

    excluded = await search_web(
        "q", backend="searxng", searxng_url=f"{base}/searx", exclude_domains=["other.org"]
    )
    assert [hit.url for hit in excluded.hits] == ["https://docs.example.com/a"]


@pytest.mark.asyncio
async def test_marginalia_reports_license_and_public_key_warning(
    fake_backends: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, seen = fake_backends
    monkeypatch.delenv("DOCPULL_MARGINALIA_KEY", raising=False)
    response = await search_web("small web", backend="marginalia", max_results=3)

    assert response.license == "CC-BY-NC-SA 4.0"
    assert response.hits[0].snippet == "desc"
    assert seen["marginalia"]["key"] == "public"
    assert response.warnings


@pytest.mark.asyncio
async def test_search_errors_are_explicit(fake_backends: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    base, _ = fake_backends
    monkeypatch.delenv("DOCPULL_SEARXNG_URL", raising=False)
    with pytest.raises(WebSearchError, match="needs --searxng-url"):
        await search_web("q", backend="searxng")
    with pytest.raises(WebSearchError, match="enable `json`"):
        await search_web("q", backend="searxng", searxng_url=f"{base}/nojson")
    with pytest.raises(WebSearchError, match="max_results"):
        await search_web("q", backend="marginalia", max_results=50)
    with pytest.raises(WebSearchError, match="backend"):
        await search_web("q", backend="google")


@pytest.mark.asyncio
async def test_include_content_fetches_through_local_pipeline(
    fake_backends: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    base, _ = fake_backends

    async def fake_fetch(url: str) -> Page:
        return Page(url=url, markdown=f"# {url}\n")

    monkeypatch.setattr("docpull.compat_api.fetch_page", fake_fetch)
    response = await search_web("q", backend="searxng", searxng_url=f"{base}/searx", include_content=True)

    assert response.hits[0].content == "# https://docs.example.com/a\n"


@pytest.mark.asyncio
async def test_search_cli_json_output(fake_backends: Any, capsys: pytest.CaptureFixture[str]) -> None:
    base, _ = fake_backends
    argv = ["q", "--backend", "searxng", "--searxng-url", f"{base}/searx", "--json"]
    # The CLI runs its own event loop, so keep the fake server's loop free.
    assert await asyncio.to_thread(run_search_cli, argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["backend"] == "searxng"
    assert len(payload["hits"]) == 2


async def _fake_search(query: str, max_results: int, include: list[str], exclude: list[str], content: bool):
    hit = SearchHit(
        url="https://example.com/a",
        title="A",
        snippet="alpha",
        score=0.9,
        published="2026-10-01",
        content="# A\n" if content else None,
    )
    return SearchResponse(query=query, backend="fake", hits=[hit][:max_results])


@pytest.mark.asyncio
async def test_tavily_and_exa_search_shapes() -> None:
    app = CompatASGIApp(search=_fake_search)

    status, tavily = await _post(
        app, "/search", {"query": "a", "max_results": 3, "include_raw_content": True}
    )
    assert status == 200
    assert tavily["results"][0] == {
        "title": "A",
        "url": "https://example.com/a",
        "content": "alpha",
        "score": 0.9,
        "raw_content": "# A\n",
    }

    status, exa = await _post(app, "/search", {"query": "a", "numResults": 2, "contents": {"text": True}})
    assert status == 200
    assert exa["results"][0]["id"] == "https://example.com/a"
    assert exa["results"][0]["text"] == "# A\n"
    assert exa["results"][0]["publishedDate"] == "2026-10-01"

    _, prefixed = await _post(app, "/exa/search", {"query": "a"})
    assert "text" not in prefixed["results"][0]
    _, prefixed_extract = await _post(app, "/tavily/search", {"query": "a"})
    assert prefixed_extract["results"][0]["raw_content"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/search", "/v2/search", "/firecrawl/v2/search"])
async def test_firecrawl_search_shapes(path: str) -> None:
    app = CompatASGIApp(search=_fake_search)
    status, body = await _post(
        app, path, {"query": "a", "limit": 2, "scrapeOptions": {"formats": ["markdown"]}}
    )

    assert status == 200 and body["success"] is True
    rows = body["data"] if path == "/v1/search" else body["data"]["web"]
    assert rows[0] == {
        "url": "https://example.com/a",
        "title": "A",
        "description": "alpha",
        "markdown": "# A\n",
    }


@pytest.mark.asyncio
async def test_search_routes_validate_and_require_a_backend() -> None:
    assert (await _post(CompatASGIApp(), "/search", {"query": "a"}))[0] == 501
    app = CompatASGIApp(search=_fake_search)
    assert (await _post(app, "/search", {"query": ""}))[0] == 400
    assert (await _post(app, "/search", {"query": "a", "max_results": 99}))[0] == 400
    assert (await _post(app, "/search", {"query": "a", "include_domains": "x.com"}))[0] == 400
    assert (await _post(app, "/firecrawl/search", {"query": "a"}))[0] == 400

    async def failing(*_args: Any) -> SearchResponse:
        raise WebSearchError("backend down")

    assert (await _post(CompatASGIApp(search=failing), "/search", {"query": "a"}))[0] == 502
