"""Discovery shards: build from locked runs, verified download, and offline search."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from docpull import discovery_index
from docpull import project as project_module
from docpull.cli import main
from docpull.discovery_index import DiscoveryIndexError, build_shard, pull, search_index
from docpull.project import init_project, sync_project
from docpull.web_search import WebSearchError, search_web

PAGES = {
    "https://docs.alpha.example/retry": (
        "Retry policies",
        "# Retry policies\n\n## Backoff\n\nSECRET BODY TEXT",
    ),
    "https://docs.alpha.example/auth": ("Authentication", "# Authentication\n\n## Retry on 401\n\nbody"),
    "https://blog.beta.example/post": ("Release notes", "# Release notes\n\n```\n# not a heading\n```\n"),
}


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj"
    init_project(name="proj", source="https://docs.alpha.example/", root=root)

    async def fetch(**kwargs: Any) -> dict[str, Any]:
        records = [
            {
                "document_id": f"doc_{i}",
                "url": url,
                "title": title,
                "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "fetched_at": "2026-10-12T00:00:00Z",
                "metadata": {"docpull_project_source": kwargs["source"].name},
            }
            for i, (url, (title, content)) in enumerate(PAGES.items())
        ]
        return {"records": records, "errors": [], "skips": [], "stats": {}}

    monkeypatch.setattr(project_module, "_sync_source", fetch)
    sync_project(root=root, run_id="proj_run")
    return root


@pytest.fixture
def shards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    out = tmp_path / "published"
    build_shard([_project(tmp_path, monkeypatch)], out, "alpha")
    return out


def test_build_writes_titles_and_headings_but_no_bodies(shards: Path) -> None:
    manifest = json.loads((shards / "manifest.json").read_text())
    [entry] = manifest["shards"]
    shard = shards / "alpha.sqlite"
    assert entry["sha256"] == hashlib.sha256(shard.read_bytes()).hexdigest()
    assert entry["documents"] == 3 and entry["domains"] == ["blog.beta.example", "docs.alpha.example"]
    with sqlite3.connect(shard) as conn:
        rows = dict(conn.execute("SELECT url, headings FROM pages").fetchall())
    assert rows["https://docs.alpha.example/retry"] == "Retry policies\nBackoff"
    assert rows["https://blog.beta.example/post"] == "Release notes"
    assert b"SECRET BODY TEXT" not in shard.read_bytes()


def _serve(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> list[str]:
    fetched: list[str] = []

    async def fake_fetch(url: str, limit: int) -> bytes:
        fetched.append(url)
        data = files[url]
        if len(data) > limit:
            raise DiscoveryIndexError("too large")
        return data

    monkeypatch.setattr(discovery_index, "_fetch_bytes", fake_fetch)
    return fetched


def test_pull_verifies_and_skips_current_shards(
    shards: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = "https://index.example/v1/"
    fetched = _serve(
        monkeypatch,
        {
            base + "manifest.json": (shards / "manifest.json").read_bytes(),
            base + "alpha.sqlite": (shards / "alpha.sqlite").read_bytes(),
        },
    )
    installed = tmp_path / "installed"
    first = asyncio.run(pull(base + "manifest.json", installed))
    assert first["downloaded"] == ["alpha"] and (installed / "alpha.sqlite").is_file()
    second = asyncio.run(pull(base + "manifest.json", installed))
    assert second["downloaded"] == [] and second["up_to_date"] == ["alpha"]
    assert fetched.count(base + "alpha.sqlite") == 1

    with pytest.raises(DiscoveryIndexError, match="limit"):
        asyncio.run(pull(base + "manifest.json", tmp_path / "small", max_bytes=10))
    with pytest.raises(DiscoveryIndexError, match="no shard named"):
        asyncio.run(pull(base + "manifest.json", installed, shards=["missing"]))


def test_pull_rejects_tampered_shard(shards: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = "https://index.example/"
    data = bytearray((shards / "alpha.sqlite").read_bytes())
    data[-1] ^= 1
    _serve(
        monkeypatch,
        {base + "manifest.json": (shards / "manifest.json").read_bytes(), base + "alpha.sqlite": bytes(data)},
    )
    with pytest.raises(DiscoveryIndexError, match="sha256 mismatch"):
        asyncio.run(pull(base + "manifest.json", tmp_path / "installed"))
    assert not (tmp_path / "installed" / "alpha.sqlite").exists()


@pytest.mark.parametrize("url", ["http://index.example/manifest.json", "https://127.0.0.1/manifest.json"])
def test_fetch_refuses_plain_http_and_private_hosts(url: str) -> None:
    with pytest.raises(DiscoveryIndexError, match="Refusing"):
        asyncio.run(discovery_index._fetch_bytes(url, 100))


def test_index_backend_searches_offline(shards: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*args: Any, **kwargs: Any) -> None:
        pytest.fail("index search attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    response = asyncio.run(search_web("retry", backend="index", index_dir=shards))
    assert [hit.url for hit in response.hits] == [
        "https://docs.alpha.example/retry",
        "https://docs.alpha.example/auth",
    ]
    assert response.hits[0].snippet == "Retry policies · Backoff"
    filtered = search_index("retry release", index_dir=shards, exclude_domains=["docs.alpha.example"])
    assert [hit.url for hit in filtered] == ["https://blog.beta.example/post"]
    with pytest.raises(WebSearchError, match="index pull"):
        search_index("retry", index_dir=shards / "missing")


def test_index_cli_build_and_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _project(tmp_path, monkeypatch)
    out = tmp_path / "cli"
    assert main(["index", "build", "--project", str(root), "--out", str(out), "--name", "alpha"]) == 0
    assert main(["index", "--index-dir", str(out), "list"]) == 0
    assert "alpha\t" in capsys.readouterr().out
    assert main(["index", "pull"]) == 1
