"""Context CI tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from docpull.basis import basis_record, write_basis
from docpull.cli import main
from docpull.context_ci import CIThresholds, _rights_gates


def _record(url: str = "https://docs.example.com/api", content_hash: str = "aaa") -> dict[str, object]:
    return {
        "document_id": f"doc_{content_hash}",
        "url": url,
        "title": "Example API",
        "content": (
            "Example API returns current cited JSON results for agents. "
            "Deprecated legacy behavior is no longer supported."
        ),
        "content_hash": content_hash,
        "source_type": "test",
        "fetched_at": "2026-07-01T00:00:00+00:00",
    }


def _write_pack(pack_dir: Path, *, coverage: str = "high") -> None:
    pack_dir.mkdir(parents=True, exist_ok=True)
    (pack_dir / "sources").mkdir(exist_ok=True)
    record = _record()
    (pack_dir / "documents.ndjson").write_text(json.dumps(record) + "\n", encoding="utf-8")
    (pack_dir / "corpus.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "document_count": 1,
                "record_count": 1,
                "records": [
                    {
                        "document_id": record["document_id"],
                        "url": record["url"],
                        "content_hash": record["content_hash"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "sources" / "01.md").write_text(str(record["content"]), encoding="utf-8")
    (pack_dir / "sources.md").write_text("# Sources\n", encoding="utf-8")
    (pack_dir / "local.pack.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "local",
                "workflow": "context-pack",
                "request_options": {"source_policy": {"include_domains": ["docs.example.com"]}},
                "extract_error_count": 0,
                "record_count": 1,
                "sources": [
                    {
                        "index": 1,
                        "url": record["url"],
                        "title": record["title"],
                        "path": "sources/01.md",
                    }
                ],
                "artifacts": {
                    "documents_ndjson": "documents.ndjson",
                    "corpus_manifest": "corpus.manifest.json",
                    "sources": "sources.md",
                },
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "coverage.report.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "summary": {
                    "coverage_confidence": coverage,
                    "discovered_url_count": 1,
                    "selected_url_count": 1,
                    "extracted_doc_count": 1,
                },
                "recommendations": [],
            }
        ),
        encoding="utf-8",
    )
    (pack_dir / "acquisition.routes.json").write_text(
        json.dumps({"schema_version": 1, "routes": [{"route": "sitemap", "fetched_count": 1}]}),
        encoding="utf-8",
    )


def _write_passing_sidecars(pack_dir: Path) -> None:
    assert main(["pack", "prepare", str(pack_dir), "--eval-grade", "--no-search", "--no-graph"]) == 0
    assert main(["pack", "audit", str(pack_dir)]) == 0


def test_context_ci_pack_prepare_writes_report_and_passes(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    assert main(["ci", str(pack), "--prepare"]) == 0

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["mode"] == "pack"
    assert (pack / "CONTEXT_CI.md").exists()
    assert (pack / "PACK_CARD.md").exists()
    assert (pack / "citation.index.json").exists()


def test_context_ci_low_coverage_fails(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack, coverage="low")

    assert main(["ci", str(pack), "--prepare"]) == 1

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["coverage_confidence"]["status"] == "fail"


def test_context_ci_medium_coverage_fails_only_in_strict_mode(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack, coverage="medium")

    assert main(["ci", str(pack), "--prepare"]) == 0
    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["coverage_confidence"]["status"] == "warn"

    assert main(["ci", str(pack), "--strict"]) == 1
    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["coverage_confidence"]["status"] == "fail"


def test_context_ci_stale_sidecars_fail(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    stale_score = {
        "schema_version": 1,
        "score": 95,
        "grade": "excellent",
        "summary": {"record_count": 2, "document_count": 1},
        "issues": [],
        "warnings": [],
    }
    stale_audit = {
        "schema_version": 1,
        "score": 95,
        "grade": "excellent",
        "summary": {"record_count": 2, "document_count": 1},
        "dimensions": {"citation_coverage": {"value": 1.0}},
        "issues": [],
        "warnings": [],
    }
    (pack / "pack.score.json").write_text(json.dumps(stale_score), encoding="utf-8")
    (pack / "pack.audit.json").write_text(json.dumps(stale_audit), encoding="utf-8")

    assert main(["ci", str(pack)]) == 1

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    stale = [gate for gate in report["gates"] if gate["name"] in {"pack.score_current", "pack.audit_current"}]
    assert stale
    assert all(gate["status"] == "fail" for gate in stale)


def test_context_ci_missing_eval_grade_warns_without_prepare(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    for filename in ("rights.manifest.json", "provenance.graph.json", "citation.index.json", "PACK_CARD.md"):
        (pack / filename).unlink()

    assert main(["ci", str(pack)]) == 0

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["eval_grade_artifacts"]["status"] == "warn"


def test_context_ci_basis_gate_passes_with_supported_basis(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    write_basis(
        pack / "basis.ndjson",
        [
            basis_record(
                claim_path="answer.question",
                claim="Example API returns current cited JSON results.",
                citation_ids=["S1"],
                source_urls=["https://docs.example.com/api"],
                excerpts=[
                    {
                        "citation_id": "S1",
                        "source_url": "https://docs.example.com/api",
                        "text": "Example API returns current cited JSON results for agents.",
                    }
                ],
                confidence="high",
                producer="test",
            )
        ],
    )

    assert main(["ci", str(pack)]) == 0

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["basis_quality"]["status"] == "pass"


def test_context_ci_missing_basis_warns_by_default_and_fails_strict(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    for filename in ("basis.ndjson", "basis.report.json", "BASIS.md"):
        path = pack / filename
        if path.exists():
            path.unlink()

    assert main(["ci", str(pack)]) == 0
    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["basis_quality"]["status"] == "warn"

    assert main(["ci", str(pack), "--strict"]) == 1
    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["basis_quality"]["status"] == "fail"


def test_context_ci_low_supported_basis_fails_with_predictions(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    write_basis(
        pack / "basis.ndjson",
        [
            basis_record(
                claim_path="answer.question",
                claim="Unsupported claim",
                confidence="low",
                evidence_state="insufficient",
                warnings=["no evidence"],
                producer="test",
            )
        ],
    )
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text(
        json.dumps({"id": "missing", "answer": "Unsupported claim"}) + "\n",
        encoding="utf-8",
    )

    assert main(["ci", str(pack), "--predictions", str(predictions)]) == 1

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["basis_quality"]["status"] == "fail"


def test_context_ci_missing_coverage_warns_without_crashing(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    (pack / "coverage.report.json").unlink()

    assert main(["ci", str(pack)]) == 0

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["coverage_confidence"]["status"] == "warn"
    assert gates["coverage_confidence"]["value"] == "unknown"


def test_context_ci_required_rights_fail_when_unknown(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)

    assert main(["ci", str(pack), "--prepare", "--require-rights", "eval_generation"]) == 1

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["rights_eval_generation"]["status"] == "fail"


def test_context_ci_required_rights_accept_conditioned_permissions(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "rights.manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "allowed_use": {
                    "internal_indexing": "allowed",
                    "redistribution": "allowed_with_conditions",
                    "model_training": "unknown",
                    "eval_generation": "allowed_with_conditions",
                },
                "obligations": ["preserve license notices"],
            }
        ),
        encoding="utf-8",
    )

    gates = _rights_gates(pack, CIThresholds(require_rights=("eval_generation",)))

    assert gates[0]["status"] == "pass"
    assert gates[0]["value"] == "allowed_with_conditions"


def test_context_ci_predictions_pass_and_fail(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    assert main(["ci", str(pack), "--prepare"]) == 0
    task = json.loads((pack / "evals" / "tasks.public.jsonl").read_text(encoding="utf-8").splitlines()[0])

    passing = tmp_path / "passing.jsonl"
    passing.write_text(
        json.dumps(
            {
                "id": task["id"],
                "answer": f"Supported by {task['required_sources'][0]}",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    failing = tmp_path / "failing.jsonl"
    failing.write_text(json.dumps({"id": task["id"], "answer": "No citation here."}) + "\n", encoding="utf-8")
    write_basis(
        pack / "basis.ndjson",
        [
            basis_record(
                claim_path="answer.question",
                claim="Example API returns current cited JSON results.",
                citation_ids=["S1"],
                source_urls=["https://docs.example.com/api"],
                excerpts=[
                    {
                        "citation_id": "S1",
                        "source_url": "https://docs.example.com/api",
                        "text": "Example API returns current cited JSON results for agents.",
                    }
                ],
                confidence="high",
                producer="test",
            )
        ],
    )

    assert main(["ci", str(pack), "--predictions", str(passing)]) == 0
    assert main(["ci", str(pack), "--predictions", str(failing)]) == 1

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert gates["context_prediction_pass_rate"]["status"] == "fail"


def test_context_ci_stale_answer_trap_fails_even_with_citation(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    _write_pack(pack)
    assert main(["ci", str(pack), "--prepare"]) == 0
    task = json.loads((pack / "evals" / "tasks.public.jsonl").read_text(encoding="utf-8").splitlines()[0])

    stale = tmp_path / "stale.jsonl"
    stale.write_text(
        json.dumps(
            {
                "id": task["id"],
                "answer": f"This is a pre-current context answer. Source: {task['required_sources'][0]}",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["ci", str(pack), "--predictions", str(stale)]) == 1

    bench = json.loads((pack / "freshdocs.report.json").read_text(encoding="utf-8"))
    assert bench["summary"]["pass_rate"] == 0.0
    assert bench["results"][0]["checks"]["failed_terms"] == ["pre-current context answer"]


def test_context_ci_project_mode_validates_lock_and_latest_run(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pack = project / ".docpull" / "runs" / "run_1"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    (project / ".docpull").mkdir(exist_ok=True)
    (project / ".docpull" / "latest-run").write_text("run_1\n", encoding="utf-8")
    (project / "docpull.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "sources": [{"name": "docs", "url": "https://docs.example.com", "type": "auto"}],
                "ci": {"min_pack_score": 80, "min_context_pass_rate": 0.8},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (project / ".docpull" / "context.lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "demo",
                "run_id": "run_1",
                "sources": [
                    {
                        "name": "docs",
                        "url": "https://docs.example.com",
                        "type": "auto",
                        "discover": False,
                        "discovered_urls": [],
                        "alias": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["ci", str(project), "--prepare"]) == 0

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    gates = {gate["name"]: gate for gate in report["gates"]}
    assert report["mode"] == "project"
    assert gates["project_lockfile"]["status"] == "pass"
    assert gates["latest_project_run"]["status"] == "pass"


def test_context_ci_project_legacy_freshdocs_threshold_alias(tmp_path: Path) -> None:
    project = tmp_path / "project"
    pack = project / ".docpull" / "runs" / "run_1"
    _write_pack(pack)
    _write_passing_sidecars(pack)
    (project / ".docpull").mkdir(exist_ok=True)
    (project / ".docpull" / "latest-run").write_text("run_1\n", encoding="utf-8")
    (project / "docpull.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "sources": [{"name": "docs", "url": "https://docs.example.com", "type": "auto"}],
                "ci": {"min_freshdocs_pass_rate": 1.0},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (project / ".docpull" / "context.lock.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project": "demo",
                "run_id": "run_1",
                "sources": [
                    {
                        "name": "docs",
                        "url": "https://docs.example.com",
                        "type": "auto",
                        "discover": False,
                        "discovered_urls": [],
                        "alias": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(["ci", str(project)]) == 0

    report = json.loads((pack / "context-ci.report.json").read_text(encoding="utf-8"))
    assert report["thresholds"]["min_context_pass_rate"] == 1.0
    assert "min_freshdocs_pass_rate" not in report["thresholds"]
