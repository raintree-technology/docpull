"""Tests for the installation doctor."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

import docpull.doctor as doctor


def test_check_dependency_reports_present_and_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_import_module(name: str) -> object:
        if name == "missing_module":
            raise ImportError("no module")
        return object()

    monkeypatch.setattr(doctor, "import_module", fake_import_module)

    assert doctor.check_dependency("present_module", "present-package") == (True, "[OK] present-package")
    assert doctor.check_dependency("missing_module", "missing-package") == (
        False,
        "[MISSING] missing-package",
    )
    assert doctor.check_dependency("missing_module", "optional-package", optional=True) == (
        False,
        "[WARN] optional-package (optional - not installed)",
    )


def test_check_network_reports_dns_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_dns(_hostname: str) -> str:
        raise socket.gaierror("simulated DNS failure")

    monkeypatch.setattr(socket, "gethostbyname", fail_dns)

    assert doctor.check_network() == (False, "[FAIL] Network connectivity - DNS resolution failed")


def test_check_network_reports_unexpected_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_network(_hostname: str) -> str:
        raise RuntimeError("resolver unavailable")

    monkeypatch.setattr(socket, "gethostbyname", fail_network)

    success, message = doctor.check_network()
    assert success is False
    assert message == "[WARN] Network connectivity - resolver unavailable"


def test_check_output_dir_reports_writable_and_unwritable(tmp_path: Path) -> None:
    assert doctor.check_output_dir(tmp_path / "out") == (
        True,
        f"[OK] Output directory writable ({tmp_path / 'out'})",
    )

    existing_file = tmp_path / "not-a-directory"
    existing_file.write_text("not a dir", encoding="utf-8")

    success, message = doctor.check_output_dir(existing_file)
    assert success is False
    assert "[FAIL] Output directory" in message
    assert str(existing_file) in message


def test_run_doctor_plain_output_reports_missing_core_dependency(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_check_dependency(module_name: str, package_name: str | None = None, optional: bool = False):
        display = package_name or module_name
        if optional:
            return False, f"[WARN] {display} (optional - not installed)"
        if module_name == "html2text":
            return False, f"[MISSING] {display}"
        return True, f"[OK] {display}"

    monkeypatch.setattr(doctor, "check_dependency", fake_check_dependency)
    monkeypatch.setattr(doctor, "check_network", lambda: (True, "[OK] Network connectivity"))
    monkeypatch.setattr(doctor, "check_output_dir", lambda _output_dir=None: (True, "[OK] Output"))
    monkeypatch.setattr(
        doctor,
        "check_agent_browser_availability",
        lambda: (True, "[OK] agent-browser backend (/bin/agent-browser)"),
    )
    monkeypatch.setattr(
        doctor,
        "check_vercel_sandbox_availability",
        lambda: (True, "[OK] Vercel Sandbox backend (/bin/sandbox)"),
    )
    monkeypatch.setattr(
        doctor,
        "check_e2b_sandbox_availability",
        lambda: (True, "[OK] E2B Sandbox backend"),
    )

    assert doctor.run_doctor(use_rich=False) == 1

    output = capsys.readouterr().out
    assert "Core Dependencies:" in output
    assert "[MISSING] html2text" in output
    assert "WARNING: Some core dependencies are missing!" in output
    assert "pipx reinstall docpull --force" in output


def test_run_doctor_plain_output_reports_optional_feature_guidance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_check_dependency(module_name: str, package_name: str | None = None, optional: bool = False):
        display = package_name or module_name
        if optional:
            return False, f"[WARN] {display} (optional - not installed)"
        return True, f"[OK] {display}"

    monkeypatch.setattr(doctor, "check_dependency", fake_check_dependency)
    monkeypatch.setattr(doctor, "check_network", lambda: (True, "[OK] Network connectivity"))
    monkeypatch.setattr(doctor, "check_output_dir", lambda _output_dir=None: (True, "[OK] Output"))
    monkeypatch.setattr(
        doctor,
        "check_agent_browser_availability",
        lambda: (False, "[WARN] agent-browser backend unavailable"),
    )
    monkeypatch.setattr(
        doctor,
        "check_vercel_sandbox_availability",
        lambda: (False, "[WARN] Vercel Sandbox backend unavailable"),
    )
    monkeypatch.setattr(
        doctor,
        "check_e2b_sandbox_availability",
        lambda: (False, "[WARN] E2B Sandbox backend unavailable"),
    )

    assert doctor.run_doctor(use_rich=False) == 0

    output = capsys.readouterr().out
    assert "All core dependencies installed correctly!" in output
    assert "Optional features available:" in output
    assert "Browser rendering" in output
    assert "Cloud rendering" in output
    assert "pip install docpull[all]" in output


def test_run_doctor_plain_output_reports_optional_external_tool_guidance(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_check_dependency(module_name: str, package_name: str | None = None, optional: bool = False):
        display = package_name or module_name
        return True, f"[OK] {display}"

    monkeypatch.setattr(doctor, "check_dependency", fake_check_dependency)
    monkeypatch.setattr(doctor, "check_network", lambda: (True, "[OK] Network connectivity"))
    monkeypatch.setattr(doctor, "check_output_dir", lambda _output_dir=None: (True, "[OK] Output"))
    monkeypatch.setattr(
        doctor,
        "check_agent_browser_availability",
        lambda: (False, "[WARN] agent-browser backend unavailable"),
    )
    monkeypatch.setattr(
        doctor,
        "check_vercel_sandbox_availability",
        lambda: (False, "[WARN] Vercel Sandbox backend unavailable"),
    )
    monkeypatch.setattr(
        doctor,
        "check_e2b_sandbox_availability",
        lambda: (False, "[WARN] E2B Sandbox backend unavailable"),
    )

    assert doctor.run_doctor(use_rich=False) == 0

    output = capsys.readouterr().out
    assert "Optional External Tools:" in output
    assert "agent-browser backend unavailable" in output
    assert "Vercel Sandbox backend unavailable" in output
    assert "E2B Sandbox backend unavailable" in output
    assert "Browser rendering" in output


def test_run_doctor_rich_output_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_check_dependency(module_name: str, package_name: str | None = None, optional: bool = False):
        display = package_name or module_name
        return True, f"[OK] {display}"

    monkeypatch.setattr(doctor, "check_dependency", fake_check_dependency)
    monkeypatch.setattr(doctor, "check_network", lambda: (True, "[OK] Network connectivity"))
    monkeypatch.setattr(doctor, "check_output_dir", lambda _output_dir=None: (True, "[OK] Output"))
    monkeypatch.setattr(
        doctor,
        "check_agent_browser_availability",
        lambda: (True, "[OK] agent-browser backend (/bin/agent-browser)"),
    )
    monkeypatch.setattr(
        doctor,
        "check_vercel_sandbox_availability",
        lambda: (True, "[OK] Vercel Sandbox backend (/bin/sandbox)"),
    )
    monkeypatch.setattr(
        doctor,
        "check_e2b_sandbox_availability",
        lambda: (True, "[OK] E2B Sandbox backend"),
    )

    assert doctor.run_doctor(use_rich=True) == 0

    output = capsys.readouterr().out
    assert "Running docpull diagnostics" in output
    assert "All core dependencies installed correctly!" in output
