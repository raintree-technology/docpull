"""Execute registry retry steps without publishing packages or waiting in tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.6.0"
PYPI_DELAY = f"PyPI package 'docpull' exists, but version '{VERSION}' was not found (status: 404)."


def _workflow(name: str) -> dict:
    # BaseLoader preserves GitHub's `on` key instead of YAML 1.1's boolean True.
    return yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader)


def test_mcp_registration_depends_on_successful_pypi_publication() -> None:
    pypi = _workflow("publish.yml")
    mcp = _workflow("publish-mcp-registry.yml")
    registration = pypi["jobs"]["register-mcp"]
    assert registration["needs"] == ["build", "publish"]
    assert "if" not in registration  # Preserve GitHub's default success() dependency gate.
    assert registration["uses"] == "./.github/workflows/publish-mcp-registry.yml"
    assert registration["with"]["version"] == "${{ needs.build.outputs.version }}"
    assert registration["permissions"] == {"contents": "read", "id-token": "write"}
    assert set(mcp["on"]) == {"workflow_call", "workflow_dispatch"}
    for event in mcp["on"].values():
        assert event["inputs"]["version"]["required"] == "true"
        assert event["inputs"]["version"]["type"] == "string"
    assert mcp["concurrency"]["group"] != pypi["concurrency"]["group"]
    assert "github.workflow" not in mcp["concurrency"]["group"]
    check = next(
        s for s in mcp["jobs"]["publish"]["steps"] if s.get("name") == "Verify registry metadata version"
    )
    assert check["env"]["REQUESTED_VERSION"] == "${{ inputs.version }}"


def _run_step(
    tmp_path: Path,
    workflow: str,
    step_name: str,
    errors: list[str],
    *,
    installed_version: str = VERSION,
    valid_export: bool = True,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.jsonl"
    fixture = {
        "errors": errors,
        "installed_version": installed_version,
        "valid_export": valid_export,
    }
    (tmp_path / "fixture.json").write_text(json.dumps(fixture))
    stub = (
        f"#!{sys.executable}\n"
        + """
import json, os, sys
from pathlib import Path
root = Path(os.environ["REGISTRY_TEST_ROOT"])
fixture = json.loads((root / "fixture.json").read_text())
name = Path(sys.argv[0]).name
with (root / "calls.jsonl").open("a") as stream:
    stream.write(json.dumps({"name": name, "args": sys.argv[1:]}) + "\\n")
if name == "sleep" or (name == "npm" and sys.argv[1] == "init"):
    sys.exit(0)
counter = root / "attempts"
attempt = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(attempt + 1))
error = fixture["errors"][min(attempt, len(fixture["errors"]) - 1)]
if error:
    print(error, file=sys.stderr)
    sys.exit(7)
if name == "npm":
    package = Path("node_modules/@raintree-technology/docpull-sdk")
    package.mkdir(parents=True, exist_ok=True)
    (package / "package.json").write_text(json.dumps({
        "version": fixture["installed_version"], "type": "module", "main": "index.js"
    }))
    (package / "index.js").write_text(
        "export function readPack() {}" if fixture["valid_export"] else "export const broken = true;"
    )
print("registry command succeeded")
"""
    )
    for path in (bin_dir / "npm", bin_dir / "sleep", tmp_path / "mcp-publisher"):
        path.write_text(stub)
        path.chmod(0o755)
    step = next(s for s in _workflow(workflow)["jobs"]["publish"]["steps"] if s.get("name") == step_name)
    result = subprocess.run(
        [shutil.which("bash") or "bash", "-c", step["run"]],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "REGISTRY_TEST_ROOT": str(tmp_path),
            "VERSION": VERSION,
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result, [json.loads(line) for line in calls.read_text().splitlines()]


def test_npm_waits_for_propagation_and_refreshes_metadata(tmp_path: Path) -> None:
    result, calls = _run_step(
        tmp_path,
        "publish-sdk.yml",
        "Smoke install from npm",
        ["npm error code ETARGET", "npm error code E404", "npm error code ETIMEDOUT", ""],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    installs = [c["args"] for c in calls if c["name"] == "npm" and c["args"][0] == "install"]
    assert len(installs) == 4
    for args in installs:
        assert f"@raintree-technology/docpull-sdk@{VERSION}" in args
        assert {"--prefer-online", "--fetch-retries=0", "--fetch-timeout=20000"} <= set(args)
    assert [c["args"] for c in calls if c["name"] == "sleep"] == [["20"]] * 3


@pytest.mark.parametrize("code", ["E401", "E403", "EINTEGRITY", "EBADENGINE"])
def test_npm_permanent_errors_fail_immediately(tmp_path: Path, code: str) -> None:
    result, calls = _run_step(
        tmp_path, "publish-sdk.yml", "Smoke install from npm", [f"npm error code {code}", ""]
    )
    assert result.returncode == 7
    assert not any(c["name"] == "sleep" for c in calls)


@pytest.mark.parametrize("installed_version,valid_export", [("0.0.0", True), (VERSION, False)])
def test_npm_smoke_rejects_wrong_version_or_broken_exports(
    tmp_path: Path, installed_version: str, valid_export: bool
) -> None:
    result, _ = _run_step(
        tmp_path,
        "publish-sdk.yml",
        "Smoke install from npm",
        [""],
        installed_version=installed_version,
        valid_export=valid_export,
    )
    assert result.returncode != 0


def test_mcp_retries_only_pypi_version_propagation(tmp_path: Path) -> None:
    result, calls = _run_step(
        tmp_path, "publish-mcp-registry.yml", "Publish server metadata", [PYPI_DELAY, PYPI_DELAY, ""]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert len([c for c in calls if c["name"] == "mcp-publisher"]) == 3
    assert [c["args"] for c in calls if c["name"] == "sleep"] == [["20"], ["20"]]


@pytest.mark.parametrize(
    "error", ["Unauthorized (401)", "Invalid server metadata", PYPI_DELAY.replace(VERSION, "0.0.0")]
)
def test_mcp_permanent_errors_fail_immediately(tmp_path: Path, error: str) -> None:
    result, calls = _run_step(tmp_path, "publish-mcp-registry.yml", "Publish server metadata", [error, ""])
    assert result.returncode == 7
    assert len(calls) == 1


@pytest.mark.parametrize(
    "workflow,step,error,command",
    [
        ("publish-sdk.yml", "Smoke install from npm", "npm error code ETARGET", "npm"),
        ("publish-mcp-registry.yml", "Publish server metadata", PYPI_DELAY, "mcp-publisher"),
    ],
)
def test_registry_propagation_timeout_fails(
    tmp_path: Path, workflow: str, step: str, error: str, command: str
) -> None:
    result, calls = _run_step(tmp_path, workflow, step, [error])
    assert result.returncode == 1
    assert "after 31 attempts" in result.stdout
    assert len([c for c in calls if c["name"] == command and c["args"][0] != "init"]) == 31
    assert len([c for c in calls if c["name"] == "sleep"]) == 30
