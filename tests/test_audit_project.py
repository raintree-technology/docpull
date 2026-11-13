"""Regression tests for audited project, CI, query, and pack-reader fixes."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

import docpull.project as project_module
from docpull.change_events import _line_opcodes, write_change_events
from docpull.context_ci import ContextCIError, _resolve_target, _resolve_thresholds
from docpull.pack_reader import load_pack
from docpull.project import (
    ProjectError,
    _cleanup_stale_fetch_dirs,
    _connect_index,
    _reconcile_locked_run,
    _validate_context_lock,
    _write_jsonl,
    init_project,
    load_project_config,
    project_paths,
    project_status,
    run_sync_cli,
    sync_project,
)
from tests.test_project import _install_fake_fetcher


def _ok_result(url: str, content: str) -> dict[str, Any]:
    import hashlib

    record = {
        "document_id": "d",
        "url": url,
        "title": "t",
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "metadata": {},
        "extraction": {},
    }
    return {"records": [record], "errors": [], "skips": [], "stats": {}}


def test_ci_broken_yaml_fails_loudly(tmp_path: Path) -> None:
    init_project(name="demo", root=tmp_path)
    (tmp_path / "docpull.yaml").write_text("name: [unclosed\n", encoding="utf-8")
    with pytest.raises(ContextCIError, match="docpull.yaml"):
        _resolve_thresholds({"mode": "project", "project_root": str(tmp_path)}, {}, strict=False)


def test_ci_discovery_only_falls_back_on_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert _resolve_target(None)["mode"] == "pack"

    def boom(_start: Path) -> Path:
        raise ProjectError("permission denied reading parent")

    monkeypatch.setattr(project_module, "find_project_root", boom)
    with pytest.raises(ContextCIError, match="permission denied"):
        _resolve_target(None)


def test_failed_sync_does_not_persist_refreshed_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_project(name="demo", source="https://docs.example.com/api", root=tmp_path)
    config = load_project_config(tmp_path)
    config.sources[0].discover = True
    project_module.save_project_config(tmp_path, config)
    before = (tmp_path / "docpull.yaml").read_bytes()

    def refresh(_root: Path, cfg: Any, *, source_names: list[str]) -> Any:
        updated = cfg.model_copy(deep=True)
        updated.sources[0].discovered_urls = ["https://docs.example.com/api/new"]
        return updated

    async def fail(**_kwargs: Any) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(project_module, "_refresh_project_discovery", refresh)
    monkeypatch.setattr(project_module, "_sync_source", fail)
    payload = sync_project(root=tmp_path, run_id="run_fail")
    assert payload["status"] == "failed"
    assert (tmp_path / "docpull.yaml").read_bytes() == before
    assert not project_paths(tmp_path).context_lock.exists()


def test_partial_sync_exits_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_project(name="demo", source="https://a.example.com/docs", root=tmp_path)
    project_module.add_source("https://b.example.com/docs", name="b", root=tmp_path)

    async def mixed(*, source: Any, **_kwargs: Any) -> dict[str, Any]:
        if "b.example" in source.url:
            raise RuntimeError("boom")
        return _ok_result(source.url, "hello")

    monkeypatch.setattr(project_module, "_sync_source", mixed)
    monkeypatch.chdir(tmp_path)
    assert run_sync_cli(["--json"]) == 1
    run = json.loads(next(project_paths(tmp_path).runs.glob("*/run.json")).read_text())
    assert run["status"] == "success"
    assert run["complete"] is False


def test_reconcile_repairs_interrupted_finalize(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_fetcher(monkeypatch, ["# API\nbody"])
    init_project(name="demo", source="https://docs.example.com/api", root=tmp_path)
    sync_project(root=tmp_path, run_id="run_one")
    paths = project_paths(tmp_path)
    # Simulate a crash after the lock was written but before latest-run / index.
    paths.latest_run.unlink()
    with sqlite3.connect(paths.index) as conn:
        conn.execute("DELETE FROM runs WHERE run_id = 'run_one'")
    assert _reconcile_locked_run(tmp_path, load_project_config(tmp_path)) == "run_one"
    assert paths.latest_run.read_text().strip() == "run_one"
    with sqlite3.connect(paths.index) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE run_id='run_one'").fetchone()[0] == 1
    assert _reconcile_locked_run(tmp_path, load_project_config(tmp_path)) is None
    assert project_status(root=tmp_path)["last_run_id"] == "run_one"


def test_stale_fetch_dirs_are_cleaned(tmp_path: Path) -> None:
    init_project(name="demo", root=tmp_path)
    paths = project_paths(tmp_path)
    stale = paths.runs / "run_stale" / "_fetch" / "docs"
    fresh = paths.runs / "run_fresh" / "_fetch" / "docs"
    done = paths.runs / "run_done" / "_fetch" / "docs"
    for directory in (stale, fresh, done):
        directory.mkdir(parents=True)
    (paths.runs / "run_done" / "run.json").write_text("{}")
    old = time.time() - 3 * 86400
    for run in ("run_stale", "run_done"):
        os.utime(paths.runs / run / "_fetch", (old, old))
    assert _cleanup_stale_fetch_dirs(paths) == ["run_stale"]
    assert not (paths.runs / "run_stale").exists()
    assert fresh.exists() and done.exists()


def test_index_connection_uses_wal_and_busy_timeout(tmp_path: Path) -> None:
    conn = _connect_index(tmp_path / "x.sqlite")
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    finally:
        conn.close()


def test_atomic_jsonl_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "out.jsonl"
    _write_jsonl(target, [{"a": 1}, {"b": 2}])
    assert target.read_text().splitlines() == ['{"a": 1}', '{"b": 2}']
    assert [p.name for p in tmp_path.iterdir()] == ["out.jsonl"]


def test_unknown_lock_alias_degrades_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    init_project(name="demo", source="https://docs.example.com/api", root=tmp_path)
    config = load_project_config(tmp_path)
    source = config.sources[0]
    item = {
        "name": source.name,
        "url": source.url,
        "type": source.type,
        "discover": source.discover,
        "discovered_urls": [],
        "alias": "retired-alias",
    }
    with caplog.at_level(logging.WARNING):
        _validate_context_lock(config, {"sources": [item]})
    assert "retired-alias" in caplog.text
    with pytest.raises(ProjectError, match="records no URL"):
        _validate_context_lock(config, {"sources": [{**item, "url": None}]})


def test_large_diff_is_bounded() -> None:
    old = [f"line {i}" for i in range(5000)]
    new = list(old)
    new[2500] = "changed"
    started = time.perf_counter()
    ops = [op for op in _line_opcodes(old, new) if op[0] != "equal"]
    assert time.perf_counter() - started < 2
    assert ops == [("replace", 2500, 2501, 2500, 2501)]
    assert _line_opcodes(["a"], ["b"]) == [("replace", 0, 1, 0, 1)]


def test_write_change_events_is_atomic(tmp_path: Path) -> None:
    path = write_change_events(tmp_path / "e.jsonl", [{"x": 1}])
    assert path.read_text() == '{"x": 1}\n'
    assert [p.name for p in tmp_path.iterdir()] == ["e.jsonl"]


def test_pack_reader_accepts_documents_jsonl(tmp_path: Path) -> None:
    record = {"url": "https://docs.example.com/a", "title": "A", "content": "hello"}
    (tmp_path / "documents.jsonl").write_text(json.dumps(record) + "\n\n", encoding="utf-8")
    pack = load_pack(tmp_path)
    assert [doc.url for doc in pack.documents] == ["https://docs.example.com/a"]
