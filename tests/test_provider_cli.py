"""Provider-neutral CLI tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from docpull import provider_cli
from docpull.cli import main


def test_provider_extension_help_is_actionable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert provider_cli.run_provider_extension_cli("tavily", ["--help"]) == 0

    output = capsys.readouterr().out
    assert "docpull tavily auth --json --require-ready" in output
    assert "docpull tavily map-pack" in output


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


def test_provider_context_pack_explicit_live_run_fails_without_ready_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)

    assert main(["tavily", "context-pack", "Find docs", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context-pack"
    assert "No requested providers are ready" in payload["error"]["message"]
    assert "docpull providers init" in payload["error"]["message"]


def test_provider_context_packs_writes_non_dry_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_statuses(providers: list[str]) -> dict[str, dict[str, object]]:
        return {
            provider: {
                "ready": True,
                "reason": "ready",
                "api_key_env_var": f"{provider.upper()}_API_KEY",
            }
            for provider in providers
        }

    def fake_case(**kwargs: object) -> dict[str, object]:
        provider = Path(str(kwargs["output_dir"])).name
        return {"provider": provider, "output_dir": str(kwargs["output_dir"])}

    monkeypatch.setattr(provider_cli, "_live_provider_statuses", fake_statuses)
    monkeypatch.setattr(provider_cli, "_run_parallel_context_case", fake_case)
    monkeypatch.setattr(provider_cli, "_run_tavily_case", fake_case)
    monkeypatch.setattr(provider_cli, "_run_exa_case", fake_case)

    report = provider_cli.run_provider_context_packs(
        objective="Build a pack",
        queries=["Parallel docs"],
        providers=["parallel", "tavily", "exa"],
        output_dir=tmp_path / "providers",
        include_domains=["docs.parallel.ai"],
        mode="advanced",
        max_search_results=2,
        extract_limit=1,
        max_estimated_cost=1.0,
        dry_run=False,
    )

    assert [case["provider"] for case in report["cases"]] == ["parallel", "tavily", "exa"]
    assert (tmp_path / "providers" / "provider-packs.report.json").exists()


def test_provider_context_pack_budget_zero_dry_run_marks_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_statuses(providers: list[str]) -> dict[str, dict[str, object]]:
        return {
            provider: {
                "ready": True,
                "reason": "ready",
                "api_key_env_var": f"{provider.upper()}_API_KEY",
            }
            for provider in providers
        }

    def fail_case(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("provider case should not run")

    monkeypatch.setattr(provider_cli, "_live_provider_statuses", fake_statuses)
    monkeypatch.setattr(provider_cli, "_run_parallel_context_case", fail_case)

    report = provider_cli.run_provider_context_packs(
        objective="Build a pack",
        queries=["Parallel docs"],
        providers=["parallel"],
        output_dir=tmp_path / "providers",
        include_domains=["docs.parallel.ai"],
        mode="advanced",
        max_search_results=2,
        extract_limit=1,
        max_estimated_cost=1.0,
        dry_run=True,
        budget_limit=0,
    )

    assert report["blocked_by_budget"] is True
    assert report["planned_cases"][0]["blocked_by_budget"] is True
    assert report["cases"] == []


def test_provider_extract_pack_budget_zero_blocks_adapter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_adapter(_provider: str) -> object:
        raise AssertionError("provider adapter should not be created")

    monkeypatch.setattr(provider_cli, "_provider_adapter", fail_adapter)

    report = provider_cli.run_provider_extract_pack(
        provider="exa",
        urls=["https://docs.example.com/page"],
        url_file=None,
        objective="Extract",
        queries=[],
        output_dir=tmp_path / "extract",
        mode="advanced",
        dry_run=True,
        budget_limit=0,
    )

    assert report["blocked_by_budget"] is True
    assert report["blocked_action"]["provider"] == "exa"


def test_provider_context_pack_cli_text_report_lists_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fake_context_packs(**kwargs: object) -> dict[str, object]:
        output_dir = Path(str(kwargs["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / "provider-packs.report.json"
        report_path.write_text("{}", encoding="utf-8")
        return {
            "artifacts": {"json": str(report_path)},
            "cases": [{"provider": "exa"}],
            "skipped_providers": [{"provider": "parallel"}],
        }

    monkeypatch.setattr(provider_cli, "run_provider_context_packs", fake_context_packs)

    assert (
        main(
            [
                "providers",
                "context-pack",
                "Build a pack",
                "--output-dir",
                str(tmp_path / "providers"),
            ]
        )
        == 0
    )

    output = capsys.readouterr().out
    assert "Provider context-pack report" in output
    assert "parallel" in output


def test_provider_capabilities_json_lists_shared_and_specific_surfaces(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["providers", "capabilities", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["providers"] == ["parallel", "tavily", "exa"]
    assert {
        capability["id"]
        for capability in payload["capabilities"]["tavily"]
        if capability["status"] == "available"
    } >= {"context-pack", "extract-pack", "map-pack"}
    assert {
        capability["id"] for capability in payload["capabilities"]["exa"] if capability["status"] == "planned"
    } >= {"agent-pack", "monitor-pack"}


def test_provider_extension_alias_routes_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_provider_cli(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(provider_cli, "run_provider_cli", fake_run_provider_cli)

    assert provider_cli.run_provider_extension_cli("tavily", ["capabilities"]) == 0
    assert captured["argv"] == ["capabilities", "--provider", "tavily"]


def test_provider_extract_pack_dry_run_reports_urls(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "providers",
                "extract-pack",
                "https://docs.example.com/a",
                "--provider",
                "exa",
                "--output-dir",
                str(tmp_path / "extract"),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "exa"
    assert payload["urls"] == ["https://docs.example.com/a"]
    assert payload["dry_run"] is True


def test_provider_tavily_map_pack_dry_run_reports_request(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "tavily",
                "map-pack",
                "https://docs.example.com",
                "--instructions",
                "Find API reference pages",
                "--output-dir",
                str(tmp_path / "map"),
                "--dry-run",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["provider"] == "tavily"
    assert payload["workflow"] == "tavily-map-pack"
    assert payload["request_options"]["include_domains"] == ["docs.example.com"]
    assert payload["request_options"]["allow_external"] is False


def test_provider_tavily_map_pack_uses_adapter_without_live_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def map_pack(self, **kwargs: object) -> dict[str, object]:
            assert kwargs["url"] == "https://docs.example.com"
            assert kwargs["include_domains"] == ["docs.example.com"]
            assert kwargs["limit"] == 5
            return {
                "provider": "tavily",
                "workflow": "tavily-map-pack",
                "candidate_count": 2,
                "artifacts": {"pack": str(tmp_path / "map" / "discovery.pack.json")},
            }

    monkeypatch.setattr(provider_cli, "_provider_adapter", lambda _provider: FakeAdapter())

    report = provider_cli.run_provider_map_pack(
        provider="tavily",
        url="https://docs.example.com",
        objective="Map docs",
        query=None,
        instructions=None,
        output_dir=tmp_path / "map",
        include_domains=[],
        exclude_domains=[],
        select_paths=[],
        select_domains=[],
        exclude_paths=[],
        max_depth=1,
        max_breadth=20,
        limit=5,
        allow_external=False,
        timeout=30.0,
        dry_run=False,
    )

    assert report["provider"] == "tavily"
    assert report["candidate_count"] == 2
    assert report["dry_run"] is False


def test_provider_extract_pack_uses_adapter_without_live_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        def extract_pack(self, **kwargs: object) -> SimpleNamespace:
            output_dir = Path(str(kwargs["output_dir"]))
            output_dir.mkdir(parents=True, exist_ok=True)
            pack_path = output_dir / "tavily.pack.json"
            pack_path.write_text("{}", encoding="utf-8")
            assert kwargs["urls"] == ["https://docs.example.com/a"]
            return SimpleNamespace(
                documents=[object()],
                output_dir=output_dir,
                pack_path=pack_path,
            )

    monkeypatch.setattr(provider_cli, "_provider_adapter", lambda _provider: FakeAdapter())
    monkeypatch.setattr(
        provider_cli,
        "_provider_case_payload",
        lambda _result, **kwargs: {"name": kwargs["name"], "workflow": kwargs["workflow"]},
    )

    report = provider_cli.run_provider_extract_pack(
        provider="tavily",
        urls=["https://docs.example.com/a"],
        url_file=None,
        objective="Extract docs",
        queries=[],
        output_dir=tmp_path / "extract",
        mode="basic",
        dry_run=False,
    )

    assert report["provider"] == "tavily"
    assert report["record_count"] == 1
    assert report["case"]["name"] == "tavily-extract"
    assert report["artifacts"]["pack"].endswith("tavily.pack.json")


def test_provider_extension_alias_routes_to_provider_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, list[str]] = {}

    def fake_run_provider_cli(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(provider_cli, "run_provider_cli", fake_run_provider_cli)

    assert provider_cli.run_provider_extension_cli("exa", ["context-pack", "Build a pack"]) == 0
    assert captured["argv"] == ["context-pack", "Build a pack", "--provider", "exa"]


def test_main_provider_extension_alias_routes_extract_pack(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_extract_pack(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {
            "artifacts": {"pack": "/tmp/exa.pack.json"},
            "record_count": 1,
        }

    monkeypatch.setattr(provider_cli, "run_provider_extract_pack", fake_extract_pack)

    assert (
        main(
            [
                "exa",
                "extract-pack",
                "https://docs.example.com/a",
                "--output-dir",
                str(tmp_path / "provider-extract"),
            ]
        )
        == 0
    )
    assert captured["provider"] == "exa"
    assert captured["urls"] == ["https://docs.example.com/a"]


def test_provider_context_pack_guards_validate_inputs(tmp_path: Path) -> None:
    kwargs = {
        "objective": "Build a pack",
        "queries": ["Parallel docs"],
        "providers": ["auto"],
        "output_dir": tmp_path,
        "include_domains": ["docs.parallel.ai"],
        "mode": "advanced",
        "max_search_results": 1,
        "extract_limit": 1,
        "max_estimated_cost": 0.05,
        "dry_run": True,
    }

    with pytest.raises(provider_cli.ProviderCliError, match="max_search_results"):
        provider_cli.run_provider_context_packs(**{**kwargs, "max_search_results": 0})
    with pytest.raises(provider_cli.ProviderCliError, match="extract_limit"):
        provider_cli.run_provider_context_packs(**{**kwargs, "extract_limit": 0})
    with pytest.raises(provider_cli.ProviderCliError, match="max_estimated_cost"):
        provider_cli.run_provider_context_packs(**{**kwargs, "max_estimated_cost": -1})
    with pytest.raises(provider_cli.ProviderCliError, match="Unsupported provider"):
        provider_cli._provider_name("unknown")
