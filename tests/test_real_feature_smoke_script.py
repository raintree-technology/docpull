"""Tests for the opt-in real-data smoke harness."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_smoke_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "real_feature_smoke",
        ROOT / "scripts" / "real_feature_smoke.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _smoke_for_tmp(tmp_path: Path):
    module = _load_smoke_module()
    return module.RealFeatureSmoke(
        argparse.Namespace(
            repo=ROOT,
            python=sys.executable,
            base_dir=tmp_path,
            trust_render_targets=False,
        )
    )


def test_real_feature_smoke_plan_mode_is_network_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "real_feature_smoke.py"),
            "--plan-only",
            "--json",
            "--repo",
            str(ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "root fetch markdown/json/ndjson/sqlite/okf" in payload["plan"]
    assert "watch one-shot" in payload["plan"]
    assert "cloud render runtimes when credentials/tools are available" not in payload["plan"]


def test_real_feature_smoke_plan_can_include_cloud_contracts() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "real_feature_smoke.py"),
            "--plan-only",
            "--json",
            "--include-cloud",
            "--repo",
            str(ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "cloud render runtimes when credentials/tools are available" in payload["plan"]


def test_real_feature_smoke_plan_does_not_create_base_dir(tmp_path: Path) -> None:
    base_dir = tmp_path / "planned-smoke"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "real_feature_smoke.py"),
            "--plan-only",
            "--json",
            "--repo",
            str(ROOT),
            "--base-dir",
            str(base_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["base_dir"] == str(base_dir.resolve())
    assert not base_dir.exists()


def test_real_feature_smoke_plan_includes_a_plus_gates() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "real_feature_smoke.py"),
            "--plan-only",
            "--json",
            "--full-mcp",
            "--strict-ci",
            "--auth-matrix",
            "--monitor-soak-minutes",
            "0.01",
            "--repo",
            str(ROOT),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "full MCP public tool calls" in payload["plan"]
    assert "strict Context CI zero-warning fixture" in payload["plan"]
    assert "loopback auth matrix bearer/basic/cookie/header" in payload["plan"]
    assert "bounded monitor soak (0.01 minutes)" in payload["plan"]


def test_record_check_drops_notes_for_passes(tmp_path: Path) -> None:
    smoke = _smoke_for_tmp(tmp_path)

    smoke._record_check("passing invariant", True, note="failure wording")
    smoke._record_check("failing invariant", False, note="failure wording")

    assert smoke.results[0].status == "pass"
    assert smoke.results[0].note == ""
    assert smoke.results[1].status == "fail"
    assert smoke.results[1].note == "failure wording"


def test_run_keeps_output_only_for_failures(tmp_path: Path) -> None:
    smoke = _smoke_for_tmp(tmp_path)

    smoke._run(
        "successful command",
        [sys.executable, "-c", "print('large success output')"],
        cwd=ROOT,
        timeout=30,
    )
    smoke._run(
        "failing command",
        [sys.executable, "-c", "import sys; print('failure output'); sys.exit(2)"],
        cwd=ROOT,
        timeout=30,
    )

    assert smoke.results[0].status == "pass"
    assert smoke.results[0].stdout_tail == ""
    assert smoke.results[1].status == "fail"
    assert "failure output" in smoke.results[1].stdout_tail
