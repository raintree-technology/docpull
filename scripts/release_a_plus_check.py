#!/usr/bin/env python3
"""Run the DocPull v6 A+ release-readiness scorecard."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCORECARD_SCHEMA_VERSION = 1

AREAS = (
    "Core fetch/output",
    "v3 pack contract",
    "Typed packs",
    "Exports",
    "Context CI",
    "Project workflow",
    "Graph",
    "MCP",
    "Render",
    "Auth",
    "Monitor",
    "Security/static quality",
    "Web/docs copy",
    "Release hygiene",
)

AREA_GATES: dict[str, tuple[str, ...]] = {
    "Core fetch/output": ("real_feature_smoke",),
    "v3 pack contract": ("real_feature_smoke", "pytest"),
    "Typed packs": ("real_feature_smoke", "pytest"),
    "Exports": ("real_feature_smoke", "pytest"),
    "Context CI": ("real_feature_smoke", "pytest"),
    "Project workflow": ("real_feature_smoke",),
    "Graph": ("real_feature_smoke", "pytest"),
    "MCP": ("real_feature_smoke", "mcp_bun_test", "mcp_typecheck"),
    "Render": ("real_feature_smoke", "pytest"),
    "Auth": ("real_feature_smoke", "pytest"),
    "Monitor": ("real_feature_smoke", "pytest"),
    "Security/static quality": (
        "ruff_check",
        "ruff_format",
        "mypy",
        "pytest",
        "security_tests",
        "bandit",
        "pip_audit",
        "gitleaks",
        "mcp_bun_audit",
        "web_bun_audit",
        "package_build",
        "twine_check",
    ),
    "Web/docs copy": ("claim_audit", "web_typecheck", "web_lint", "web_build"),
    "Release hygiene": ("git_status_clean", "release_metadata"),
}

A_PLUS_SMOKE_MARKERS = {
    "MCP": ("mcp stdio full",),
    "Auth": ("auth matrix bearer passes", "auth matrix basic passes", "auth matrix cookie passes"),
    "Monitor": ("monitor bounded soak completed",),
    "Context CI": ("strict ci fixture zero warnings",),
    "Graph": ("graph fixture expected entity", "graph fixture expected cited edge"),
    "Render": ("render js loopback content",),
}


@dataclass
class GateResult:
    name: str
    status: str
    classification: str = "required"
    command: list[str] = field(default_factory=list)
    cwd: str = ""
    code: int | None = None
    seconds: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""
    note: str = ""
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "classification": self.classification,
            "command": self.command,
            "cwd": self.cwd,
            "code": self.code,
            "seconds": self.seconds,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
            "note": self.note,
            "artifacts": self.artifacts,
        }


def run_scorecard(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo).resolve()
    python = str(Path(args.python).expanduser()) if args.python else sys.executable
    output_dir = Path(args.output_dir).resolve() if args.output_dir else repo
    output_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")

    gates: list[GateResult] = []
    smoke_payload: dict[str, Any] | None = None

    if args.smoke_report:
        smoke_payload = _read_json(Path(args.smoke_report))
        gates.append(_smoke_gate_from_payload(smoke_payload, Path(args.smoke_report)))
    elif not args.skip_smoke:
        smoke_gate, smoke_payload = _run_smoke_gate(
            args, repo=repo, python=python, output_dir=output_dir, env=env
        )
        gates.append(smoke_gate)
    else:
        gates.append(
            GateResult(
                name="real_feature_smoke",
                status="skip",
                note="--skip-smoke was supplied; strict A+ mode requires this gate.",
            )
        )

    if not args.skip_commands:
        gates.extend(_run_command_gates(args, repo=repo, python=python, output_dir=output_dir, env=env))
    else:
        for name in _planned_command_names(repo):
            gates.append(GateResult(name=name, status="skip", note="--skip-commands was supplied"))

    gate_by_name = {gate.name: gate for gate in gates}
    areas = _grade_areas(gate_by_name, smoke_payload, area_gates=_active_area_gates(repo))
    all_a_plus = all(area["grade"] == "A+" for area in areas)
    payload = {
        "schema_version": SCORECARD_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "strict": bool(args.strict),
        "all_a_plus": all_a_plus,
        "area_count": len(areas),
        "areas": areas,
        "gates": [gate.to_dict() for gate in gates],
        "artifacts": {
            "json": str(output_dir / "release-readiness.report.json"),
            "markdown": str(output_dir / "RELEASE_READINESS.md"),
        },
    }
    (output_dir / "release-readiness.report.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "RELEASE_READINESS.md").write_text(_markdown(payload), encoding="utf-8")
    return payload


def _run_command_gates(
    args: argparse.Namespace,
    *,
    repo: Path,
    python: str,
    output_dir: Path,
    env: dict[str, str],
) -> list[GateResult]:
    commands: list[tuple[str, list[str], Path, int, str]] = [
        ("release_metadata", [python, "scripts/sync_release_metadata.py", "--check"], repo, 120, "required"),
        (
            "claim_audit",
            [
                python,
                "scripts/claim_audit.py",
                "--repo",
                str(repo),
                "--json",
                "--output",
                str(output_dir / "claim-audit.report.json"),
            ],
            repo,
            60,
            "required",
        ),
        ("ruff_check", [python, "-m", "ruff", "check", "."], repo, 180, "required"),
        ("ruff_format", [python, "-m", "ruff", "format", "--check", "."], repo, 180, "required"),
        ("mypy", [python, "-m", "mypy", "src"], repo, 300, "required"),
        ("pytest", [python, "-m", "pytest", "-q"], repo, 900, "required"),
        (
            "security_tests",
            [
                python,
                "-m",
                "pytest",
                "-q",
                "tests/test_security_hardening.py",
                "tests/test_discovery.py",
                "tests/test_integration.py",
            ],
            repo,
            420,
            "required",
        ),
        (
            "bandit",
            [python, "-m", "bandit", "-q", "-c", "pyproject.toml", "-r", "src", "scripts"],
            repo,
            240,
            "required",
        ),
        ("pip_audit", [python, "-m", "pip_audit"], repo, 240, "required"),
        (
            "package_build",
            [python, "scripts/build_release.py", "--verify-reproducible"],
            repo,
            300,
            "required",
        ),
        (
            "twine_check",
            [
                python,
                "-c",
                (
                    "import glob, subprocess, sys; files=glob.glob('dist/*'); "
                    "sys.exit(subprocess.call([sys.executable, '-m', 'twine', 'check', *files]))"
                ),
            ],
            repo,
            180,
            "required",
        ),
    ]
    if (repo / "web" / "package.json").exists():
        commands.extend(
            [
                ("web_bun_install", ["bun", "install", "--frozen-lockfile"], repo / "web", 300, "supporting"),
                ("web_bun_audit", ["bun", "audit"], repo / "web", 180, "required"),
                ("web_typecheck", ["bun", "run", "typecheck"], repo / "web", 240, "required"),
                ("web_lint", ["bun", "run", "lint"], repo / "web", 240, "required"),
                ("web_build", ["bun", "run", "build"], repo / "web", 300, "required"),
            ]
        )
    if (repo / "mcp" / "package.json").exists():
        commands.extend(
            [
                ("mcp_bun_install", ["bun", "install", "--frozen-lockfile"], repo / "mcp", 300, "supporting"),
                ("mcp_bun_audit", ["bun", "audit"], repo / "mcp", 180, "required"),
                ("mcp_bun_test", ["bun", "test"], repo / "mcp", 240, "required"),
                ("mcp_typecheck", ["bun", "run", "typecheck"], repo / "mcp", 240, "required"),
            ]
        )

    gates = [_run_git_status_clean(repo)]
    gates.extend(
        _run_gate(name, command, cwd=cwd, timeout=timeout, env=env, classification=classification)
        for name, command, cwd, timeout, classification in commands
    )
    gates.append(_run_gitleaks(repo, timeout=420, env=env, strict=bool(args.strict)))
    return gates


def _run_smoke_gate(
    args: argparse.Namespace,
    *,
    repo: Path,
    python: str,
    output_dir: Path,
    env: dict[str, str],
) -> tuple[GateResult, dict[str, Any] | None]:
    smoke_dir = output_dir / "real-feature-smoke"
    command = [
        python,
        "scripts/real_feature_smoke.py",
        "--repo",
        str(repo),
        "--python",
        python,
        "--base-dir",
        str(smoke_dir),
        "--json",
        "--full-mcp",
        "--strict-ci",
        "--auth-matrix",
        "--monitor-soak-minutes",
        str(args.monitor_soak_minutes),
    ]
    if args.quick:
        command.append("--quick")
    completed_gate = _run_gate(
        "real_feature_smoke",
        command,
        cwd=repo,
        timeout=max(900, int(float(args.monitor_soak_minutes) * 60) + 900),
        env=env,
        classification="required",
    )
    payload = _parse_stdout_json(completed_gate.stdout_tail)
    report_path = smoke_dir / "real_feature_smoke.report.json"
    if report_path.exists():
        payload = _read_json(report_path)
    completed_gate.artifacts.append(str(report_path))
    if completed_gate.status == "pass":
        if isinstance(payload, dict):
            failed_required = int(payload.get("failed_required") or 0)
            completed_gate.status = "pass" if failed_required == 0 else "fail"
            completed_gate.note = f"failed_required={failed_required}"
        else:
            completed_gate.status = "fail"
            completed_gate.note = "real-feature smoke passed but did not emit a readable report"
    return completed_gate, payload if isinstance(payload, dict) else None


def _run_gate(
    name: str,
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str],
    classification: str,
) -> GateResult:
    started = time.time()
    try:
        completed = subprocess.run(  # nosec B603
            command,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as err:
        return GateResult(
            name=name,
            status="fail",
            classification=classification,
            command=command,
            cwd=str(cwd),
            seconds=round(time.time() - started, 3),
            note=f"missing executable: {err.filename}",
        )
    except subprocess.TimeoutExpired as err:
        stdout = err.stdout if isinstance(err.stdout, str) else ""
        stderr = err.stderr if isinstance(err.stderr, str) else ""
        return GateResult(
            name=name,
            status="fail",
            classification=classification,
            command=command,
            cwd=str(cwd),
            code=124,
            seconds=round(time.time() - started, 3),
            stdout_tail=stdout[-3000:],
            stderr_tail=stderr[-3000:],
            note=f"timeout after {timeout}s",
        )
    status = "pass" if completed.returncode == 0 else "fail"
    stdout_tail = "" if status == "pass" else completed.stdout[-3000:]
    stderr_tail = "" if status == "pass" else completed.stderr[-3000:]
    return GateResult(
        name=name,
        status=status,
        classification=classification,
        command=command,
        cwd=str(cwd),
        code=completed.returncode,
        seconds=round(time.time() - started, 3),
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )


def _run_gitleaks(repo: Path, *, timeout: int, env: dict[str, str], strict: bool) -> GateResult:
    if shutil.which("gitleaks"):
        return _run_gate(
            "gitleaks",
            ["gitleaks", "detect", "--source", str(repo), "--redact", "--no-banner"],
            cwd=repo,
            timeout=timeout,
            env=env,
            classification="required",
        )
    if shutil.which("docker"):
        return _run_gate(
            "gitleaks",
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{repo}:/repo",
                "ghcr.io/gitleaks/gitleaks@sha256:691af3c7c5a48b16f187ce3446d5f194838f91238f27270ed36eef6359a574d9",
                "detect",
                "--source=/repo",
                "--redact",
                "--no-banner",
            ],
            cwd=repo,
            timeout=timeout,
            env=env,
            classification="required",
        )
    return GateResult(
        name="gitleaks",
        status="fail" if strict else "skip",
        note="gitleaks CLI or docker is required for strict local release scoring",
    )


def _run_git_status_clean(repo: Path) -> GateResult:
    started = time.time()
    git = shutil.which("git")
    if not git:
        return GateResult(
            name="git_status_clean",
            status="fail",
            classification="required",
            command=["git", "status", "--short"],
            cwd=str(repo),
            seconds=round(time.time() - started, 3),
            note="missing executable: git",
        )
    completed = subprocess.run(  # nosec B603
        [git, "status", "--short"],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    status_lines = [line for line in completed.stdout.splitlines() if line.strip()]
    deleted = sum(1 for line in status_lines if line[:2] in {" D", "D ", "DD"})
    untracked = sum(1 for line in status_lines if line.startswith("??"))
    modified = max(0, len(status_lines) - deleted - untracked)
    if completed.returncode != 0:
        note = "git status failed"
        status = "fail"
    elif status_lines:
        note = (
            f"working tree is not clean: {modified} modified/staged, {deleted} deleted, {untracked} untracked"
        )
        status = "fail"
    else:
        note = "working tree is clean"
        status = "pass"
    return GateResult(
        name="git_status_clean",
        status=status,
        classification="required",
        command=[git, "status", "--short"],
        cwd=str(repo),
        code=completed.returncode,
        seconds=round(time.time() - started, 3),
        stdout_tail=completed.stdout[-3000:],
        stderr_tail=completed.stderr[-3000:],
        note=note,
    )


def _smoke_gate_from_payload(payload: dict[str, Any], path: Path) -> GateResult:
    failed_required = int(payload.get("failed_required") or 0) if isinstance(payload, dict) else 1
    return GateResult(
        name="real_feature_smoke",
        status="pass" if failed_required == 0 else "fail",
        note=f"failed_required={failed_required}",
        artifacts=[str(path)],
    )


def _grade_areas(
    gate_by_name: dict[str, GateResult],
    smoke_payload: dict[str, Any] | None,
    *,
    area_gates: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    areas: list[dict[str, Any]] = []
    for area, required in (area_gates or AREA_GATES).items():
        gate_statuses: dict[str, str] = {}
        for name in required:
            gate = gate_by_name.get(name)
            gate_statuses[name] = gate.status if gate is not None else "missing"
        missing_markers = _missing_smoke_markers(area, smoke_payload)
        failed = [name for name, status in gate_statuses.items() if status != "pass"]
        grade = (
            "A+"
            if not failed and not missing_markers
            else "B"
            if all(status != "fail" for status in gate_statuses.values())
            else "F"
        )
        areas.append(
            {
                "area": area,
                "grade": grade,
                "required_gates": gate_statuses,
                "missing_smoke_markers": missing_markers,
                "notes": _area_note(grade, failed, missing_markers),
            }
        )
    return areas


def _active_area_gates(repo: Path) -> dict[str, tuple[str, ...]]:
    """Return gates for product surfaces that exist in this checkout."""
    active = dict(AREA_GATES)
    if not (repo / "web" / "package.json").is_file():
        active.pop("Web/docs copy")
        active["Security/static quality"] = tuple(
            name for name in active["Security/static quality"] if name != "web_bun_audit"
        )
    return active


def _missing_smoke_markers(area: str, smoke_payload: dict[str, Any] | None) -> list[str]:
    markers = A_PLUS_SMOKE_MARKERS.get(area, ())
    if not markers:
        return []
    if not isinstance(smoke_payload, dict):
        return list(markers)
    names = {
        str(item.get("name") or "")
        for item in smoke_payload.get("results", [])
        if isinstance(item, dict) and item.get("status") == "pass"
    }
    return [marker for marker in markers if marker not in names]


def _area_note(grade: str, failed: list[str], missing_markers: list[str]) -> str:
    if grade == "A+":
        return "All required gates and A+ smoke markers passed."
    details = []
    if failed:
        details.append("non-passing gates: " + ", ".join(failed))
    if missing_markers:
        details.append("missing smoke markers: " + ", ".join(missing_markers))
    return "; ".join(details)


def _markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Release Readiness",
        "",
        f"- Generated: `{payload['generated_at']}`",
        f"- Strict: `{payload['strict']}`",
        f"- All A+: `{payload['all_a_plus']}`",
        "",
        "| Area | Grade | Notes |",
        "| --- | --- | --- |",
    ]
    for area in payload["areas"]:
        lines.append(f"| {area['area']} | {area['grade']} | {area['notes']} |")
    lines.extend(["", "## Gates", ""])
    for gate in payload["gates"]:
        lines.append(f"- **{gate['status']}** `{gate['name']}` {gate.get('note') or ''}".rstrip())
    return "\n".join(lines).rstrip() + "\n"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_stdout_json(stdout_tail: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout_tail)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _planned_command_names(repo: Path) -> list[str]:
    names = [
        "git_status_clean",
        "release_metadata",
        "claim_audit",
        "ruff_check",
        "ruff_format",
        "mypy",
        "pytest",
        "security_tests",
        "bandit",
        "pip_audit",
        "package_build",
        "twine_check",
        "gitleaks",
    ]
    if (repo / "web" / "package.json").exists():
        names.extend(["web_bun_install", "web_bun_audit", "web_typecheck", "web_lint", "web_build"])
    if (repo / "mcp" / "package.json").exists():
        names.extend(["mcp_bun_install", "mcp_bun_audit", "mcp_bun_test", "mcp_typecheck"])
    return names


def create_parser() -> argparse.ArgumentParser:
    repo_default = Path(__file__).resolve().parents[1]
    venv_python = repo_default / ".venv" / "bin" / "python"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=repo_default)
    parser.add_argument("--python", default=str(venv_python if venv_python.exists() else sys.executable))
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--quick", action="store_true", help="Pass --quick to the real-feature smoke")
    parser.add_argument("--skip-smoke", action="store_true")
    parser.add_argument("--skip-commands", action="store_true")
    parser.add_argument("--smoke-report", type=Path)
    parser.add_argument("--monitor-soak-minutes", type=float, default=10.0)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--plan-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    if args.plan_only:
        repo = Path(args.repo).resolve()
        areas = list(_active_area_gates(repo))
        commands = _planned_command_names(repo)
        smoke_command = (
            "scripts/real_feature_smoke.py --json --full-mcp --strict-ci --auth-matrix --monitor-soak-minutes"
        )
        payload = {
            "schema_version": SCORECARD_SCHEMA_VERSION,
            "areas": areas,
            "commands": commands,
            "smoke_command": smoke_command,
        }
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json_output else "\n".join(areas))
        return 0

    payload = run_scorecard(args)
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        raw_artifacts = payload.get("artifacts")
        artifacts: dict[str, Any] = raw_artifacts if isinstance(raw_artifacts, dict) else {}
        print(f"release readiness: all_a_plus={payload['all_a_plus']} report={artifacts.get('json')}")
    return 0 if (payload["all_a_plus"] or not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
