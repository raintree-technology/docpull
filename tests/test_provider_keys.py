"""Provider key handling tests."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from docpull import provider_cli

pytestmark = pytest.mark.internal_legacy


def main(argv: list[str]) -> int:
    command, *rest = argv
    if command == "providers":
        return provider_cli.run_provider_cli(rest)
    if command in {"tavily", "exa"}:
        return provider_cli.run_provider_extension_cli(command, rest)
    raise AssertionError(f"Unexpected provider CLI command: {command}")


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


def test_providers_auth_text_output_lists_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    assert main(["providers", "auth"]) == 0

    output = capsys.readouterr().out
    assert "Provider local auth preflight" in output
    assert "Secret handling" in output


def test_providers_auth_require_ready_gives_agent_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    assert main(["providers", "auth", "--provider", "tavily", "--json", "--require-ready"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"]["tavily"]["ready"] is False
    assert payload["next_actions"][0]["command"] == "docpull providers capabilities --json"
    assert any(action["command"] == "docpull providers init tavily" for action in payload["next_actions"])


def test_providers_auth_redacts_paths_for_agent_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    secret_path = tmp_path / "config" / "docpull" / "secrets.env"
    secret_path.parent.mkdir(parents=True)
    secret_path.write_text('EXA_API_KEY="test-exa-secret"\n', encoding="utf-8")

    assert main(["providers", "auth", "--provider", "exa", "--json", "--redact-paths"]) == 0

    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    payload = json.loads(output)
    assert payload["paths_redacted"] is True
    assert payload["user_secrets_path"] == "[redacted]"
    assert payload["project_env_path"] == "[redacted]"
    assert payload["providers"]["exa"]["api_key_source_path"] == "[redacted]"


def test_providers_auth_reports_invalid_key_without_exposing_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "test-secret\nheader-injection")

    assert main(["providers", "auth", "--json"]) == 0

    output = capsys.readouterr().out
    assert "test-secret" not in output
    assert "header-injection" not in output
    payload = json.loads(output)
    assert payload["providers"]["tavily"]["ready"] is False
    assert payload["providers"]["tavily"]["reason"] == "invalid_api_key"
    assert payload["providers"]["tavily"]["api_key_source"] == "invalid_env"
    assert "control characters" in payload["providers"]["tavily"]["api_key_invalid_reason"]


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


def test_providers_init_rejects_unsafe_key_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr("sys.stdin", io.StringIO("bad-secret\x1fvalue\n"))

    with pytest.raises(provider_cli.ProviderCliError, match="control characters"):
        provider_cli.init_provider_auth("exa", from_stdin=True)

    assert not (tmp_path / "config" / "docpull" / "secrets.env").exists()


def test_providers_init_project_key_updates_gitignore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO("parallel-secret\n"))

    result = provider_cli.init_provider_auth("parallel", project=True, from_stdin=True)

    assert result["provider"] == "parallel"
    assert result["key_source"] == "project_env"
    assert (tmp_path / ".env.local").read_text(encoding="utf-8") == 'PARALLEL_API_KEY="parallel-secret"\n'
    assert ".env.local" in (tmp_path / ".gitignore").read_text(encoding="utf-8")

    monkeypatch.setattr("sys.stdin", io.StringIO("another-secret\n"))
    with pytest.raises(provider_cli.ProviderCliError, match="already contains"):
        provider_cli.init_provider_auth("parallel", project=True, from_stdin=True)
