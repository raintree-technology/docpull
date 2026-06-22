"""Tests for the local pack ASGI server."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.save_sqlite import SqliteSaveStep
from docpull.server import create_pack_app, run_serve_cli
from tests.pack_fixtures import write_context_pack


async def _call_json(
    app: Any,
    path: str,
    query_string: bytes = b"",
    *,
    method: str = "GET",
    scope_type: str = "http",
) -> tuple[int, dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    sent_request = False

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": scope_type,
            "method": method,
            "path": path,
            "query_string": query_string,
            "headers": [],
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        bytes(message.get("body") or b"") for message in messages if message["type"] == "http.response.body"
    )
    return int(start["status"]), json.loads(body.decode("utf-8"))


@pytest.mark.asyncio
async def test_pack_server_health_manifest_documents_and_citations(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    app = create_pack_app(pack)

    status, health = await _call_json(app, "/health")
    assert status == 200
    assert health["document_count"] == 1
    assert health["source_count"] == 1

    status, manifest = await _call_json(app, "/manifest")
    assert status == 200
    assert manifest["record_count"] == 1

    status, documents = await _call_json(app, "/documents", b"limit=1")
    assert status == 200
    assert documents["count"] == 1
    assert "content" not in documents["documents"][0]

    status, document = await _call_json(app, "/documents/doc_1")
    assert status == 200
    assert document["content"] == "Parallel Search API returns cited JSON results for live agent search."
    assert document["citation_id"] == "S1"

    status, citations = await _call_json(app, "/citations")
    assert status == 200
    assert citations["sources"][0]["citation_id"] == "S1"

    status, sources = await _call_json(app, "/sources")
    assert status == 200
    assert sources["sources"][0]["path"] == "sources/01.md"


@pytest.mark.asyncio
async def test_pack_server_search_uses_scan_fallback_for_ndjson_pack(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    app = create_pack_app(pack)

    status, payload = await _call_json(app, "/search", b"q=cited+JSON&limit=5")

    assert status == 200
    assert payload["engine"] == "scan"
    assert payload["result_count"] == 1
    assert payload["results"][0]["document_id"] == "doc_1"
    assert payload["results"][0]["citation_id"] == "S1"


@pytest.mark.asyncio
async def test_pack_server_search_uses_sqlite_fts_when_available(tmp_path: Path) -> None:
    step = SqliteSaveStep(tmp_path)
    ctx = PageContext(
        url="https://example.com/install",
        output_path=tmp_path / "install.md",
        markdown="# Install\n\nUse the orbital wrench package manager for setup.",
        title="Install",
    )
    await step.execute(ctx)
    step.close()
    app = create_pack_app(tmp_path)

    status, payload = await _call_json(app, "/search", b"q=orbital")

    assert status == 200
    assert payload["engine"] == "sqlite-fts"
    assert payload["result_count"] == 1
    assert payload["results"][0]["engine"] == "sqlite-fts"
    assert payload["results"][0]["url"] == "https://example.com/install"


@pytest.mark.asyncio
async def test_pack_server_large_pack_pagination(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    records = [
        {
            "document_id": f"doc_{index}",
            "url": f"https://docs.example.com/{index}",
            "title": f"Doc {index}",
            "content": f"content {index}",
            "content_hash": f"hash_{index}",
        }
        for index in range(25)
    ]
    write_context_pack(pack, records=records, include_domains=["docs.example.com"])
    app = create_pack_app(pack)

    status, payload = await _call_json(app, "/documents", b"limit=10&offset=20")

    assert status == 200
    assert payload["total"] == 25
    assert payload["count"] == 5
    assert payload["documents"][0]["document_id"] == "doc_20"


@pytest.mark.asyncio
async def test_pack_server_error_routes_and_invalid_query_defaults(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)
    app = create_pack_app(pack)

    status, payload = await _call_json(app, "/health", scope_type="websocket")
    assert status == 500
    assert payload["error"] == "Unsupported ASGI scope"

    status, payload = await _call_json(app, "/health", method="POST")
    assert status == 405
    assert payload["error"] == "Only GET endpoints are supported"

    status, payload = await _call_json(app, "/missing")
    assert status == 404
    assert payload["path"] == "/missing"

    status, payload = await _call_json(app, "/search")
    assert status == 400
    assert payload["error"] == "Missing required query parameter: q"

    status, payload = await _call_json(app, "/documents/missing")
    assert status == 404
    assert payload["document_id"] == "missing"

    status, payload = await _call_json(app, "/documents", b"limit=bad&offset=also-bad")
    assert status == 200
    assert payload["total"] == 1
    assert payload["count"] == 1


def test_serve_cli_rejects_non_localhost_bind(tmp_path: Path, capsys) -> None:
    pack = tmp_path / "pack"
    write_context_pack(pack)

    result = run_serve_cli([str(pack), "--host", "0.0.0.0"])

    assert result == 1
    captured = capsys.readouterr()
    assert "Refusing non-localhost bind host" in captured.out
