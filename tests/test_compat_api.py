"""Tests for the local Tavily-, Exa-, and Firecrawl-compatible extract endpoints."""

from __future__ import annotations

import json
from typing import Any

import pytest

from docpull.compat_api import CompatASGIApp, Page, fetch_page
from docpull.server import run_serve_cli
from tests.test_outputs_e2e import output_server  # noqa: F401 - pytest fixture

OK_URL = "https://example.com/ok"
BAD_URL = "https://example.com/missing"


async def _fake_fetch(url: str) -> Page:
    if url == BAD_URL:
        return Page(url=url, status_code=404, error="HTTP 404")
    return Page(url=url, markdown="# Hello\n\nBody text.", title="Hello", status_code=200)


async def _post(
    app: Any,
    path: str,
    payload: Any,
    *,
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> tuple[int, dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": headers or []}
    await app(scope, receive, send)
    start = next(m for m in messages if m["type"] == "http.response.start")
    return start["status"], json.loads(b"".join(m.get("body", b"") for m in messages[1:]))


@pytest.mark.asyncio
async def test_tavily_extract_shape_splits_results_and_failures() -> None:
    status, body = await _post(CompatASGIApp(fetch=_fake_fetch), "/extract", {"urls": [OK_URL, BAD_URL]})

    assert status == 200
    assert body["results"] == [{"url": OK_URL, "raw_content": "# Hello\n\nBody text.", "images": []}]
    assert body["failed_results"] == [{"url": BAD_URL, "error": "HTTP 404"}]
    assert isinstance(body["response_time"], float)

    status, single = await _post(CompatASGIApp(fetch=_fake_fetch), "/extract", {"urls": OK_URL})
    assert status == 200 and single["results"][0]["url"] == OK_URL


@pytest.mark.asyncio
async def test_exa_contents_shape_reports_statuses_and_truncates_text() -> None:
    app = CompatASGIApp(fetch=_fake_fetch)
    status, body = await _post(app, "/contents", {"urls": [OK_URL, BAD_URL], "text": {"maxCharacters": 7}})

    assert status == 200
    assert body["results"] == [{"id": OK_URL, "url": OK_URL, "title": "Hello", "text": "# Hello"}]
    assert body["statuses"][0] == {"id": OK_URL, "status": "success"}
    assert body["statuses"][1]["status"] == "error"

    _, no_text = await _post(app, "/contents", {"ids": [OK_URL], "text": False})
    assert "text" not in no_text["results"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/scrape", "/v2/scrape"])
async def test_firecrawl_scrape_shape(path: str) -> None:
    app = CompatASGIApp(fetch=_fake_fetch)
    status, body = await _post(app, path, {"url": OK_URL, "formats": ["markdown"]})

    assert status == 200
    assert body["success"] is True
    assert body["data"]["markdown"] == "# Hello\n\nBody text."
    assert body["data"]["metadata"]["sourceURL"] == OK_URL

    _, failed = await _post(app, path, {"url": BAD_URL})
    assert failed == {"success": False, "error": "HTTP 404"}

    status, unsupported = await _post(app, path, {"url": OK_URL, "formats": ["markdown", "screenshot"]})
    assert status == 400 and "screenshot" in unsupported["error"]


@pytest.mark.asyncio
async def test_request_validation_and_methods() -> None:
    app = CompatASGIApp(fetch=_fake_fetch)

    assert (await _post(app, "/extract", {"urls": []}))[0] == 400
    assert (await _post(app, "/extract", {"urls": [OK_URL] * 21}))[0] == 400
    assert (await _post(app, "/extract", b"{not json"))[0] == 400
    assert (await _post(app, "/extract", [OK_URL]))[0] == 400
    assert (await _post(app, "/extract", {}, method="GET"))[0] == 405
    assert (await _post(app, "/nope", {}))[0] == 404
    status, health = await _post(app, "/health", {}, method="GET")
    assert status == 200 and "/extract" in health["endpoints"]


@pytest.mark.asyncio
async def test_api_key_accepts_bearer_or_x_api_key() -> None:
    app = CompatASGIApp(fetch=_fake_fetch, api_key="s3cret")
    payload = {"urls": [OK_URL]}

    assert (await _post(app, "/extract", payload))[0] == 401
    assert (await _post(app, "/extract", payload, headers=[(b"authorization", b"Bearer wrong")]))[0] == 401
    assert (await _post(app, "/extract", payload, headers=[(b"authorization", b"Bearer s3cret")]))[0] == 200
    assert (await _post(app, "/contents", payload, headers=[(b"x-api-key", b"s3cret")]))[0] == 200


@pytest.mark.asyncio
async def test_fetch_page_uses_the_local_pipeline(output_server: str) -> None:  # noqa: F811
    page = await fetch_page(output_server)

    assert page.error is None
    assert "First page for local e2e output tests." in page.markdown
    assert not page.markdown.startswith("---")


def test_compat_serve_refuses_network_bind_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_serve_cli(["--compat", "--host", "0.0.0.0", "--allow-network-bind"]) == 1
    monkeypatch.delenv("DOCPULL_COMPAT_KEY", raising=False)
    assert run_serve_cli(["--compat", "--api-key-env", "DOCPULL_COMPAT_KEY"]) == 1
    with pytest.raises(SystemExit):
        run_serve_cli([])
