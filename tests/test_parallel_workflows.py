"""Parallel context workflow tests."""

from __future__ import annotations

import builtins
import importlib.resources
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import docpull.parallel_workflows as parallel_workflows
from docpull.cli import main
from docpull.parallel_workflows import (
    ParallelWorkflowError,
    _require_parallel_sdk,
    estimate_context_pack_cost,
    run_parallel_cli,
)
from docpull.security.url_validator import UrlValidator

EXAMPLE_FIXTURE = Path(__file__).resolve().parents[1] / "docs/examples/parallel-search-extract.json"


@pytest.fixture(autouse=True)
def isolate_parallel_auth_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


@pytest.fixture(autouse=True)
def deterministic_parallel_url_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    def validator_factory(*args: object, **kwargs: object) -> UrlValidator:
        kwargs["resolver"] = lambda _hostname: ["93.184.216.34"]
        return UrlValidator(*args, **kwargs)

    monkeypatch.setattr(parallel_workflows, "UrlValidator", validator_factory)


def _write_fixture(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "objective": "Compare AI web-search APIs for agents",
                "queries": ["AI web search API", "agent web extraction API"],
                "mode": "advanced",
                "session_id": "session_fixture",
                "search": {
                    "search_id": "search_fixture",
                    "results": [
                        {
                            "url": "https://parallel.ai/",
                            "title": "Parallel",
                            "excerpts": ["Parallel builds web infrastructure for AI agents."],
                        }
                    ],
                },
                "extract": {
                    "extract_id": "extract_fixture",
                    "results": [
                        {
                            "url": "https://parallel.ai/",
                            "title": "Parallel",
                            "full_content": "# Parallel\n\nWeb infrastructure for AI agents.",
                            "excerpts": ["Web infrastructure for AI agents."],
                        }
                    ],
                    "errors": [
                        {
                            "url": "https://example.invalid/",
                            "error_type": "fetch_error",
                            "content": "Could not fetch",
                        }
                    ],
                },
                "task": {
                    "run_id": "task_fixture",
                    "content": "# Brief\n\nParallel provides agent web infrastructure.",
                },
                "usage": {"search": [{"type": "request", "quantity": 1}]},
            }
        ),
        encoding="utf-8",
    )


