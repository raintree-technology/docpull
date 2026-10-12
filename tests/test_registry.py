"""Pack recipes: export from a synced project, verified fetch, add, and drift reports."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from docpull import discovery_index, registry
from docpull import project as project_module
from docpull.cli import main
from docpull.project import init_project, load_project_config, sync_project
from docpull.registry import RegistryError, export_recipe, fetch_recipe, install_recipe, verify_recipe

PAGES = {
    "https://docs.alpha.example/retry": "# Retry\n\nWait 2 seconds.",
    "https://docs.alpha.example/auth": "# Auth\n\nUse a token.",
}
BASE = "https://registry.example/"


def _fake_sync(monkeypatch: pytest.MonkeyPatch, pages: dict[str, str]) -> None:
    async def fetch(**kwargs: Any) -> dict[str, Any]:
        source = kwargs["source"]
        records = [
            {
                "document_id": f"doc_{i}",
                "url": url,
                "title": url,
                "content": content,
                "content_hash": hashlib.sha256(content.encode()).hexdigest(),
                "metadata": {"docpull_project_source": source.name},
            }
            for i, (url, content) in enumerate(pages.items())
            if url.startswith(source.url.rstrip("/"))
        ]
        return {"records": records, "errors": [], "skips": [], "stats": {}}

    monkeypatch.setattr(project_module, "_sync_source", fetch)


def _publisher(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "publisher"
    init_project(name="publisher", source="https://docs.alpha.example/", root=root)
    config = load_project_config(root)
    pinned = config.sources[0].model_copy(update={"discovered_urls": sorted(PAGES)})
    project_module.save_project_config(root, config.model_copy(update={"sources": [pinned]}))
    _fake_sync(monkeypatch, PAGES)
    sync_project(root=root, run_id="publisher_run")
    return root


def _serve(monkeypatch: pytest.MonkeyPatch, recipe_bytes: bytes, sha256: str | None = None) -> None:
    index = {
        "schema_version": 1,
        "packs": {
            "alpha-docs": {
                "latest": "2026-10",
                "versions": {
                    "2026-10": {
                        "path": "packs/alpha-docs/2026-10.json",
                        "sha256": sha256 or hashlib.sha256(recipe_bytes).hexdigest(),
                    }
                },
            }
        },
    }
    files = {
        BASE + "index.json": json.dumps(index).encode(),
        BASE + "packs/alpha-docs/2026-10.json": recipe_bytes,
    }

    async def fake_fetch(url: str, limit: int) -> bytes:
        return files[url]

    monkeypatch.setattr(registry, "_fetch_bytes", fake_fetch)
    monkeypatch.setenv(registry.REGISTRY_URL_ENV, BASE)


def test_export_records_pinned_sources_and_page_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recipe = export_recipe(_publisher(tmp_path, monkeypatch), "alpha-docs", "2026-10", "Alpha docs")
    [source] = recipe.sources
    assert source.discovered_urls == sorted(PAGES)
    assert recipe.expected[source.name] == {
        url: hashlib.sha256(content.encode()).hexdigest() for url, content in sorted(PAGES.items())
    }
    assert b"Wait 2 seconds" not in recipe.canonical()
    with pytest.raises(RegistryError, match="name"):
        export_recipe(tmp_path / "publisher", "Bad Name", "1")


def test_export_refuses_private_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _publisher(tmp_path, monkeypatch)
    config = load_project_config(root)
    private = config.sources[0].model_copy(
        update={"auth": project_module.ProjectSourceAuth(type="bearer_env", env="TOKEN")}
    )
    project_module.save_project_config(root, config.model_copy(update={"sources": [private]}))
    with pytest.raises(RegistryError, match="auth"):
        export_recipe(root, "alpha-docs", "2026-10")


def test_fetch_verifies_sha256_and_resolves_latest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = export_recipe(_publisher(tmp_path, monkeypatch), "alpha-docs", "2026-10").canonical()
    _serve(monkeypatch, data)
    assert asyncio.run(fetch_recipe("alpha-docs@latest")).version == "2026-10"
    with pytest.raises(RegistryError, match="no version 2027"):
        asyncio.run(fetch_recipe("alpha-docs@2027"))
    with pytest.raises(RegistryError, match="no pack named"):
        asyncio.run(fetch_recipe("beta@1"))
    _serve(monkeypatch, data, sha256="0" * 64)
    with pytest.raises(RegistryError, match="sha256 mismatch"):
        asyncio.run(fetch_recipe("alpha-docs@2026-10"))


@pytest.mark.parametrize(
    "source",
    [
        {"name": "local", "url": "./secrets.sqlite", "type": "auto"},
        {"name": "typed", "url": "https://github.com/a/b", "type": "repo"},
        {"name": "plain", "url": "http://docs.example/", "type": "auto"},
    ],
)
def test_registry_recipes_only_allow_public_crawl_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: dict[str, str]
) -> None:
    payload = {
        "schema_version": 1,
        "name": "alpha-docs",
        "version": "2026-10",
        "sources": [source],
        "expected": {},
    }
    _serve(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(RegistryError, match="Invalid recipe"):
        asyncio.run(fetch_recipe("alpha-docs@2026-10"))


def test_add_installs_syncs_and_reports_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data = export_recipe(_publisher(tmp_path, monkeypatch), "alpha-docs", "2026-10").canonical()
    _serve(monkeypatch, data)
    consumer = tmp_path / "consumer"
    init_project(name="consumer", root=consumer)
    monkeypatch.chdir(consumer)
    changed = {**PAGES, "https://docs.alpha.example/auth": "# Auth\n\nUse an API key."}
    _fake_sync(monkeypatch, changed)

    assert main(["add", "alpha-docs@2026-10"]) == 0
    assert "1/2 pages match" in capsys.readouterr().out
    [source] = load_project_config(consumer).sources
    assert source.discover is False and source.discovered_urls == sorted(PAGES)
    report = verify_recipe(consumer, "alpha-docs@2026-10")
    assert (report["matched"], report["changed"], report["missing"], report["new"]) == (1, 1, 0, 0)
    assert report["match_ratio"] == 0.5

    with pytest.raises(RegistryError, match="already has sources"):
        install_recipe(asyncio.run(fetch_recipe("alpha-docs@2026-10")), consumer)
    assert main(["registry", "verify", "alpha-docs@2026-10", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["matched"] == 1


def test_add_still_resolves_aliases_urls_and_typed_specs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_registry(*args: Any, **kwargs: Any) -> None:
        pytest.fail("non-registry source reached the registry")

    monkeypatch.setattr(discovery_index, "_fetch_bytes", no_registry)
    monkeypatch.setattr(registry, "_fetch_bytes", no_registry)
    root = tmp_path / "plain"
    init_project(name="plain", root=root)
    monkeypatch.chdir(root)
    assert main(["add", "react"]) == 0
    assert main(["add", "https://docs.gamma.example/"]) == 0
    assert main(["add", "npm:react@18.3.1"]) == 0
    assert main(["add", "owner/repo@v1"]) == 0
    assert len(load_project_config(root).sources) == 4
