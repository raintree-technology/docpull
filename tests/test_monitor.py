"""Tests for local monitor workflows."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.cli import main
from docpull.monitor import (
    init_monitor,
    list_monitors,
    run_monitor_once,
    scheduler_snippet,
    set_monitor_paused,
)
from tests.pack_fixtures import write_context_pack


def test_monitor_init_and_list(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    state_dir = tmp_path / "monitors"
    write_context_pack(pack_dir)

    created = init_monitor("vendor-docs", pack_dir, state_dir=state_dir)
    listed = list_monitors(state_dir=state_dir)

    assert created["name"] == "vendor-docs"
    assert listed["monitor_count"] == 1
    assert listed["monitors"][0]["pack_dir"] == str(pack_dir.resolve())


def test_monitor_run_once_dry_run_writes_report(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    state_dir = tmp_path / "monitors"
    write_context_pack(pack_dir)
    init_monitor("vendor-docs", pack_dir, state_dir=state_dir)

    payload = run_monitor_once("vendor-docs", state_dir=state_dir, dry_run=True)

    assert payload["dry_run"] is True
    assert payload["summary"]["changed_count"] == 0
    assert Path(payload["run_dir"], "monitor.report.json").exists()
    assert Path(payload["run_dir"], "MONITOR_REPORT.md").exists()
    assert payload["trigger"] == "scheduled"
    assert payload["summary"]["dedupe_key"] == "url+content_hash"


def test_monitor_pause_unpause_and_scheduler_snippets(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    state_dir = tmp_path / "monitors"
    write_context_pack(pack_dir)
    init_monitor(
        "vendor-docs",
        pack_dir,
        state_dir=state_dir,
        schedule="0 6 * * *",
        dedupe_key="url",
    )

    paused = set_monitor_paused("vendor-docs", True, state_dir=state_dir)
    assert paused["paused"] is True
    assert list_monitors(state_dir=state_dir)["monitors"][0]["paused"] is True

    unpaused = set_monitor_paused("vendor-docs", False, state_dir=state_dir)
    assert unpaused["paused"] is False

    cron = scheduler_snippet("vendor-docs", kind="cron", state_dir=state_dir)
    assert "docpull monitor" in cron["snippet"]
    assert "0 6 * * *" in cron["snippet"]

    launchd = scheduler_snippet("vendor-docs", kind="launchd", state_dir=state_dir)
    assert "technology.raintree.docpull.vendor-docs" in launchd["snippet"]


def test_monitor_cli_dry_run_json(tmp_path: Path, capsys) -> None:
    pack_dir = tmp_path / "pack"
    state_dir = tmp_path / "monitors"
    write_context_pack(pack_dir)

    assert (
        main(["monitor", "--state-dir", str(state_dir), "init", str(pack_dir), "--name", "vendor-docs"]) == 0
    )
    assert (
        main(
            [
                "monitor",
                "--state-dir",
                str(state_dir),
                "run",
                "vendor-docs",
                "--once",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    payload = json.loads(output[output.find("{") :])
    assert payload["name"] == "vendor-docs"
    assert payload["dry_run"] is True

    assert (
        main(
            [
                "monitor",
                "--state-dir",
                str(state_dir),
                "trigger",
                "vendor-docs",
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    payload = json.loads(output[output.find("{") :])
    assert payload["trigger"] == "manual"
