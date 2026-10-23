"""Regression tests for cache/lock semantics found by tests/test_model_cache_lock.py."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull import project as project_module
from docpull.models.events import EventType
from docpull.project import (
    ProjectError,
    add_source,
    export_context_pack,
    init_project,
    install_project,
    sync_project,
)
from tests.test_project import _install_fake_fetcher


def _lock(root: Path) -> dict[str, Any]:
    return json.loads((root / ".docpull/context.lock.json").read_text())


@pytest.fixture
def synced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _install_fake_fetcher(monkeypatch, ["# API\nPinned content."])
    init_project(name="demo", source="https://docs.example.com/api", root=tmp_path)
    sync_project(root=tmp_path, run_id="run_good")
    return tmp_path


def test_dry_run_leaves_lock_untouched(synced: Path) -> None:
    before = (synced / ".docpull/context.lock.json").read_bytes()
    sync_project(root=synced, run_id="run_dry", dry_run=True)
    assert (synced / ".docpull/context.lock.json").read_bytes() == before
    install_project(frozen=True, root=synced)


def test_dry_run_does_not_discover_or_rewrite_config(synced: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = (synced / "docpull.yaml").read_text().replace("discover: false", "discover: true")
    (synced / "docpull.yaml").write_text(config)

    async def no_network(**_: Any) -> list[str]:
        pytest.fail("dry run must not touch the network")

    monkeypatch.setattr(project_module, "_discover_source_urls", no_network)
    before = (synced / "docpull.yaml").read_bytes()
    sync_project(root=synced, run_id="run_dry", dry_run=True)
    assert (synced / "docpull.yaml").read_bytes() == before


def test_failed_sync_keeps_previous_lock(synced: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = (synced / ".docpull/context.lock.json").read_bytes()

    async def offline(**_: Any) -> None:
        raise OSError("network down")

    monkeypatch.setattr(project_module, "_sync_source", offline)
    assert sync_project(root=synced, run_id="run_bad")["status"] == "failed"
    assert (synced / ".docpull/context.lock.json").read_bytes() == before
    assert (synced / ".docpull/latest-run").read_text().strip() == "run_good"


def test_crash_while_writing_lock_keeps_old_lock(synced: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = (synced / ".docpull/context.lock.json").read_bytes()
    original = Path.write_text

    def crash(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        original(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("power loss")

    monkeypatch.setattr(Path, "write_text", crash)
    with pytest.raises(OSError):
        project_module._write_json(synced / ".docpull/context.lock.json", {"project": "x" * 100})
    monkeypatch.undo()
    assert (synced / ".docpull/context.lock.json").read_bytes() == before
    assert not list((synced / ".docpull").glob(".*.tmp"))


def test_crash_while_saving_config_keeps_old_config(synced: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = (synced / "docpull.yaml").read_bytes()
    config = project_module.load_project_config(synced)
    original = Path.write_text

    def crash(self: Path, data: str, *args: Any, **kwargs: Any) -> int:
        original(self, data[: len(data) // 2], *args, **kwargs)
        raise OSError("power loss")

    monkeypatch.setattr(Path, "write_text", crash)
    with pytest.raises(OSError):
        project_module.save_project_config(synced, config.model_copy(update={"name": "renamed"}))
    monkeypatch.undo()
    assert (synced / "docpull.yaml").read_bytes() == before
    assert ProjectError  # config still loads
    project_module.load_project_config(synced)


def _two_source_project(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two sources whose fetched content differs per URL and per call."""
    fetcher = _install_fake_fetcher(monkeypatch, ["unused"])

    async def run(self: Any) -> Any:  # noqa: ANN401
        fetcher.calls += 1
        content = f"# T\ncontent {self.config.url} v{fetcher.calls}"
        directory = self.config.output.directory
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "document_id": f"doc_{fetcher.calls}",
            "url": self.config.url,
            "title": "T",
            "content": content,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "source_type": "test",
            "fetched_at": "2026-06-24T00:00:00+00:00",
            "metadata": {},
            "extraction": {},
        }
        (directory / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
        yield SimpleNamespace(type=EventType.STARTED, message="started")
        yield SimpleNamespace(type=EventType.COMPLETED, message="done")

    monkeypatch.setattr(fetcher, "run", run)
    init_project(name="demo", source="https://a.example.com/x", root=root)
    add_source("https://b.example.com/y", root=root)
    sync_project(root=root, run_id="run_good")


def test_scoped_sync_keeps_full_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _two_source_project(tmp_path, monkeypatch)
    before = (tmp_path / ".docpull/context.lock.json").read_bytes()
    assert len(_lock(tmp_path)["content_hashes"]) == 2
    sync_project(root=tmp_path, run_id="run_scoped", source_name="a-example-com-x")
    assert (tmp_path / ".docpull/context.lock.json").read_bytes() == before
    install_project(frozen=True, root=tmp_path)


def test_sync_that_loses_a_source_keeps_previous_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_source_project(tmp_path, monkeypatch)
    before = (tmp_path / ".docpull/context.lock.json").read_bytes()
    original = project_module._sync_source

    async def flaky(**kwargs: Any) -> Any:  # noqa: ANN401
        if "b.example.com" in kwargs["source"].url:
            raise OSError("network down")
        return await original(**kwargs)

    monkeypatch.setattr(project_module, "_sync_source", flaky)
    sync_project(root=tmp_path, run_id="run_degraded")
    assert (tmp_path / ".docpull/context.lock.json").read_bytes() == before


def test_exporting_an_older_run_does_not_repin_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _two_source_project(tmp_path, monkeypatch)
    sync_project(root=tmp_path, run_id="run_new")
    pinned = _lock(tmp_path)
    export_context_pack(target="codex", run_id="run_good", root=tmp_path)
    after = _lock(tmp_path)
    assert after["run_id"] == pinned["run_id"] == "run_new"
    assert after["content_hashes"] == pinned["content_hashes"]
    assert after["artifact"] == pinned["artifact"]
    install_project(frozen=True, root=tmp_path)
