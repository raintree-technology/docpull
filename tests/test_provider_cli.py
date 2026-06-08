"""Provider-neutral CLI tests."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from docpull.cli import main


def test_providers_auth_json_reports_partial_readiness_without_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-secret")

    assert main(["providers", "auth", "--json"]) == 0

    output = capsys.readouterr().out
    assert "test-tavily-secret" not in output
    payload = json.loads(output)
    assert payload["ready_count"] == 1
    assert payload["ready_providers"] == ["tavily"]
    assert payload["providers"]["tavily"]["api_key_source"] == "env"
    assert payload["providers"]["parallel"]["reason"] == "missing_api_key"


def test_providers_init_writes_selected_user_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("sys.stdin", io.StringIO("exa-secret\n"))

    assert main(["providers", "init", "exa", "--from-stdin"]) == 0

    output = capsys.readouterr().out
    assert "exa-secret" not in output
    secret_path = tmp_path / "config" / "docpull" / "secrets.env"
    assert secret_path.exists()
    assert 'EXA_API_KEY="exa-secret"' in secret_path.read_text(encoding="utf-8")


def test_providers_context_pack_dry_run_uses_available_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "test-exa-secret")

    assert (
        main(
            [
                "providers",
                "context-pack",
                "Build a pack",
                "--provider",
                "all",
                "--dry-run",
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "test-exa-secret" not in output
    payload = json.loads(output)
    assert payload["providers"] == ["exa"]
    assert payload["requested_providers"] == ["parallel", "tavily", "exa"]
    assert {item["provider"] for item in payload["skipped_providers"]} == {"parallel", "tavily"}
