from __future__ import annotations

from docpull_bench.runner import _report_command
from docpull_bench.sanitization import scrub_secrets


def test_scrub_secrets_removes_environment_credentials(monkeypatch) -> None:
    monkeypatch.setenv("VENDOR_API_KEY", "super-secret-value")

    scrubbed = scrub_secrets("failure Bearer abcdefghijkl and super-secret-value")

    assert "super-secret-value" not in scrubbed
    assert "abcdefghijkl" not in scrubbed
    assert scrubbed == "failure Bearer [REDACTED] and [REDACTED]"


def test_report_command_hides_executable_path_and_adapter_command() -> None:
    command = [
        "/private/venv/bin/docpull-bench",
        "run",
        "suite.yaml",
        "--command",
        "vendor --token secret --input {input} --output {output}",
    ]

    assert _report_command(command) == [
        "docpull-bench",
        "run",
        "suite.yaml",
        "--command",
        "[REDACTED_ADAPTER_COMMAND]",
    ]