def test_parallel_import_writes_context_pack_artifacts(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    output_dir = tmp_path / "pack"
    _write_fixture(fixture)

    assert run_parallel_cli(["import", str(fixture), "--output-dir", str(output_dir)]) == 0

    ndjson = output_dir / "documents.ndjson"
    pack_path = output_dir / "parallel.pack.json"
    manifest_path = output_dir / "corpus.manifest.json"

    assert ndjson.exists()
    assert manifest_path.exists()
    assert (output_dir / "sources.md").exists()
    agent_context = output_dir / "AGENT_CONTEXT.md"
    assert agent_context.exists()
    assert (output_dir / "brief.md").read_text(encoding="utf-8").startswith("# Brief")
    assert list((output_dir / "sources").glob("*.md"))

    records = [json.loads(line) for line in ndjson.read_text(encoding="utf-8").splitlines()]
    assert records
    assert records[0]["url"] == "https://parallel.ai/"
    assert records[0]["metadata"]["session_id"] == "session_fixture"
    assert records[0]["source_type"] == "parallel_extract"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["output_format"] == "ndjson"
    assert manifest["record_count"] == len(records)

    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack["objective"] == "Compare AI web-search APIs for agents"
    assert pack["search_id"] == "search_fixture"
    assert pack["extract_id"] == "extract_fixture"
    assert pack["task_run_id"] == "task_fixture"
    assert pack["extract_error_count"] == 1
    assert pack["artifacts"]["documents_ndjson"] == "documents.ndjson"
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"
    agent_context_text = agent_context.read_text(encoding="utf-8")
    assert "## Load Plan" in agent_context_text
    assert "cited research synthesis" in agent_context_text
    assert "Extract error `fetch_error`: https://example.invalid/" in agent_context_text
    assert "PARALLEL_API_KEY" not in pack_path.read_text(encoding="utf-8")
    assert "secret" not in pack_path.read_text(encoding="utf-8").lower()


def test_parallel_import_checked_in_example_fixture(tmp_path: Path) -> None:
    output_dir = tmp_path / "example-pack"

    assert run_parallel_cli(["import", str(EXAMPLE_FIXTURE), "--output-dir", str(output_dir)]) == 0

    pack = json.loads((output_dir / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["session_id"] == "session_example_parallel_context_pack"
    assert pack["extract_result_count"] == 2
    assert pack["extract_error_count"] == 1
    assert pack["task_basis"][0]["citations"][0]["url"] == "https://parallel.ai/"
    assert pack["estimated_cost_usd"] == 0.013
    assert pack["request_options"]["source_policy"]["exclude_domains"] == ["onparallel.com"]
    assert (output_dir / "brief.md").exists()


def test_parallel_demo_uses_packaged_fixture(tmp_path: Path) -> None:
    output_dir = tmp_path / "demo-pack"

    assert run_parallel_cli(["demo", "--output-dir", str(output_dir)]) == 0

    pack = json.loads((output_dir / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["session_id"] == "session_example_parallel_context_pack"
    assert pack["extract_result_count"] == 2
    assert pack["artifacts"]["brief"] == "brief.md"
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"


def test_parallel_repo_fixture_matches_packaged_fixture() -> None:
    repo_fixture = json.loads(EXAMPLE_FIXTURE.read_text(encoding="utf-8"))
    packaged_fixture = json.loads(
        importlib.resources.files("docpull.fixtures")
        .joinpath("parallel-search-extract.json")
        .read_text(encoding="utf-8")
    )

    assert packaged_fixture == repo_fixture


def test_parallel_import_handles_symlinked_output_dir(tmp_path: Path) -> None:
    real_output = tmp_path / "real-pack"
    real_output.mkdir()
    linked_output = tmp_path / "linked-pack"
    linked_output.symlink_to(real_output, target_is_directory=True)

    assert run_parallel_cli(["import", str(EXAMPLE_FIXTURE), "--output-dir", str(linked_output)]) == 0

    pack = json.loads((linked_output / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["artifacts"]["corpus_manifest"] == "corpus.manifest.json"
    assert pack["sources"][0]["path"].startswith("sources/")


def test_parallel_import_invalid_fixture_returns_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = tmp_path / "bad.json"
    fixture.write_text('{"queries": []}', encoding="utf-8")

    assert run_parallel_cli(["import", str(fixture), "--output-dir", str(tmp_path / "pack")]) == 1

    captured = capsys.readouterr()
    assert "objective" in captured.out


def test_parallel_import_all_extract_failures_preserves_pack_errors(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = tmp_path / "failed.json"
    output_dir = tmp_path / "pack"
    fixture.write_text(
        json.dumps(
            {
                "objective": "Find current product docs",
                "queries": ["product docs"],
                "extract": {
                    "errors": [
                        {
                            "url": "https://example.com/",
                            "error_type": "fetch_error",
                            "content": "failed",
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    assert run_parallel_cli(["import", str(fixture), "--output-dir", str(output_dir)]) == 1

    captured = capsys.readouterr()
    assert "no successful results" in captured.out
    pack = json.loads((output_dir / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["extract_error_count"] == 1
    assert pack["errors"][0]["url"] == "https://example.com/"


def test_parallel_context_pack_requires_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)

    result = run_parallel_cli(
        ["context-pack", "Research AI web search APIs", "--output-dir", str(tmp_path / "pack")]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert "PARALLEL_API_KEY" in captured.out


def test_parallel_auth_reports_missing_key(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)

    assert run_parallel_cli(["auth"]) == 1

    output = capsys.readouterr().out
    normalized = " ".join(output.split())
    assert "Parallel local auth preflight" in output
    assert "PARALLEL_API_KEY: missing" in output
    assert "no live key validation call" in output
    assert 'export PARALLEL_API_KEY="<your-parallel-api-key>"' in normalized


def test_parallel_auth_json_reports_ready_without_exposing_key(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-secret-value")
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)

    assert run_parallel_cli(["auth", "--json"]) == 0

    output = capsys.readouterr().out
    assert "test-secret-value" not in output
    payload = json.loads(output)
    assert payload["ready"] is True
    assert payload["api_key_present"] is True
    assert payload["api_key_env_var"] == "PARALLEL_API_KEY"
    assert "PARALLEL_API_KEY" in payload["key_handling"]
    assert "no live key validation call" in payload["validation"]
    assert payload["api_key_source"] == "env"
    assert payload["api_key_source_path"] is None


def test_parallel_auth_human_reports_ready_without_exposing_key(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-secret-value")
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)

    assert run_parallel_cli(["auth"]) == 0

    output = capsys.readouterr().out
    assert "Local configuration is present for live Parallel workflows." in output
    assert "test-secret-value" not in output
    assert "PARALLEL_API_KEY: detected" in output
    assert "Key source: env" in output


def test_parallel_auth_treats_blank_key_as_missing(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "   ")
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)

    assert run_parallel_cli(["auth", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is False
    assert payload["api_key_present"] is False
    assert payload["api_key_source"] == "missing"


def test_parallel_auth_reports_invalid_key_without_exposing_value(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-secret\nheader-injection")
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)

    assert run_parallel_cli(["auth", "--json"]) == 1

    output = capsys.readouterr().out
    assert "test-secret" not in output
    assert "header-injection" not in output
    payload = json.loads(output)
    assert payload["ready"] is False
    assert payload["api_key_present"] is False
    assert payload["api_key_source"] == "invalid_env"
    assert "control characters" in payload["api_key_invalid_reason"]


def test_parallel_init_writes_user_secret_and_auth_uses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("test-secret-value\n"))

    assert run_parallel_cli(["init", "--from-stdin"]) == 0

    output = capsys.readouterr().out
    assert "test-secret-value" not in output
    secret_path = tmp_path / "xdg-config" / "docpull" / "secrets.env"
    assert secret_path.exists()
    assert secret_path.stat().st_mode & 0o777 == 0o600
    assert secret_path.parent.stat().st_mode & 0o777 == 0o700
    assert "test-secret-value" in secret_path.read_text(encoding="utf-8")

    assert run_parallel_cli(["auth", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ready"] is True
    assert payload["api_key_source"] == "user_config"
    assert payload["api_key_source_path"] == str(secret_path)


def test_parallel_init_rejects_unsafe_key_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("bad-secret\x1fvalue\n"))

    assert run_parallel_cli(["init", "--from-stdin"]) == 1

    output = capsys.readouterr().out
    assert "bad-secret" not in output
    assert "control characters" in output
    assert not (tmp_path / "xdg-config" / "docpull" / "secrets.env").exists()


def test_parallel_init_project_writes_env_local_and_gitignore(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)
    monkeypatch.setattr("sys.stdin", io.StringIO("project-secret-value\n"))

    assert run_parallel_cli(["init", "--project", "--from-stdin"]) == 0

    output = capsys.readouterr().out
    assert "project-secret-value" not in output
    env_path = tmp_path / ".env.local"
    assert env_path.exists()
    assert env_path.stat().st_mode & 0o777 == 0o600
    assert "project-secret-value" in env_path.read_text(encoding="utf-8")
    assert ".env.local" in (tmp_path / ".gitignore").read_text(encoding="utf-8")

    assert run_parallel_cli(["auth", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["api_key_source"] == "project_env"
    assert payload["api_key_source_path"] == str(env_path)


def test_parallel_auth_env_overrides_project_and_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    user_secret = tmp_path / "xdg-config" / "docpull" / "secrets.env"
    user_secret.parent.mkdir(parents=True)
    user_secret.write_text('PARALLEL_API_KEY="user-secret"\n', encoding="utf-8")
    (tmp_path / ".env.local").write_text('PARALLEL_API_KEY="project-secret"\n', encoding="utf-8")
    monkeypatch.setenv("PARALLEL_API_KEY", "env-secret")
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)

    assert run_parallel_cli(["auth", "--json"]) == 0

    output = capsys.readouterr().out
    assert "env-secret" not in output
    assert "project-secret" not in output
    assert "user-secret" not in output
    payload = json.loads(output)
    assert payload["api_key_source"] == "env"
    assert payload["api_key_source_path"] is None


def test_parallel_auth_json_can_redact_local_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: True)
    secret_path = tmp_path / "xdg-config" / "docpull" / "secrets.env"
    secret_path.parent.mkdir(parents=True)
    secret_path.write_text('PARALLEL_API_KEY="user-secret"\n', encoding="utf-8")

    assert run_parallel_cli(["auth", "--json", "--redact-paths"]) == 0

    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    payload = json.loads(output)
    assert payload["paths_redacted"] is True
    assert payload["api_key_source_path"] == "[redacted]"
    assert payload["user_secrets_path"] == "[redacted]"
    assert payload["project_env_path"] == "[redacted]"


def test_parallel_init_refuses_overwrite_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("first-secret\n"))
    assert run_parallel_cli(["init", "--from-stdin"]) == 0
    capsys.readouterr()

    monkeypatch.setattr("sys.stdin", io.StringIO("second-secret\n"))
    assert run_parallel_cli(["init", "--from-stdin"]) == 1
    output = capsys.readouterr().out
    assert "second-secret" not in output
    assert "--force" in output
    secret_path = tmp_path / "xdg-config" / "docpull" / "secrets.env"
    assert "first-secret" in secret_path.read_text(encoding="utf-8")

    monkeypatch.setattr("sys.stdin", io.StringIO("second-secret\n"))
    assert run_parallel_cli(["init", "--from-stdin", "--force"]) == 0
    assert "second-secret" in secret_path.read_text(encoding="utf-8")


def test_parallel_auth_json_reports_missing_sdk(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "test-secret-value")
    monkeypatch.setattr(parallel_workflows, "_parallel_sdk_installed", lambda: False)

    assert run_parallel_cli(["auth", "--json"]) == 1

    output = capsys.readouterr().out
    assert "test-secret-value" not in output
    payload = json.loads(output)
    assert payload["ready"] is False
    assert payload["sdk_installed"] is False
    assert "pip install 'docpull[parallel]'" in payload["next_steps"][0]


def test_parallel_context_pack_rejects_blank_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PARALLEL_API_KEY", "   ")

    result = run_parallel_cli(
        ["context-pack", "Research AI web search APIs", "--output-dir", str(tmp_path / "pack")]
    )

    assert result == 1
    assert "PARALLEL_API_KEY" in capsys.readouterr().out


def test_parallel_context_pack_live_uses_text_task_spec_and_basis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_task: dict[str, object] = {}

    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeTaskRun:
        def create(self, **kwargs: object) -> Response:
            created_task.update(kwargs)
            return Response(run_id="task_live")

        def result(self, run_id: str, *, api_timeout: int) -> Response:
            assert run_id == "task_live"
            assert api_timeout == 3600
            return Response(
                output=Response(
                    content="# Brief\n\nLive Parallel brief.",
                    basis=[{"field": "content", "citations": [{"url": "https://parallel.ai/"}]}],
                ),
                usage=[{"type": "task", "quantity": 1}],
            )

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.task_run = FakeTaskRun()

        def search(self, **kwargs: object) -> Response:
            assert kwargs["mode"] == "advanced"
            return Response(
                search_id="search_live",
                session_id="session_live",
                results=[Response(url="https://parallel.ai/", title="Parallel", excerpts=["Search excerpt"])],
                usage=[{"type": "search", "quantity": 1}],
            )

        def extract(self, **kwargs: object) -> Response:
            assert kwargs["session_id"] == "session_live"
            return Response(
                extract_id="extract_live",
                session_id="session_live",
                results=[
                    Response(
                        url="https://parallel.ai/",
                        title="Parallel",
                        full_content="# Parallel\n\nExtracted content.",
                    )
                ],
                errors=[],
                usage=[{"type": "extract", "quantity": 1}],
            )

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "context-pack",
                "Build a Parallel context pack",
                "--query",
                "Parallel Search API",
                "--output-dir",
                str(tmp_path / "pack"),
                "--task-brief",
            ]
        )
        == 0
    )

    assert created_task["processor"] == "base"
    assert created_task["task_spec"]["output_schema"]["type"] == "text"  # type: ignore[index]
    pack = json.loads((tmp_path / "pack" / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["task_basis"][0]["citations"][0]["url"] == "https://parallel.ai/"


def test_parallel_context_pack_live_passes_controls_and_cost_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def search(self, **kwargs: object) -> Response:
            assert kwargs["mode"] == "turbo"
            assert kwargs["client_model"] == "gpt-5.4"
            assert kwargs["max_chars_total"] == 1200
            assert kwargs["advanced_settings"] == {
                "source_policy": {
                    "include_domains": ["parallel.ai", "docs.parallel.ai"],
                    "exclude_domains": ["onparallel.com"],
                    "after_date": "2026-01-01",
                },
                "fetch_policy": {
                    "max_age_seconds": 600,
                    "timeout_seconds": 30,
                    "disable_cache_fallback": True,
                },
                "excerpt_settings": {"max_chars_per_result": 5000},
                "location": "us",
                "max_results": 4,
            }
            return Response(
                search_id="search_live",
                session_id="session_live",
                results=[Response(url="https://parallel.ai/", title="Parallel", excerpts=["Search excerpt"])],
                warnings=[{"type": "notice", "message": "example"}],
                usage=[{"name": "sku_search", "count": 1}],
            )

        def extract(self, **kwargs: object) -> Response:
            assert kwargs["client_model"] == "gpt-5.4"
            assert kwargs["max_chars_total"] == 800
            assert kwargs["advanced_settings"] == {
                "fetch_policy": {
                    "max_age_seconds": 600,
                    "timeout_seconds": 30,
                    "disable_cache_fallback": True,
                },
                "excerpt_settings": {"max_chars_per_result": 5000},
                "full_content": False,
            }
            return Response(
                extract_id="extract_live",
                session_id="session_live",
                results=[
                    Response(
                        url="https://parallel.ai/",
                        title="Parallel",
                        excerpts=["Extracted excerpt."],
                    )
                ],
                errors=[],
                warnings=[],
                usage=[{"name": "sku_extract_excerpts", "count": 1}],
            )

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "context-pack",
                "Build a Parallel context pack",
                "--query",
                "Parallel Search API",
                "--output-dir",
                str(tmp_path / "pack"),
                "--mode",
                "turbo",
                "--extract-limit",
                "1",
                "--include-domain",
                "parallel.ai",
                "--include-domain",
                "docs.parallel.ai",
                "--exclude-domain",
                "onparallel.com",
                "--after-date",
                "2026-01-01",
                "--max-search-results",
                "4",
                "--max-search-chars-total",
                "1200",
                "--max-extract-chars-total",
                "800",
                "--no-full-content",
                "--fetch-max-age-seconds",
                "600",
                "--fetch-timeout-seconds",
                "30",
                "--disable-cache-fallback",
                "--excerpt-chars-per-result",
                "5000",
                "--location",
                "us",
                "--client-model",
                "gpt-5.4",
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "pack" / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["mode"] == "turbo"
    assert pack["estimated_cost_usd"] == 0.006
    assert pack["request_options"]["source_policy"]["exclude_domains"] == ["onparallel.com"]
    assert pack["request_options"]["full_content"] is False
    assert pack["request_options"]["fetch_policy"]["max_age_seconds"] == 600
    assert pack["request_options"]["excerpt_chars_per_result"] == 5000
    assert pack["request_options"]["location"] == "us"
    assert pack["warnings"]["search"][0]["type"] == "notice"


def test_parallel_context_pack_cost_guard_blocks_before_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)

    result = run_parallel_cli(
        [
            "context-pack",
            "Expensive research",
            "--query",
            "expensive research",
            "--output-dir",
            str(tmp_path / "pack"),
            "--task-brief",
            "--task-processor",
            "ultra",
            "--max-estimated-cost",
            "0.05",
        ]
    )

    assert result == 1
    captured = capsys.readouterr()
    assert "Estimated Parallel cost" in captured.out
    assert "PARALLEL_API_KEY" not in captured.out


def test_parallel_context_pack_dry_run_needs_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)

    assert (
        run_parallel_cli(
            [
                "context-pack",
                "Plan a Parallel pack",
                "--query",
                "Parallel docs",
                "--include-domain",
                "parallel.ai",
                "--extract-limit",
                "2",
                "--dry-run",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["estimated_cost_usd"] == estimate_context_pack_cost(extract_limit=2)
    assert payload["request_options"]["source_policy"]["include_domains"] == ["parallel.ai"]


def test_parallel_context_pack_rejects_unknown_task_processor_before_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)

    assert (
        run_parallel_cli(
            [
                "context-pack",
                "Plan a Parallel pack",
                "--query",
                "Parallel docs",
                "--task-brief",
                "--task-processor",
                "definitely-not-real",
                "--dry-run",
            ]
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "Unsupported Parallel Task processor" in output
    assert "PARALLEL_API_KEY" not in output


def test_parallel_search_pack_writes_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def search(self, **kwargs: object) -> Response:
            assert kwargs["advanced_settings"]["source_policy"]["include_domains"] == ["docs.parallel.ai"]
            return Response(
                search_id="search_123",
                session_id="session_123",
                results=[
                    Response(
                        url="https://docs.parallel.ai/api-reference/search/search",
                        title="Search",
                        excerpts=["Search API excerpt."],
                    )
                ],
                usage=[{"name": "sku_search", "count": 1}],
            )

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "search-pack",
                "Parallel Search docs",
                "--query",
                "Parallel Search API",
                "--include-domain",
                "docs.parallel.ai",
                "--output-dir",
                str(tmp_path / "search"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "search" / "search.pack.json").read_text(encoding="utf-8"))
    assert pack["workflow"] == "search-pack"
    assert pack["metadata"]["search_id"] == "search_123"
    assert pack["item_count"] == 1
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"
    agent_context = (tmp_path / "search" / "AGENT_CONTEXT.md").read_text(encoding="utf-8")
    assert "Workflow: `search-pack`" in agent_context
    assert "Items: 1" in agent_context
    assert "## Source Scores" in agent_context


def test_parallel_discover_docs_writes_ranked_crawl_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def search(self, **kwargs: object) -> Response:
            assert kwargs["advanced_settings"]["source_policy"]["include_domains"] == ["docs.parallel.ai"]
            return Response(
                search_id="search_456",
                session_id="session_456",
                results=[
                    Response(
                        url="https://parallel.ai/blog/search",
                        title="Search launch blog",
                        excerpts=["Blog excerpt."],
                    ),
                    Response(
                        url="https://docs.parallel.ai/api-reference/search/search",
                        title="Search API Reference",
                        excerpts=["Docs excerpt."],
                    ),
                ],
                usage=[{"name": "sku_search", "count": 1}],
            )

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "discover-docs",
                "Find Parallel Search docs",
                "--query",
                "Parallel Search API docs",
                "--include-domain",
                "docs.parallel.ai",
                "--output-dir",
                str(tmp_path / "discovery"),
            ]
        )
        == 0
    )

    discovered = json.loads((tmp_path / "discovery" / "discovered_urls.json").read_text(encoding="utf-8"))
    assert discovered["sources"][0]["url"] == "https://docs.parallel.ai/api-reference/search/search"
    assert discovered["sources"][0]["source_score"]["grade"] == "primary"
    assert (
        "docpull https://docs.parallel.ai/api-reference/search/search"
        in discovered["sources"][0]["next_command"]
    )
    assert (tmp_path / "discovery" / "NEXT_STEPS.md").exists()
    pack = json.loads((tmp_path / "discovery" / "discovery.pack.json").read_text(encoding="utf-8"))
    assert pack["workflow"] == "discover-docs"
    assert pack["artifacts"]["discovered_urls"] == "discovered_urls.json"


def test_parallel_extract_pack_writes_extract_context_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def extract(self, **kwargs: object) -> Response:
            assert kwargs["urls"] == ["https://docs.parallel.ai/api-reference/search/search"]
            return Response(
                extract_id="extract_123",
                session_id="session_123",
                results=[
                    Response(
                        url="https://docs.parallel.ai/api-reference/search/search",
                        title="Search",
                        full_content="# Search\n\nExtracted content.",
                        excerpts=[],
                    )
                ],
                errors=[],
                usage=[{"name": "sku_extract", "count": 1}],
            )

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "extract-pack",
                "https://docs.parallel.ai/api-reference/search/search",
                "--output-dir",
                str(tmp_path / "extract"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "extract" / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["workflow"] == "extract-pack"
    assert pack["extract_id"] == "extract_123"
    assert pack["record_count"] == 1
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"


def test_parallel_fallback_pack_uses_core_then_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def extract(self, **kwargs: object) -> Response:
            assert kwargs["urls"] == ["https://docs.parallel.ai/fallback"]
            return Response(
                extract_id="extract_fallback",
                session_id="session_fallback",
                results=[
                    Response(
                        url="https://docs.parallel.ai/fallback",
                        title="Fallback",
                        full_content="# Fallback\n\nParallel content.",
                    )
                ],
                errors=[],
                warnings=[],
                usage=[{"name": "sku_extract", "count": 1}],
            )

    def fake_core(url: str, *, profile: str, max_core_chars: int) -> dict[str, object]:
        assert profile == "rag"
        assert max_core_chars == 50000
        if url.endswith("/fallback"):
            raise RuntimeError("core failed")
        return {
            "url": url,
            "title": "Core",
            "full_content": "# Core\n\nCore content.",
            "provider": "docpull_core",
            "fallback_used": False,
        }

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)
    monkeypatch.setattr(parallel_workflows, "_core_docpull_extract_result", fake_core)

    assert (
        run_parallel_cli(
            [
                "fallback-pack",
                "https://docs.parallel.ai/core",
                "https://docs.parallel.ai/fallback",
                "--output-dir",
                str(tmp_path / "fallback"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "fallback" / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["workflow"] == "fallback-pack"
    assert pack["extract_id"] == "extract_fallback"
    assert pack["request_options"]["core_success_count"] == 1
    assert pack["request_options"]["fallback_url_count"] == 1
    assert pack["warnings"]["core_fetch_failures"][0]["url"] == "https://docs.parallel.ai/fallback"
    records = [
        json.loads(line)
        for line in (tmp_path / "fallback" / "documents.ndjson").read_text(encoding="utf-8").splitlines()
    ]
    assert {record["metadata"]["provider"] for record in records} == {
        "docpull_core",
        "parallel_extract",
    }


def test_parallel_fallback_core_extract_uses_temp_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_fetch_one(url: str, **kwargs: object) -> SimpleNamespace:
        captured["url"] = url
        captured["kwargs"] = kwargs
        output = kwargs["output"]
        assert isinstance(output, dict)
        output_dir = output["directory"]
        assert isinstance(output_dir, Path)
        assert output_dir.is_absolute()
        assert not str(output_dir).startswith(str(tmp_path / "docs"))
        return SimpleNamespace(
            error=None,
            should_skip=False,
            markdown="# Search\n\nCore content.",
            title="Search",
            metadata={},
            status_code=200,
            content_type="text/html",
            extraction_info={},
        )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("docpull.core.fetcher.fetch_one", fake_fetch_one)

    result = parallel_workflows._core_docpull_extract_result(
        "https://docs.parallel.ai/api-reference/search/search",
        profile="rag",
        max_core_chars=50000,
    )

    assert result["provider"] == "docpull_core"
    assert captured["url"] == "https://docs.parallel.ai/api-reference/search/search"
    assert not (tmp_path / "docs").exists()


def test_parallel_task_pack_passes_lifecycle_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}

    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeTaskRun:
        def create(self, **kwargs: object) -> Response:
            created.update(kwargs)
            return Response(run_id="run_123")

        def result(self, run_id: str, *, api_timeout: int) -> Response:
            assert run_id == "run_123"
            assert api_timeout == 3600
            return Response(output=Response(content="Task output."), usage=[{"name": "sku_task", "count": 1}])

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.task_run = FakeTaskRun()

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"summary": {"type": "string"}}}))
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "task-pack",
                "Research Parallel",
                "--output-schema",
                str(schema),
                "--source-include-domain",
                "docs.parallel.ai",
                "--location",
                "us",
                "--previous-interaction-id",
                "interaction_1",
                "--enable-events",
                "--metadata",
                "source=docpull",
                "--output-dir",
                str(tmp_path / "task"),
            ]
        )
        == 0
    )

    assert created["previous_interaction_id"] == "interaction_1"
    assert created["advanced_settings"] == {"location": "us"}
    assert created["source_policy"] == {"include_domains": ["docs.parallel.ai"]}
    assert created["enable_events"] is True
    pack = json.loads((tmp_path / "task" / "task.pack.json").read_text(encoding="utf-8"))
    assert pack["metadata"]["run_id"] == "run_123"


def test_parallel_diff_brief_writes_task_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def write_pack(pack_dir: Path, url: str, content_hash: str) -> None:
        pack_dir.mkdir()
        (pack_dir / "documents.ndjson").write_text(
            json.dumps(
                {
                    "document_id": f"doc_{content_hash}",
                    "url": url,
                    "title": url,
                    "content": "content",
                    "content_hash": content_hash,
                    "source_type": "parallel_extract",
                }
            )
            + "\n",
            encoding="utf-8",
        )

    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeTaskRun:
        def create(self, **kwargs: object) -> Response:
            assert kwargs["processor"] == "base"
            assert "added" in kwargs["input"].lower()
            return Response(run_id="run_diff")

        def result(self, run_id: str, *, api_timeout: int) -> Response:
            assert run_id == "run_diff"
            assert api_timeout == 3600
            return Response(output=Response(content="# Changes\n\nReload the added docs."))

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.task_run = FakeTaskRun()

    old_pack = tmp_path / "old"
    new_pack = tmp_path / "new"
    write_pack(old_pack, "https://docs.parallel.ai/old", "aaa")
    write_pack(new_pack, "https://docs.parallel.ai/new", "bbb")
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "diff-brief",
                str(old_pack),
                str(new_pack),
                "--output-dir",
                str(tmp_path / "diff"),
            ]
        )
        == 0
    )

    assert "# Changes" in (tmp_path / "diff" / "CHANGE_SUMMARY.md").read_text(encoding="utf-8")
    diff_payload = json.loads((tmp_path / "diff" / "pack.diff.json").read_text(encoding="utf-8"))
    assert diff_payload["added_urls"] == ["https://docs.parallel.ai/new"]
    pack = json.loads((tmp_path / "diff" / "diff.brief.pack.json").read_text(encoding="utf-8"))
    assert pack["workflow"] == "diff-brief"
    assert pack["artifacts"]["change_summary"] == "CHANGE_SUMMARY.md"


def test_parallel_context_pack_rejects_extract_limit_above_parallel_cap() -> None:
    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--extract-limit", "21", "--dry-run"])


def test_parallel_run_recipe_dry_run_needs_no_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    recipe = tmp_path / "parallel-pack.yaml"
    recipe.write_text(
        """
workflow: context-pack
objective: Build a Parallel docs pack
queries:
  - Parallel Search API docs
include_domains:
  - parallel.ai
exclude_domains:
  - onparallel.com
extract_limit: 2
max_estimated_cost: 0.02
""".strip(),
        encoding="utf-8",
    )

    assert run_parallel_cli(["run", str(recipe), "--dry-run"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "context-pack"
    assert payload["request_options"]["source_policy"]["include_domains"] == ["parallel.ai"]
    assert payload["estimated_cost_usd"] == estimate_context_pack_cost(extract_limit=2)


def test_parallel_run_recipe_rejects_invalid_extract_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    recipe = tmp_path / "parallel-pack.yaml"
    recipe.write_text(
        """
workflow: context-pack
objective: Invalid extract limit
queries:
  - Parallel Search API docs
extract_limit: -5
dry_run: true
""".strip(),
        encoding="utf-8",
    )

    assert run_parallel_cli(["run", str(recipe)]) == 1

    captured = capsys.readouterr()
    assert "extract_limit" in captured.out
    assert "at least 1" in captured.out


def test_parallel_run_recipe_rejects_extract_limit_above_parallel_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    recipe = tmp_path / "parallel-pack.yaml"
    recipe.write_text(
        """
workflow: context-pack
objective: Invalid extract limit
queries:
  - Parallel Search API docs
extract_limit: 21
dry_run: true
""".strip(),
        encoding="utf-8",
    )

    assert run_parallel_cli(["run", str(recipe)]) == 1

    captured = capsys.readouterr()
    assert "extract_limit" in captured.out
    assert "at most 20" in captured.out


def test_parallel_run_recipe_supports_taskgroup_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    inputs = tmp_path / "companies.json"
    inputs.write_text(json.dumps([{"company": "Parallel"}]), encoding="utf-8")
    recipe = tmp_path / "parallel-taskgroup.yaml"
    recipe.write_text(
        """
workflow: taskgroup-pack
inputs: ./companies.json
prompt_template: Research {company}
dry_run: true
""".strip(),
        encoding="utf-8",
    )

    assert run_parallel_cli(["run", str(recipe)]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["workflow"] == "taskgroup-pack"
    assert payload["input_count"] == 1


def test_parallel_run_recipe_dry_run_supports_lifecycle_workflows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    schema = json.dumps({"type": "object", "properties": {"summary": {"type": "string"}}})
    recipes: dict[str, dict[str, object]] = {
        "task-result": {"workflow": "task-result", "run_id": "run_123"},
        "task-events": {"workflow": "task-events", "run_id": "run_123", "limit": 5},
        "api-pack": {"workflow": "api-pack", "source": "https://docs.parallel.ai/llms.txt", "kind": "llms"},
        "findall-ingest-pack": {"workflow": "findall-ingest-pack", "objective": "Find API docs"},
        "findall-result-pack": {"workflow": "findall-result-pack", "findall_id": "findall_123"},
        "findall-schema-pack": {"workflow": "findall-schema-pack", "findall_id": "findall_123"},
        "findall-enrich-pack": {
            "workflow": "findall-enrich-pack",
            "findall_id": "findall_123",
            "output_schema_json": schema,
        },
        "findall-extend-pack": {
            "workflow": "findall-extend-pack",
            "findall_id": "findall_123",
            "additional_match_limit": 2,
        },
        "findall-cancel-pack": {"workflow": "findall-cancel-pack", "findall_id": "findall_123"},
        "findall-events-pack": {"workflow": "findall-events-pack", "findall_id": "findall_123", "limit": 5},
    }

    for workflow, recipe_payload in recipes.items():
        recipe = tmp_path / f"{workflow}.json"
        recipe.write_text(json.dumps(recipe_payload), encoding="utf-8")

        assert run_parallel_cli(["run", str(recipe), "--dry-run"]) == 0

        payload = json.loads(capsys.readouterr().out)
        assert payload["workflow"] == workflow


def test_parallel_run_recipe_invokes_context_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"

        def search(self, **kwargs: object) -> Response:
            assert kwargs["advanced_settings"]["source_policy"]["include_domains"] == ["parallel.ai"]
            return Response(
                search_id="search_recipe",
                session_id="session_recipe",
                results=[Response(url="https://parallel.ai/", title="Parallel", excerpts=["Search excerpt"])],
                usage=[{"name": "sku_search", "count": 1}],
            )

        def extract(self, **kwargs: object) -> Response:
            assert kwargs["advanced_settings"] == {"full_content": False}
            return Response(
                extract_id="extract_recipe",
                session_id="session_recipe",
                results=[
                    Response(url="https://parallel.ai/", title="Parallel", excerpts=["Extract excerpt"])
                ],
                errors=[],
                usage=[{"name": "sku_extract_excerpts", "count": 1}],
            )

    recipe = tmp_path / "parallel-pack.yaml"
    recipe.write_text(
        """
workflow: context-pack
objective: Build a Parallel docs pack
queries:
  - Parallel Search API docs
include_domains:
  - parallel.ai
extract_limit: 1
no_full_content: true
output_dir: pack
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert run_parallel_cli(["run", str(recipe), "--output-dir", str(tmp_path / "pack")]) == 0

    pack = json.loads((tmp_path / "pack" / "parallel.pack.json").read_text(encoding="utf-8"))
    assert pack["search_id"] == "search_recipe"
    assert pack["request_options"]["full_content"] is False


def test_parallel_entity_pack_writes_candidate_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeFindAll:
        def entity_search(self, **kwargs: object) -> Response:
            assert kwargs["entity_type"] == "companies"
            assert kwargs["match_limit"] == 2
            return Response(
                entity_set_id="entities_123",
                entities=[
                    Response(
                        name="Parallel Web Systems",
                        url="https://parallel.ai/",
                        description="Web intelligence APIs.",
                    )
                ],
                usage=[{"name": "sku_entity_search", "count": 1}],
            )

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.beta = Response(findall=FakeFindAll())

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "entity-pack",
                "AI web infrastructure companies",
                "--match-limit",
                "2",
                "--output-dir",
                str(tmp_path / "entities"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "entities" / "entity.pack.json").read_text(encoding="utf-8"))
    assert pack["item_count"] == 1
    assert pack["metadata"]["entity_set_id"] == "entities_123"
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"
    assert (tmp_path / "entities" / "documents.ndjson").exists()


def test_parallel_findall_pack_dry_run_uses_price_guard(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)

    assert (
        run_parallel_cli(
            [
                "findall-pack",
                "Find AI developer tool startups",
                "--condition",
                "ai_tool=Company must sell AI developer tooling.",
                "--match-limit",
                "5",
                "--dry-run",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["estimated_cost_usd"] == 0.1
    assert payload["match_conditions"][0]["name"] == "ai_tool"


def test_parallel_findall_pack_waits_and_writes_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeFindAll:
        def create(self, **kwargs: object) -> Response:
            assert kwargs["generator"] == "preview"
            return Response(findall_id="findall_123", status=Response(is_active=False))

        def retrieve(self, findall_id: str) -> Response:
            assert findall_id == "findall_123"
            return Response(status=Response(is_active=False))

        def result(self, findall_id: str) -> Response:
            assert findall_id == "findall_123"
            return Response(
                candidates=[
                    Response(
                        name="Parallel",
                        url="https://parallel.ai/",
                        description="A matching company.",
                    )
                ],
                usage=[{"name": "sku_findall", "count": 1}],
            )

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.beta = Response(findall=FakeFindAll())

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "findall-pack",
                "Find AI developer tool startups",
                "--condition",
                "Company must sell AI developer tooling.",
                "--wait",
                "--output-dir",
                str(tmp_path / "findall"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "findall" / "findall.pack.json").read_text(encoding="utf-8"))
    assert pack["metadata"]["findall_id"] == "findall_123"
    assert pack["item_count"] == 1


def test_parallel_findall_pack_wait_requires_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeFindAll:
        def create(self, **kwargs: object) -> Response:
            assert kwargs["generator"] == "preview"
            return Response(status=Response(is_active=False))

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.beta = Response(findall=FakeFindAll())

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "findall-pack",
                "Find AI developer tool startups",
                "--wait",
                "--output-dir",
                str(tmp_path / "findall"),
            ]
        )
        == 1
    )

    assert "findall_id" in capsys.readouterr().out


def test_parallel_findall_lifecycle_packs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeFindAll:
        def ingest(self, **kwargs: object) -> Response:
            assert kwargs["objective"] == "Find AI companies"
            return Response(objective="Find AI companies", entity_type="companies")

        def schema(self, findall_id: str) -> Response:
            assert findall_id == "findall_123"
            return Response(objective="Find AI companies", entity_type="companies")

        def enrich(self, **kwargs: object) -> Response:
            assert kwargs["findall_id"] == "findall_123"
            assert kwargs["output_schema"]["type"] == "json"
            return Response(findall_id="findall_123", enrichments=[kwargs["output_schema"]])

        def extend(self, **kwargs: object) -> Response:
            assert kwargs["additional_match_limit"] == 5
            return Response(findall_id="findall_123", match_limit=10)

        def cancel(self, **kwargs: object) -> Response:
            assert kwargs["findall_id"] == "findall_123"
            return Response(findall_id="findall_123", status="cancelled")

        def result(self, findall_id: str) -> Response:
            assert findall_id == "findall_123"
            return Response(candidates=[Response(name="Parallel", url="https://parallel.ai/")])

        def events(self, **kwargs: object) -> list[Response]:
            assert kwargs["findall_id"] == "findall_123"
            return [Response(event_id="event_1", type="findall.candidate.generated")]

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.beta = Response(findall=FakeFindAll())

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"ceo": {"type": "string"}}}))
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            ["findall-ingest-pack", "Find AI companies", "--output-dir", str(tmp_path / "ingest")]
        )
        == 0
    )
    assert (
        run_parallel_cli(["findall-schema-pack", "findall_123", "--output-dir", str(tmp_path / "schema")])
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "findall-enrich-pack",
                "findall_123",
                "--output-schema",
                str(schema),
                "--output-dir",
                str(tmp_path / "enrich"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "findall-extend-pack",
                "findall_123",
                "--additional-match-limit",
                "5",
                "--output-dir",
                str(tmp_path / "extend"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(["findall-cancel-pack", "findall_123", "--output-dir", str(tmp_path / "cancel")])
        == 0
    )
    assert (
        run_parallel_cli(["findall-result-pack", "findall_123", "--output-dir", str(tmp_path / "result")])
        == 0
    )
    assert (
        run_parallel_cli(["findall-events-pack", "findall_123", "--output-dir", str(tmp_path / "events")])
        == 0
    )

    enrich_pack = json.loads((tmp_path / "enrich" / "findall.enrich.pack.json").read_text(encoding="utf-8"))
    assert enrich_pack["metadata"]["findall_id"] == "findall_123"
    events_pack = json.loads((tmp_path / "events" / "findall.events.pack.json").read_text(encoding="utf-8"))
    assert events_pack["item_count"] == 1


def test_parallel_taskgroup_pack_dry_run_from_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([{"company": "Parallel"}, {"company": "Cursor"}]), encoding="utf-8")

    assert (
        run_parallel_cli(
            [
                "taskgroup-pack",
                str(inputs),
                "--prompt-template",
                "Research {company}",
                "--processor",
                "lite",
                "--dry-run",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["input_count"] == 2
    assert payload["estimated_cost_usd"] == 0.01


def test_parallel_taskgroup_pack_rejects_unknown_processor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    inputs = tmp_path / "inputs.ndjson"
    inputs.write_text('{"company":"Parallel"}\n', encoding="utf-8")

    assert (
        run_parallel_cli(
            [
                "taskgroup-pack",
                str(inputs),
                "--processor",
                "definitely-not-real",
                "--dry-run",
            ]
        )
        == 1
    )

    assert "Unsupported Parallel Task processor" in capsys.readouterr().out


def test_parallel_taskgroup_pack_waits_for_inactive_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeTaskGroup:
        def __init__(self) -> None:
            self.created_inputs: list[object] = []

        def create(self, **kwargs: object) -> Response:
            assert kwargs["metadata"] == {"source": "docpull"}
            return Response(task_group_id="taskgroup_123")

        def add_runs(self, task_group_id: str, **kwargs: object) -> Response:
            assert task_group_id == "taskgroup_123"
            self.created_inputs = list(kwargs["inputs"])  # type: ignore[arg-type]
            assert kwargs["refresh_status"] is False
            assert kwargs["default_task_spec"]["output_schema"]["type"] == "text"  # type: ignore[index]
            return Response(run_ids=["run_1"], status=Response(is_active=True), run_cursor="run_cursor_1")

        def retrieve(self, task_group_id: str) -> Response:
            assert task_group_id == "taskgroup_123"
            return Response(status=Response(is_active=False, task_run_status_counts={"completed": 1}))

        def get_runs(self, task_group_id: str, **kwargs: object) -> list[Response]:
            assert task_group_id == "taskgroup_123"
            assert kwargs["include_input"] is True
            assert kwargs["include_output"] is True
            return [Response(run_id="run_1", output=Response(content="Research output."))]

    fake_task_group = FakeTaskGroup()

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.task_group = fake_task_group

    inputs = tmp_path / "inputs.ndjson"
    inputs.write_text('{"company":"Parallel"}\n', encoding="utf-8")
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "taskgroup-pack",
                str(inputs),
                "--prompt-template",
                "Research {company}",
                "--processor",
                "lite",
                "--wait",
                "--output-dir",
                str(tmp_path / "taskgroup"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "taskgroup" / "taskgroup.pack.json").read_text(encoding="utf-8"))
    assert pack["metadata"]["waited_for_outputs"] is True
    assert pack["metadata"]["final_group"]["status"]["is_active"] is False
    assert pack["item_count"] == 1


def test_parallel_taskgroup_pack_snapshots_run_ids_without_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeTaskGroup:
        def create(self, **kwargs: object) -> Response:
            assert kwargs["metadata"] == {"source": "docpull"}
            return Response(task_group_id="taskgroup_123")

        def add_runs(self, task_group_id: str, **kwargs: object) -> Response:
            assert task_group_id == "taskgroup_123"
            assert kwargs["refresh_status"] is False
            return Response(
                run_ids=["run_1", "run_2"],
                status=Response(is_active=True),
                event_cursor="event_cursor_1",
                run_cursor="run_cursor_1",
            )

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.task_group = FakeTaskGroup()

    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps([{"company": "Parallel"}, {"company": "Cursor"}]), encoding="utf-8")
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "taskgroup-pack",
                str(inputs),
                "--processor",
                "lite",
                "--output-dir",
                str(tmp_path / "taskgroup"),
            ]
        )
        == 0
    )

    pack = json.loads((tmp_path / "taskgroup" / "taskgroup.pack.json").read_text(encoding="utf-8"))
    assert pack["item_count"] == 2
    assert pack["record_count"] == 2
    assert [source["title"] for source in pack["sources"]] == ["run_1", "run_2"]
    assert pack["metadata"]["run_response"]["run_ids"] == ["run_1", "run_2"]
    assert pack["artifacts"]["agent_context"] == "AGENT_CONTEXT.md"


def test_parallel_monitor_create_and_events_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeMonitor:
        def create(self, **kwargs: object) -> Response:
            assert kwargs["settings"] == {"query": "Track Parallel releases"}
            return Response(monitor_id="monitor_123", status="active", query="Track Parallel releases")

        def events(self, monitor_id: str, **kwargs: object) -> Response:
            assert monitor_id == "monitor_123"
            assert kwargs["limit"] == 3
            assert "cursor" not in kwargs
            assert "event_group_id" not in kwargs
            return Response(
                events=[
                    Response(
                        event_id="event_1",
                        url="https://parallel.ai/changelog",
                        output=Response(content="New release detected."),
                    )
                ],
                next_cursor="next",
            )

        def list(self, **kwargs: object) -> Response:
            assert kwargs["status"] == ["active"]
            return Response(
                monitors=[
                    Response(
                        monitor_id="monitor_123",
                        status="active",
                        query="Track Parallel releases",
                    )
                ],
                next_cursor="cursor_2",
            )

        def retrieve(self, monitor_id: str) -> Response:
            assert monitor_id == "monitor_123"
            return Response(monitor_id=monitor_id, status="active")

        def update(self, monitor_id: str, **kwargs: object) -> Response:
            assert monitor_id == "monitor_123"
            assert kwargs["frequency"] == "6h"
            return Response(monitor_id=monitor_id, status="active", frequency="6h")

        def cancel(self, monitor_id: str) -> Response:
            assert monitor_id == "monitor_123"
            return Response(monitor_id=monitor_id, status="cancelled")

        def trigger(self, monitor_id: str) -> None:
            assert monitor_id == "monitor_123"
            return None

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.monitor = FakeMonitor()

    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "create",
                "Track Parallel releases",
                "--output-dir",
                str(tmp_path / "monitor"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "events",
                "monitor_123",
                "--limit",
                "3",
                "--output-dir",
                str(tmp_path / "events"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "list",
                "--status",
                "active",
                "--output-dir",
                str(tmp_path / "monitor-list"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "retrieve",
                "monitor_123",
                "--output-dir",
                str(tmp_path / "monitor-retrieve"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "update",
                "monitor_123",
                "--frequency",
                "6h",
                "--output-dir",
                str(tmp_path / "monitor-update"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "cancel",
                "monitor_123",
                "--output-dir",
                str(tmp_path / "monitor-cancel"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "trigger",
                "monitor_123",
                "--output-dir",
                str(tmp_path / "monitor-trigger"),
            ]
        )
        == 0
    )

    monitor_pack = json.loads((tmp_path / "monitor" / "monitor.pack.json").read_text(encoding="utf-8"))
    events_pack = json.loads((tmp_path / "events" / "monitor.events.pack.json").read_text(encoding="utf-8"))
    list_pack = json.loads((tmp_path / "monitor-list" / "monitor.list.pack.json").read_text(encoding="utf-8"))
    update_pack = json.loads(
        (tmp_path / "monitor-update" / "monitor.update.pack.json").read_text(encoding="utf-8")
    )
    assert monitor_pack["metadata"]["monitor_id"] == "monitor_123"
    assert events_pack["item_count"] == 1
    assert events_pack["metadata"]["next_cursor"] == "next"
    assert list_pack["metadata"]["next_cursor"] == "cursor_2"
    assert update_pack["metadata"]["updated"]["frequency"] == "6h"


def test_parallel_monitor_snapshot_and_event_stream_policy_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, object]] = []
    updated: dict[str, object] = {}

    class Response:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class FakeMonitor:
        def create(self, **kwargs: object) -> Response:
            created.append(dict(kwargs))
            return Response(monitor_id=f"monitor_{len(created)}", status="active")

        def update(self, monitor_id: str, **kwargs: object) -> Response:
            assert monitor_id == "monitor_1"
            updated.update(kwargs)
            return Response(monitor_id=monitor_id, status="active")

    class FakeParallel:
        def __init__(self, *, api_key: str) -> None:
            assert api_key == "test-key"
            self.monitor = FakeMonitor()

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "properties": {"headline": {"type": "string"}}}))
    monkeypatch.setenv("PARALLEL_API_KEY", "test-key")
    monkeypatch.setattr(parallel_workflows, "_require_parallel_sdk", lambda: FakeParallel)

    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "create",
                "Track Parallel docs",
                "--include-domain",
                "docs.parallel.ai",
                "--location",
                "us",
                "--output-schema",
                str(schema),
                "--include-backfill",
                "--metadata",
                "owner=docs",
                "--output-dir",
                str(tmp_path / "event-monitor"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "create",
                "--type",
                "snapshot",
                "--task-run-id",
                "run_123",
                "--output-dir",
                str(tmp_path / "snapshot-monitor"),
            ]
        )
        == 0
    )
    assert (
        run_parallel_cli(
            [
                "monitor-pack",
                "update",
                "monitor_1",
                "--query",
                "Track Parallel docs updates",
                "--include-domain",
                "docs.parallel.ai",
                "--location",
                "us",
                "--metadata",
                "owner=docs",
                "--output-dir",
                str(tmp_path / "monitor-update-policy"),
            ]
        )
        == 0
    )

    assert created[0]["settings"]["advanced_settings"]["source_policy"]["include_domains"] == [
        "docs.parallel.ai"
    ]
    assert created[0]["settings"]["output_schema"]["type"] == "json"
    assert created[1]["type"] == "snapshot"
    assert created[1]["settings"] == {"task_run_id": "run_123"}
    assert updated["settings"]["advanced_settings"]["location"] == "us"
    assert updated["metadata"] == {"owner": "docs"}


def test_parallel_api_pack_from_openapi(tmp_path: Path) -> None:
    spec = tmp_path / "openapi.json"
    spec.write_text(
        json.dumps(
            {
                "openapi": "3.1.0",
                "info": {"title": "Example API", "version": "1.0.0"},
                "paths": {
                    "/v1/search": {
                        "post": {
                            "summary": "Search",
                            "description": "Searches the web.",
                            "operationId": "search",
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert run_parallel_cli(["api-pack", str(spec), "--output-dir", str(tmp_path / "api")]) == 0

    pack = json.loads((tmp_path / "api" / "api.pack.json").read_text(encoding="utf-8"))
    assert pack["metadata"]["kind"] == "openapi"
    assert pack["metadata"]["operation_count"] == 1
    source_file = next((tmp_path / "api" / "sources").glob("*.md"))
    assert "Search" in source_file.read_text(encoding="utf-8")


def test_parallel_api_pack_from_llms_txt(tmp_path: Path) -> None:
    llms = tmp_path / "llms.txt"
    llms.write_text(
        "# Docs\n\n- [Search](https://docs.example.com/search.md): Search API docs.\n",
        encoding="utf-8",
    )

    assert run_parallel_cli(["api-pack", str(llms), "--output-dir", str(tmp_path / "llms")]) == 0

    pack = json.loads((tmp_path / "llms" / "api.pack.json").read_text(encoding="utf-8"))
    assert pack["metadata"]["kind"] == "llms"
    assert pack["metadata"]["link_count"] == 1


def test_parallel_api_pack_rejects_http_loopback_url(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        run_parallel_cli(
            [
                "api-pack",
                "http://127.0.0.1:8765/openapi.json",
                "--output-dir",
                str(tmp_path / "api"),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    output = " ".join(captured.out.split())
    assert "Remote api-pack source rejected" in output
    assert "Scheme 'http'" in output
    assert "not allowed" in output


def test_parallel_api_pack_rejects_https_loopback_url(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        run_parallel_cli(
            [
                "api-pack",
                "https://127.0.0.1:8765/openapi.json",
                "--output-dir",
                str(tmp_path / "api"),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    output = " ".join(captured.out.split())
    assert "Remote api-pack source rejected" in output
    assert "not allowed" in output


def test_parallel_missing_sdk_error_is_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    original_import = builtins.__import__

    def fake_import(name, globals_=None, locals_=None, fromlist=(), level=0):  # type: ignore[no-untyped-def]
        if name == "parallel":
            raise ImportError("not installed")
        return original_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ParallelWorkflowError) as exc:
        _require_parallel_sdk()

    assert "pip install 'docpull[parallel]'" in str(exc.value)


def test_parallel_help_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as top_help:
        main(["parallel", "--help"])
    assert top_help.value.code == 0
    top_help_out = capsys.readouterr().out
    assert "auth" in top_help_out
    assert "context-pack" in top_help_out

    with pytest.raises(SystemExit) as auth_help:
        main(["parallel", "auth", "--help"])
    assert auth_help.value.code == 0
    assert "--json" in capsys.readouterr().out

    with pytest.raises(SystemExit) as context_help:
        main(["parallel", "context-pack", "--help"])
    assert context_help.value.code == 0
    assert "--extract-limit" in capsys.readouterr().out

    with pytest.raises(SystemExit) as demo_help:
        main(["parallel", "demo", "--help"])
    assert demo_help.value.code == 0
    assert "--output-dir" in capsys.readouterr().out

    with pytest.raises(SystemExit) as run_help:
        main(["parallel", "run", "--help"])
    assert run_help.value.code == 0
    run_help_out = capsys.readouterr().out
    assert "context-pack" in run_help_out
    assert "recipe" in run_help_out


def test_parallel_cli_rejects_invalid_numeric_options() -> None:
    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--extract-limit", "0"])

    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--extract-limit", "21"])

    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--max-tokens-per-file", "99"])

    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--max-estimated-cost", "-1"])

    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--after-date", "2026-99-99"])

    with pytest.raises(SystemExit):
        main(["parallel", "context-pack", "objective", "--fetch-max-age-seconds", "599"])


def test_parallel_context_pack_rejects_url_shaped_source_domain(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        run_parallel_cli(
            [
                "context-pack",
                "objective",
                "--include-domain",
                "https://docs.parallel.ai/search",
                "--dry-run",
            ]
        )
        == 1
    )

    assert "domains only" in capsys.readouterr().out
