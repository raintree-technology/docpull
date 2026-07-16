"""Tests for the A+ release-readiness scorecard entrypoint."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]


def _load_scorecard_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "release_a_plus_check",
        ROOT / "scripts" / "release_a_plus_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_a_plus_plan_mode_is_side_effect_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_a_plus_check.py"),
            "--repo",
            str(ROOT),
            "--plan-only",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert "Core fetch/output" in payload["areas"]
    assert "Docs/public contract" in payload["areas"]
    assert "claim_audit" in payload["commands"]
    assert not {"web_typecheck", "web_lint", "web_build", "web_bun_audit"}.intersection(payload["commands"])
    assert "--full-mcp" in payload["smoke_command"]


def test_release_a_plus_defaults_to_the_invoking_python() -> None:
    module = _load_scorecard_module()

    assert module.create_parser().parse_args(["--plan-only"]).python == sys.executable


def test_release_a_plus_has_no_standalone_web_gates() -> None:
    module = _load_scorecard_module()

    assert module.AREA_GATES["Docs/public contract"] == ("claim_audit",)
    assert "web_bun_audit" not in module.AREA_GATES["Security/static quality"]
    assert all(not name.startswith("web_") for name in module._planned_command_names(ROOT))


def test_release_a_plus_writes_reports_from_existing_smoke(tmp_path: Path) -> None:
    smoke = tmp_path / "smoke.json"
    smoke.write_text(
        json.dumps(
            {
                "failed_required": 0,
                "results": [
                    {"name": "mcp stdio full", "status": "pass"},
                    {"name": "auth matrix bearer passes", "status": "pass"},
                    {"name": "auth matrix basic passes", "status": "pass"},
                    {"name": "auth matrix cookie passes", "status": "pass"},
                    {"name": "monitor bounded soak completed", "status": "pass"},
                    {"name": "strict ci fixture zero warnings", "status": "pass"},
                    {"name": "graph fixture expected entity", "status": "pass"},
                    {"name": "graph fixture expected cited edge", "status": "pass"},
                    {"name": "render js loopback content", "status": "pass"},
                ],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "release_a_plus_check.py"),
            "--repo",
            str(ROOT),
            "--output-dir",
            str(tmp_path),
            "--smoke-report",
            str(smoke),
            "--skip-commands",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert (tmp_path / "release-readiness.report.json").exists()
    assert (tmp_path / "RELEASE_READINESS.md").exists()
    assert payload["all_a_plus"] is False
    assert any(area["grade"] == "A+" for area in payload["areas"])


def test_release_gate_keeps_output_only_for_failures() -> None:
    module = _load_scorecard_module()
    env = {"PYTHONUNBUFFERED": "1"}

    passing = module._run_gate(
        "passing",
        [sys.executable, "-c", "print('success output')"],
        cwd=ROOT,
        timeout=30,
        env=env,
        classification="required",
    )
    failing = module._run_gate(
        "failing",
        [sys.executable, "-c", "import sys; print('failure output'); sys.exit(2)"],
        cwd=ROOT,
        timeout=30,
        env=env,
        classification="required",
    )

    assert passing.status == "pass"
    assert passing.stdout_tail == ""
    assert failing.status == "fail"
    assert "failure output" in failing.stdout_tail
