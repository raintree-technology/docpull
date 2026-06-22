"""Provider live probe tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docpull import parallel_workflows, provider_cli, provider_probes
from docpull.cli import main
from docpull.provider_probes import ProbeHttpResponse


@pytest.fixture
def isolated_provider_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)


def test_tavily_safe_probe_uses_usage_endpoint_without_account_metadata(
    isolated_provider_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(**kwargs: Any) -> ProbeHttpResponse:
        captured.update(kwargs)
        return ProbeHttpResponse(
            status=200,
            body={
                "key": {"usage": 1, "limit": 100},
                "account": {"current_plan": "Bootstrap"},
            },
            headers={},
        )

    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setattr(provider_probes, "_http_json_request", fake_request)

    payload = provider_probes.provider_probe_payload(["tavily"], mode="safe")

    result = payload["providers"]["tavily"]
    assert captured["url"] == provider_probes.TAVILY_USAGE_URL
    assert captured["method"] == "GET"
    assert captured["headers"] == {"Authorization": "Bearer test-tavily-key"}
    assert result["live_valid"] is True
    assert result["workflow_ready"] is True
    assert result["may_consume_quota"] is False
    assert "account_metadata" not in result


def test_exa_safe_probe_uses_team_endpoint_with_metadata_opt_in(
    isolated_provider_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(**kwargs: Any) -> ProbeHttpResponse:
        captured.update(kwargs)
        return ProbeHttpResponse(
            status=200,
            body={"team": {"id": "team_1"}, "limits": {"search": 10}},
            headers={},
        )

    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
    monkeypatch.setattr(provider_probes, "_http_json_request", fake_request)

    payload = provider_probes.provider_probe_payload(
        ["exa"],
        mode="safe",
        include_account_metadata=True,
    )

    result = payload["providers"]["exa"]
    assert captured["url"] == provider_probes.EXA_TEAM_URL
    assert captured["method"] == "GET"
    assert captured["headers"] == {"x-api-key": "test-exa-key"}
    assert result["live_valid"] is True
    assert result["account_metadata"] == {"team": {"id": "team_1"}, "limits": {"search": 10}}


def test_parallel_safe_probe_reports_configured_without_live_request(
    isolated_provider_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_request(**_kwargs: Any) -> ProbeHttpResponse:
        raise AssertionError("safe Parallel probe must not call the network")

    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    monkeypatch.setattr(provider_probes, "_http_json_request", fail_request)

    payload = provider_probes.provider_probe_payload(["parallel"], mode="safe")

    result = payload["providers"]["parallel"]
    assert result["configured"] is True
    assert result["live_checked"] is False
    assert result["live_valid"] is None
    assert result["workflow_ready"] is True
    assert result["probe_kind"] == "no_safe_live_probe"
    assert any("mode validation" in action["command"] for action in payload["next_actions"])


def test_parallel_validation_probe_interprets_422_as_verified(
    isolated_provider_env: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_request(**kwargs: Any) -> ProbeHttpResponse:
        captured.update(kwargs)
        return ProbeHttpResponse(
            status=422,
            body={"error": {"message": "Request validation error", "ref_id": "search_1"}},
            headers={},
        )

    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    monkeypatch.setattr(provider_probes, "_http_json_request", fake_request)

    payload = provider_probes.provider_probe_payload(["parallel"], mode="validation")

    result = payload["providers"]["parallel"]
    assert captured["url"] == provider_probes.PARALLEL_SEARCH_URL
    assert captured["method"] == "POST"
    assert captured["body"] == {}
    assert result["live_checked"] is True
    assert result["live_valid"] is True
    assert result["workflow_ready"] is True
    assert result["quota_state"] == "auth_verified_request_rejected"
    assert result["request_id"] == "search_1"


def test_providers_probe_cli_json_require_verified_uses_exit_status(
    isolated_provider_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_request(**_kwargs: Any) -> ProbeHttpResponse:
        return ProbeHttpResponse(status=200, body={"requestId": "req_1"}, headers={})

    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")
    monkeypatch.setattr(provider_probes, "_http_json_request", fake_request)

    assert main(["providers", "probe", "--provider", "exa", "--json", "--require-verified"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["verified_providers"] == ["exa"]
    assert payload["providers"]["exa"]["request_id"] == "req_1"
    assert "test-exa-key" not in json.dumps(payload)


def test_providers_probe_require_verified_fails_for_parallel_safe_mode(
    isolated_provider_env: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")

    assert main(["providers", "probe", "--provider", "parallel", "--json", "--require-verified"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"]["parallel"]["live_checked"] is False
    assert payload["verified_providers"] == []


def test_provider_extension_alias_routes_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_provider_cli(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(provider_cli, "run_provider_cli", fake_run_provider_cli)

    assert provider_cli.run_provider_extension_cli("exa", ["probe", "--json"]) == 0
    assert captured["argv"] == ["probe", "--provider", "exa", "--json"]


def test_parallel_probe_alias_delegates_to_provider_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_provider_cli(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(provider_cli, "run_provider_cli", fake_run_provider_cli)

    assert parallel_workflows.run_parallel_cli(["probe", "--mode", "validation", "--json"]) == 0
    assert captured["argv"] == [
        "probe",
        "--provider",
        "parallel",
        "--mode",
        "validation",
        "--timeout",
        "15.0",
        "--max-estimated-cost",
        "0.01",
        "--json",
    ]
