"""Controlled benchmark for the persistent context-artifact lifecycle."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .sanitization import scrub_secrets

LIFECYCLE_SUITE_NAME = "context-lifecycle"
LIFECYCLE_SUITE_VERSION = "1.0.0"
BENCH_ROOT = Path(__file__).resolve().parents[2]
LifecycleCategory = Literal[
    "contract", "provenance", "reproducibility", "diff", "offline", "export", "ci", "policy"
]


class LifecycleCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: LifecycleCategory
    description: str
    passed: bool
    elapsed_seconds: float = Field(ge=0)
    evidence: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)
    error: str | None = None


class LifecycleReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    suite_name: Literal["context-lifecycle"] = "context-lifecycle"
    suite_version: Literal["1.0.0"] = "1.0.0"
    system: Literal["docpull"] = "docpull"
    system_version: str
    created_at: str
    python_version: str
    platform: str
    check_count: int = Field(ge=1)
    passed_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    checks: list[LifecycleCheck] = Field(min_length=1)


class LifecycleError(RuntimeError):
    """A benchmark assertion failed with portable evidence."""


def run_lifecycle_case(check: str, *, work_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    """Run one lifecycle assertion for the unified schema-v2 benchmark runner."""
    work_dir.mkdir(parents=True, exist_ok=True)
    fixture_v1 = work_dir / "fixture-v1"
    fixture_v2 = work_dir / "fixture-v2"
    fixture_clone = work_dir / "fixture-clone"
    _write_fixture_pack(fixture_v1, version=1)
    _write_fixture_pack(fixture_v2, version=2)
    shutil.copytree(fixture_v1, fixture_clone)

    def stable_identity() -> dict[str, str | int | float | bool | list[str]]:
        _prepare(fixture_v1)
        return _stable_identity_check(fixture_v1, fixture_clone)

    def context_ci() -> dict[str, str | int | float | bool | list[str]]:
        _prepare(fixture_v1)
        return _context_ci_check(fixture_v1)

    checks: dict[str, Callable[[], dict[str, str | int | float | bool | list[str]]]] = {
        "raw_contract": lambda: _raw_contract_check(fixture_v1),
        "eval_prepare": lambda: _eval_grade_check(fixture_v1),
        "stable_identity": stable_identity,
        "exact_diff": lambda: _exact_diff_check(fixture_v1, fixture_v2, work_dir),
        "offline_search": lambda: _offline_search_check(fixture_v1, work_dir),
        "exports": lambda: _export_check(fixture_v1, work_dir),
        "context_ci": context_ci,
        "lock_drift": lambda: _lockfile_drift_check(work_dir),
        "credential_non_persistence": lambda: _credential_non_persistence_check(work_dir),
        "zero_budget": lambda: _zero_budget_check(work_dir),
    }
    try:
        selected = checks[check]
    except KeyError as error:
        raise LifecycleError(f"unknown lifecycle check: {check}") from error
    return selected()


def run_lifecycle_benchmark(*, output_dir: Path) -> tuple[LifecycleReport, Path]:
    """Run network-free lifecycle checks and write a content-free report."""
    run_dir = output_dir / uuid.uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="docpull-lifecycle-") as temporary:
        work_dir = Path(temporary)
        fixture_v1 = work_dir / "fixture-v1"
        fixture_v2 = work_dir / "fixture-v2"
        fixture_clone = work_dir / "fixture-clone"
        _write_fixture_pack(fixture_v1, version=1)
        _write_fixture_pack(fixture_v2, version=2)
        shutil.copytree(fixture_v1, fixture_clone)

        checks: list[LifecycleCheck] = []
        checks.append(
            _check(
                "raw-contract",
                "contract",
                "A synthetic pack validates against the public raw v3 artifact contract.",
                lambda: _raw_contract_check(fixture_v1),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "eval-grade-contract",
                "provenance",
                "Preparation emits the complete eval-grade provenance and citation contract.",
                lambda: _eval_grade_check(fixture_v1),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "stable-identities",
                "reproducibility",
                "Independent preparation preserves document hashes and stable citation identities.",
                lambda: _stable_identity_check(fixture_v1, fixture_clone),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "exact-diff",
                "diff",
                "A controlled corpus update reports exact added, removed, changed, and unchanged URLs.",
                lambda: _exact_diff_check(fixture_v1, fixture_v2, work_dir),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "offline-cited-search",
                "offline",
                "A prepared pack remains locally searchable with record-level citations and no network.",
                lambda: _offline_search_check(fixture_v1, work_dir),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "agent-exports",
                "export",
                "The same pack exports to OpenAI vector JSONL and a Codex skill without refetching.",
                lambda: _export_check(fixture_v1, work_dir),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "context-ci",
                "ci",
                "Context CI gates pack quality, citation coverage, and redistribution rights.",
                lambda: _context_ci_check(fixture_v1),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "lockfile-drift",
                "policy",
                "Dependency installation rejects manifest drift from the existing context lockfile.",
                lambda: _lockfile_drift_check(work_dir),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "credential-non-persistence",
                "policy",
                "Environment-provided source credentials are not persisted in project artifacts.",
                lambda: _credential_non_persistence_check(work_dir),
                work_dir=work_dir,
            )
        )
        checks.append(
            _check(
                "zero-budget-block",
                "policy",
                "A paid-capable cloud render route is blocked before execution under a zero budget.",
                lambda: _zero_budget_check(work_dir),
                work_dir=work_dir,
            )
        )

    passed_count = sum(check.passed for check in checks)
    report = LifecycleReport(
        system_version=_docpull_version(),
        created_at=datetime.now(timezone.utc).isoformat(),
        python_version=platform.python_version(),
        platform=platform.platform(),
        check_count=len(checks),
        passed_count=passed_count,
        pass_rate=passed_count / len(checks),
        checks=checks,
    )
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (run_dir / "REPORT.md").write_text(lifecycle_markdown(report), encoding="utf-8")
    return report, run_dir


def lifecycle_markdown(report: LifecycleReport) -> str:
    lines = [
        f"# {report.suite_name} {report.suite_version}",
        "",
        f"System: `{report.system}` `{report.system_version}`  ",
        f"Result: **{report.passed_count}/{report.check_count} ({report.pass_rate:.1%})**",
        "",
        "This is a controlled, network-free artifact-lifecycle benchmark. It is not a hosted-provider "
        "leaderboard; another system can enter by producing and operating on the same published contract.",
        "",
        "| Check | Category | Result | Seconds | Evidence |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for check in report.checks:
        evidence = ", ".join(f"{key}={value}" for key, value in sorted(check.evidence.items()))
        lines.append(
            f"| `{check.id}` | {check.category} | {'pass' if check.passed else 'fail'} | "
            f"{check.elapsed_seconds:.3f} | {evidence or check.error or ''} |"
        )
    lines.extend(
        [
            "",
            "All fixture content is synthetic and redistribution-safe. Reports contain no fixture bodies, "
            "credentials, or temporary filesystem paths.",
        ]
    )
    return "\n".join(lines) + "\n"


def publish_lifecycle_report(report: LifecycleReport, *, output_dir: Path) -> Path:
    """Write a self-verifying, content-free lifecycle publication."""
    if output_dir.exists():
        raise ValueError(f"lifecycle publication output already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    report_path = output_dir / "report.json"
    report_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (output_dir / "REPORT.md").write_text(lifecycle_markdown(report), encoding="utf-8")
    (output_dir / "README.md").write_text(
        "\n".join(
            [
                f"# {report.suite_name} {report.suite_version} results",
                "",
                f"DocPull {report.system_version} passed **{report.passed_count}/{report.check_count}** "
                "controlled context-lifecycle checks.",
                "",
                "This demonstrates a reproducible local artifact lifecycle: contract validation, "
                "eval-grade provenance, stable identities, exact diffs, offline cited retrieval, "
                "agent exports, Context CI, lockfile drift detection, credential non-persistence, "
                "and zero-budget enforcement.",
                "",
                "It does not claim that hosted extraction, search, or research providers fail these "
                "tasks. Another system can enter this lane by implementing the same public fixture and "
                "artifact contract.",
                "",
                "See [REPORT.md](REPORT.md), [METHODOLOGY.md](METHODOLOGY.md), and "
                "[report.json](report.json).",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "METHODOLOGY.md").write_text(
        "\n".join(
            [
                "# Methodology",
                "",
                "The suite uses two small, benchmark-authored synthetic documentation packs. All "
                "source content is redistribution-safe. The fixture represents one unchanged page, "
                "one changed page, one removed page, and one added page.",
                "",
                "Every operation runs through the installed public DocPull CLI. Checks marked "
                "`network=disabled` run with all HTTP proxy variables pointed at a closed loopback "
                "port. The zero-budget cloud check supplies a fake credential and succeeds only when "
                "the route is rejected before a render artifact is written.",
                "",
                "Pass/fail assertions are deterministic and use no LLM judge. Temporary fixture "
                "directories, fixture bodies, and environment credentials are excluded from reports.",
                "",
                "This lane evaluates persistent context artifacts, not raw fetch coverage. It must not "
                "be aggregated with the live fixed-URL extraction score.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output_dir.iterdir())
        if path.is_file()
    }
    manifest = {
        "schema_version": 1,
        "suite_name": report.suite_name,
        "suite_version": report.suite_version,
        "system": report.system,
        "system_version": report.system_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    (output_dir / "publication.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_dir


def _check(
    check_id: str,
    category: LifecycleCategory,
    description: str,
    operation: Callable[[], dict[str, str | int | float | bool | list[str]]],
    *,
    work_dir: Path,
) -> LifecycleCheck:
    started = time.perf_counter()
    try:
        evidence = operation()
        return LifecycleCheck(
            id=check_id,
            category=category,
            description=description,
            passed=True,
            elapsed_seconds=time.perf_counter() - started,
            evidence=evidence,
        )
    except Exception as error:  # noqa: BLE001 - failures are benchmark outcomes
        message = scrub_secrets(f"{type(error).__name__}: {error}").replace(str(work_dir), "<WORKDIR>")
        return LifecycleCheck(
            id=check_id,
            category=category,
            description=description,
            passed=False,
            elapsed_seconds=time.perf_counter() - started,
            error=message,
        )


def _raw_contract_check(pack_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    output = pack_dir / "raw.validation.json"
    _run_cli(
        ["pack", "validate", str(pack_dir), "--level", "raw", "--format", "json", "--output", str(output)]
    )
    payload = _read_json(output)
    _require(payload.get("status") == "pass", f"raw validation failed: {payload.get('issues')}")
    return {
        "status": "pass",
        "required_artifacts": int(payload["summary"]["required_artifact_count"]),
        "records": int(payload["summary"]["loaded_record_count"]),
    }


def _eval_grade_check(pack_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    _prepare(pack_dir)
    output = pack_dir / "eval.validation.json"
    _run_cli(
        ["pack", "validate", str(pack_dir), "--level", "eval", "--format", "json", "--output", str(output)]
    )
    payload = _read_json(output)
    _require(payload.get("status") == "pass", f"eval validation failed: {payload.get('issues')}")
    required = [
        "context.lock.json",
        "coverage.report.json",
        "citation.index.json",
        "pack.score.json",
        "pack.audit.json",
        "rights.manifest.json",
        "provenance.graph.json",
        "basis.ndjson",
        "basis.report.json",
        "PACK_CARD.md",
    ]
    missing = [name for name in required if not (pack_dir / name).exists()]
    _require(not missing, f"missing eval artifacts: {missing}")
    citation_index = _read_json(pack_dir / "citation.index.json")
    return {
        "status": "pass",
        "required_artifacts": int(payload["summary"]["required_artifact_count"]),
        "citation_entries": len(citation_index.get("entries", [])),
    }


def _stable_identity_check(first: Path, second: Path) -> dict[str, str | int | float | bool | list[str]]:
    _prepare(second)
    first_docs = _read_ndjson(first / "documents.ndjson")
    second_docs = _read_ndjson(second / "documents.ndjson")
    first_identity = [(row["document_id"], row["content_hash"]) for row in first_docs]
    second_identity = [(row["document_id"], row["content_hash"]) for row in second_docs]
    _require(first_identity == second_identity, "document identities changed across identical preparation")
    first_citations = _citation_identity(first / "citation.index.json")
    second_citations = _citation_identity(second / "citation.index.json")
    _require(first_citations == second_citations, "citation identities changed across identical preparation")
    return {"stable_documents": len(first_identity), "stable_citations": len(first_citations)}


def _exact_diff_check(
    first: Path, second: Path, work_dir: Path
) -> dict[str, str | int | float | bool | list[str]]:
    output = work_dir / "diff.json"
    _run_cli(["pack", "diff", str(first), str(second), "--output", str(output)])
    payload = _read_json(output)
    expected = {"added_urls": 1, "removed_urls": 1, "changed_urls": 1, "unchanged_urls": 1}
    actual = {key: len(payload.get(key, [])) for key in expected}
    _require(actual == expected, f"unexpected diff counts: {actual}")
    return {key.removesuffix("_urls"): value for key, value in actual.items()}


def _offline_search_check(pack_dir: Path, work_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    output = work_dir / "search.json"
    _run_cli(
        [
            "pack",
            "search",
            str(pack_dir),
            "retry rate limits",
            "--output",
            str(output),
            "--require-domain",
            "docs.example.test",
        ],
        network_disabled=True,
    )
    payload = _read_json(output)
    results = payload.get("results", [])
    _require(results, "offline search returned no result")
    _require(
        results[0].get("url") == "https://docs.example.test/rate-limits",
        f"unexpected top result: {results[0].get('url')}",
    )
    _require(results[0].get("record_citation_id"), "offline result lacks a record citation")
    return {
        "result_count": int(payload.get("result_count", 0)),
        "top_record_citation": str(results[0]["record_citation_id"]),
        "network": "disabled",
    }


def _export_check(pack_dir: Path, work_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    vector_output = work_dir / "openai.jsonl"
    skill_output = work_dir / "codex-skill"
    _run_cli(
        ["export", str(pack_dir), "--format", "openai-vector-jsonl", "--output", str(vector_output)],
        network_disabled=True,
    )
    _run_cli(
        [
            "export",
            str(pack_dir),
            "--format",
            "codex-skill",
            "--output",
            str(skill_output),
            "--skill-name",
            "lifecycle-fixture",
        ],
        network_disabled=True,
    )
    vectors = _read_ndjson(vector_output)
    skill_files = sorted(path.name for path in skill_output.rglob("*") if path.is_file())
    _require(len(vectors) == 3, f"expected 3 vector records, received {len(vectors)}")
    _require("SKILL.md" in skill_files, f"Codex export missing SKILL.md: {skill_files}")
    return {"vector_records": len(vectors), "skill_files": len(skill_files), "network": "disabled"}


def _context_ci_check(pack_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    process = _run_cli(
        [
            "ci",
            str(pack_dir),
            "--min-pack-score",
            "0",
            "--min-audit-score",
            "0",
            "--min-citation-coverage",
            "1",
            "--require-rights",
            "redistribution",
            "--json",
        ],
        network_disabled=True,
    )
    payload = json.loads(process.stdout)
    gates = payload.get("gates", [])
    failing_gates = [gate for gate in gates if gate.get("status") == "fail"]
    _require(not failing_gates, f"Context CI failed: {failing_gates}")
    return {
        "status": str(payload.get("status")),
        "gate_count": len(gates),
        "warning_count": sum(gate.get("status") == "warn" for gate in gates),
        "network": "disabled",
    }


def _lockfile_drift_check(work_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    project = work_dir / "lock-project"
    project.mkdir()
    _run_cli(["init", "lock-project"], cwd=project, network_disabled=True)
    _run_cli(["add", "https://docs.example.test/api"], cwd=project, network_disabled=True)
    _run_cli(["install", "--json"], cwd=project, network_disabled=True)
    config_path = project / "docpull.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config["sources"].append({"name": "changed", "url": "https://docs.example.test/changed", "type": "html"})
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    process = _run_cli(["install", "--json"], cwd=project, check=False, network_disabled=True)
    combined = f"{process.stdout}\n{process.stderr}".casefold()
    _require(process.returncode != 0, "lockfile drift was accepted")
    _require("diverge" in combined or "lock" in combined, "failure did not identify lockfile drift")
    return {"initial_lock": True, "drift_rejected": True, "network": "disabled"}


def _credential_non_persistence_check(
    work_dir: Path,
) -> dict[str, str | int | float | bool | list[str]]:
    project = work_dir / "credential-project"
    project.mkdir()
    secret = "DOCPULL_LIFECYCLE_SENTINEL_SECRET"
    env = {"LIFECYCLE_PRIVATE_TOKEN": secret}
    _run_cli(["init", "credential-project"], cwd=project, env=env, network_disabled=True)
    _run_cli(
        [
            "add",
            "https://docs.example.test/private",
            "--auth",
            "bearer-env",
            "--auth-env",
            "LIFECYCLE_PRIVATE_TOKEN",
            "--auth-policy",
            "explicit-private",
        ],
        cwd=project,
        env=env,
        network_disabled=True,
    )
    _run_cli(["install", "--json"], cwd=project, env=env, network_disabled=True)
    persisted = [
        str(path.relative_to(project))
        for path in project.rglob("*")
        if path.is_file() and secret in path.read_text(encoding="utf-8", errors="ignore")
    ]
    _require(not persisted, f"credential was persisted in: {persisted}")
    config_text = (project / "docpull.yaml").read_text(encoding="utf-8")
    _require("LIFECYCLE_PRIVATE_TOKEN" in config_text, "credential environment reference was not preserved")
    return {"secret_persisted": False, "environment_reference": True, "network": "disabled"}


def _zero_budget_check(work_dir: Path) -> dict[str, str | int | float | bool | list[str]]:
    output = work_dir / "cloud-render"
    process = _run_cli(
        [
            "render",
            "https://example.com",
            "--runtime",
            "e2b",
            "--live-smoke",
            "--budget",
            "0",
            "--output-dir",
            str(output),
        ],
        check=False,
        env={"E2B_API_KEY": "DOCPULL_LIFECYCLE_FAKE_E2B_KEY"},
    )
    combined = f"{process.stdout}\n{process.stderr}".casefold()
    _require(process.returncode != 0, "zero-budget cloud route executed")
    _require("budget" in combined or "paid" in combined, "block did not report budget policy")
    rendered = output / "rendered_pages.ndjson"
    _require(not rendered.exists(), "cloud render wrote a completed render artifact")
    return {"blocked": True, "rendered_pages": 0, "budget_usd": 0.0}


def _prepare(pack_dir: Path) -> None:
    _run_cli(
        [
            "pack",
            "prepare",
            str(pack_dir),
            "--eval-grade",
            "--no-graph",
            "--require-domain",
            "docs.example.test",
            "--search-query",
            "retry rate limits",
        ],
        network_disabled=True,
    )


def _write_fixture_pack(pack_dir: Path, *, version: int) -> None:
    source = BENCH_ROOT / "fixtures" / "v2" / "lifecycle" / f"fixture-v{version}"
    if not source.is_dir():
        raise LifecycleError(f"missing generated lifecycle fixture: {source}")
    shutil.copytree(source, pack_dir)


def _docpull_version() -> str:
    try:
        return importlib.metadata.version("docpull")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _run_cli(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    env: dict[str, str] | None = None,
    network_disabled: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "docpull", *arguments]
    process_env = os.environ.copy()
    process_env.update(env or {})
    if network_disabled:
        process_env.update(
            {
                "HTTP_PROXY": "http://127.0.0.1:1",
                "HTTPS_PROXY": "http://127.0.0.1:1",
                "ALL_PROXY": "http://127.0.0.1:1",
                "NO_PROXY": "",
            }
        )
    process = subprocess.run(  # noqa: S603 - fixed interpreter and public CLI arguments
        command,
        cwd=cwd,
        env=process_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    if check and process.returncode != 0:
        raise LifecycleError(
            f"command failed ({process.returncode}): {' '.join(arguments)}\n"
            f"{process.stdout}\n{process.stderr}"
        )
    return process


def _citation_identity(path: Path) -> list[tuple[str, str, str]]:
    payload = _read_json(path)
    return sorted(
        (
            str(entry.get("record_citation_id") or ""),
            str(entry.get("url") or ""),
            str(entry.get("content_hash") or ""),
        )
        for entry in payload.get("entries", [])
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise LifecycleError(f"expected JSON object in {path.name}")
    return payload


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _require(condition: object, message: str) -> None:
    if not condition:
        raise LifecycleError(message)


def lifecycle_report_sha256(path: Path) -> str:
    """Return the SHA-256 of a lifecycle report for publication manifests."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
