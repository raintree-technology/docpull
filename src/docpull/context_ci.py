"""Context CI checks for DocPull context packs and projects."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from rich.console import Console
from rich.markup import escape

from .basis import DEFAULT_MIN_SUPPORTED_RATIO, BasisError, basis_report, read_basis
from .eval_grade import EvalGradeError, freshdocs_bench, generate_eval_pack, prepare_eval_grade_pack
from .local_workflows import LocalWorkflowError, audit_pack
from .pack_tools import PackToolError, prepare_pack
from .time_utils import utc_now_iso

CI_SCHEMA_VERSION = 1
DEFAULT_MIN_PACK_SCORE = 80
DEFAULT_MIN_AUDIT_SCORE = 80
DEFAULT_MIN_CITATION_COVERAGE = 0.90
DEFAULT_MIN_CONTEXT_PASS_RATE = 0.80
DEFAULT_MIN_BASIS_SUPPORTED_RATIO = DEFAULT_MIN_SUPPORTED_RATIO
RIGHTS_FIELDS = ("eval_generation", "redistribution", "model_training")

GateStatus = Literal["pass", "fail", "warn", "skip"]
TargetMode = Literal["project", "pack"]


class ContextCIError(RuntimeError):
    """User-facing Context CI error."""


@dataclass(frozen=True)
class CIThresholds:
    min_pack_score: int = DEFAULT_MIN_PACK_SCORE
    min_audit_score: int = DEFAULT_MIN_AUDIT_SCORE
    min_citation_coverage: float = DEFAULT_MIN_CITATION_COVERAGE
    min_context_pass_rate: float = DEFAULT_MIN_CONTEXT_PASS_RATE
    min_basis_supported_ratio: float = DEFAULT_MIN_BASIS_SUPPORTED_RATIO
    fail_on_medium_coverage: bool = False
    max_age_days: int | None = None
    require_rights: tuple[str, ...] = ()


def run_context_ci_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull ci",
        description="Run Context CI checks over a project or local context pack",
    )
    parser.add_argument("path", nargs="?", type=Path, help="Project root or context pack directory")
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Generate missing local trust/eval artifacts first",
    )
    parser.add_argument("--strict", action="store_true", help="Treat medium coverage as a failure")
    parser.add_argument("--sync", action="store_true", help="Project mode only: sync before checking")
    parser.add_argument("--predictions", type=Path, help="Context prediction JSONL to grade")
    parser.add_argument(
        "--require-rights",
        action="append",
        choices=RIGHTS_FIELDS,
        default=[],
        help="Require an allowed use in rights.manifest.json. Repeat as needed.",
    )
    parser.add_argument("--min-pack-score", type=int, help="Minimum pack.score.json score")
    parser.add_argument("--min-audit-score", type=int, help="Minimum pack.audit.json score")
    parser.add_argument("--min-citation-coverage", type=float, help="Minimum citation coverage, 0.0-1.0")
    parser.add_argument(
        "--min-context-pass-rate",
        type=float,
        help="Minimum context prediction pass rate, 0.0-1.0",
    )
    parser.add_argument(
        "--min-freshdocs-pass-rate",
        type=float,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        help="Warn/fail when fetched_at metadata is older than N days",
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="Print report JSON")
    args = parser.parse_args(argv)

    console = Console()
    try:
        payload = run_context_ci(
            path=args.path,
            prepare=args.prepare,
            strict=args.strict,
            sync=args.sync,
            predictions_path=args.predictions,
            cli_thresholds={
                "min_pack_score": args.min_pack_score,
                "min_audit_score": args.min_audit_score,
                "min_citation_coverage": args.min_citation_coverage,
                "min_context_pass_rate": args.min_context_pass_rate,
                "min_freshdocs_pass_rate": args.min_freshdocs_pass_rate,
                "max_age_days": args.max_age_days,
                "require_rights": tuple(args.require_rights or ()),
            },
        )
    except ContextCIError as err:
        console.print("[red]Context CI error:[/red] " + escape(str(err)))
        return 1

    if args.json_output:
        console.print_json(data=payload)
    else:
        status = "passed" if payload["passed"] else "failed"
        color = "green" if payload["passed"] else "red"
        summary = payload["summary"]
        console.print(
            f"[{color}]Context CI {status}:[/{color}] "
            f"{summary['pass_count']} pass, {summary['warn_count']} warn, "
            f"{summary['fail_count']} fail -> {payload['artifacts']['report']}"
        )
    return 0 if payload["passed"] else 1


def run_context_ci(
    *,
    path: Path | None = None,
    prepare: bool = False,
    strict: bool = False,
    sync: bool = False,
    predictions_path: Path | None = None,
    cli_thresholds: dict[str, Any] | None = None,
    prediction_grader: str = "deterministic",
) -> dict[str, Any]:
    """Run local Context CI checks over a project or standalone pack.

    ``prediction_grader`` is forwarded to ``freshdocs_bench`` when predictions
    are graded: ``deterministic`` (default), ``llm``, or ``hybrid``. The gate
    keeps reading ``summary.pass_rate``, which always carries the final rate.
    """

    target = _resolve_target(path)
    thresholds = _resolve_thresholds(target, cli_thresholds or {}, strict=strict)
    sync_payload: dict[str, Any] | None = None
    if sync:
        if target["mode"] != "project":
            raise ContextCIError("--sync is only supported for project mode")
        from .project import sync_project

        try:
            sync_payload = sync_project(root=Path(target["project_root"]))
        except Exception as err:  # noqa: BLE001
            raise ContextCIError(f"Project sync failed: {err}") from err
        target = _resolve_target(Path(target["project_root"]))

    pack_dir = Path(target["pack_dir"]) if target.get("pack_dir") else None
    if pack_dir is None or not pack_dir.exists():
        payload = _project_without_run_report(target, thresholds)
        _write_ci_outputs(Path(target["report_dir"]), payload)
        return payload

    if prepare:
        _prepare_ci_artifacts(pack_dir)

    gates: list[dict[str, Any]] = []
    gates.extend(_project_gates(target) if target["mode"] == "project" else [])
    gates.extend(_sidecar_gates(pack_dir))

    score_payload = _read_json(pack_dir / "pack.score.json", default=None)
    audit_payload = _read_json(pack_dir / "pack.audit.json", default=None)
    score_payload = score_payload if isinstance(score_payload, dict) else None
    audit_payload = audit_payload if isinstance(audit_payload, dict) else None

    gates.append(
        _threshold_gate(
            "pack_score",
            "Pack score meets threshold.",
            value=int(score_payload.get("score") or 0) if score_payload else 0,
            threshold=thresholds.min_pack_score,
        )
    )
    gates.append(
        _threshold_gate(
            "audit_score",
            "Pack audit score meets threshold.",
            value=int(audit_payload.get("score") or 0) if audit_payload else 0,
            threshold=thresholds.min_audit_score,
        )
    )
    gates.append(_coverage_gate(pack_dir, thresholds))
    gates.append(_citation_coverage_gate(audit_payload or {}, thresholds))
    gates.append(_max_age_gate(pack_dir, thresholds))
    gates.extend(_eval_grade_gates(pack_dir, prepare=prepare))
    gates.extend(_rights_gates(pack_dir, thresholds))
    gates.append(
        _basis_quality_gate(
            pack_dir,
            thresholds,
            fail_on_weak=thresholds.fail_on_medium_coverage or predictions_path is not None,
        )
    )
    gates.append(_context_predictions_gate(pack_dir, predictions_path, thresholds, grader=prediction_grader))

    if sync_payload:
        gates.append(_gate("project_sync", "pass", "Project sync completed before CI.", details=sync_payload))

    payload = _build_report(
        target=target,
        thresholds=thresholds,
        pack_dir=pack_dir,
        gates=gates,
        score_payload=score_payload,
        audit_payload=audit_payload,
    )
    _write_ci_outputs(pack_dir, payload)
    return payload


def _resolve_target(path: Path | None) -> dict[str, Any]:
    start = (path or Path.cwd()).resolve()
    if start.is_file():
        raise ContextCIError(f"Expected a project or pack directory, got file: {start}")
    if (start / "documents.ndjson").exists():
        return {
            "mode": "pack",
            "input_path": str(start),
            "pack_dir": str(start),
            "report_dir": str(start),
        }
    if (start / "docpull.yaml").exists():
        return _project_target(start)
    if path is None:
        try:
            from .project import find_project_root

            return _project_target(find_project_root(start))
        except Exception:
            return {
                "mode": "pack",
                "input_path": str(start),
                "pack_dir": str(start),
                "report_dir": str(start),
            }
    try:
        from .project import find_project_root

        return _project_target(find_project_root(start))
    except Exception as err:  # noqa: BLE001
        raise ContextCIError(f"Could not resolve {start} as a DocPull project or context pack") from err


def _project_target(project_root: Path) -> dict[str, Any]:
    from .project import _latest_run_id, project_paths

    paths = project_paths(project_root)
    latest_run_id = _latest_run_id(project_root)
    pack_dir = paths.runs / latest_run_id if latest_run_id else None
    return {
        "mode": "project",
        "input_path": str(project_root),
        "project_root": str(project_root),
        "latest_run_id": latest_run_id,
        "pack_dir": str(pack_dir) if pack_dir else None,
        "report_dir": str(pack_dir or project_root),
        "context_lock": str(paths.context_lock),
    }


def _resolve_thresholds(
    target: dict[str, Any],
    cli: dict[str, Any],
    *,
    strict: bool,
) -> CIThresholds:
    config_values: dict[str, Any] = {}
    config_fields: set[str] = set()
    if target["mode"] == "project":
        try:
            from .project import load_project_config

            project_config = load_project_config(Path(target["project_root"]))
            config_values = project_config.ci.model_dump(mode="json")
            config_fields = set(project_config.ci.model_fields_set)
        except Exception:
            config_values = {}

    require_rights = tuple(cli.get("require_rights") or config_values.get("require_rights") or ())
    config_context_pass_rate = (
        config_values.get("min_context_pass_rate") if "min_context_pass_rate" in config_fields else None
    )
    config_legacy_pass_rate = (
        config_values.get("min_freshdocs_pass_rate") if "min_freshdocs_pass_rate" in config_fields else None
    )
    return CIThresholds(
        min_pack_score=int(
            _coalesce(cli.get("min_pack_score"), config_values.get("min_pack_score"), DEFAULT_MIN_PACK_SCORE)
        ),
        min_audit_score=int(
            _coalesce(
                cli.get("min_audit_score"),
                config_values.get("min_audit_score"),
                DEFAULT_MIN_AUDIT_SCORE,
            )
        ),
        min_citation_coverage=float(
            _coalesce(
                cli.get("min_citation_coverage"),
                config_values.get("min_citation_coverage"),
                DEFAULT_MIN_CITATION_COVERAGE,
            )
        ),
        min_context_pass_rate=float(
            _coalesce(
                cli.get("min_context_pass_rate"),
                cli.get("min_freshdocs_pass_rate"),
                config_context_pass_rate,
                config_legacy_pass_rate,
                DEFAULT_MIN_CONTEXT_PASS_RATE,
            )
        ),
        fail_on_medium_coverage=bool(strict or config_values.get("fail_on_medium_coverage")),
        max_age_days=_optional_int(
            _coalesce(cli.get("max_age_days"), config_values.get("max_age_days"), None)
        ),
        require_rights=require_rights,
    )


def _prepare_ci_artifacts(pack_dir: Path) -> None:
    try:
        prepare_pack(pack_dir, default_search=False, graph=False, eval_grade=True)
        audit_pack(pack_dir)
        if not (pack_dir / "evals" / "tasks.public.jsonl").exists():
            generate_eval_pack(pack_dir)
    except (PackToolError, EvalGradeError, LocalWorkflowError) as err:
        raise ContextCIError(f"Could not prepare CI artifacts: {err}") from err


def _project_gates(target: dict[str, Any]) -> list[dict[str, Any]]:
    from .project import ProjectError, _read_context_lock, _validate_context_lock, load_project_config

    root = Path(target["project_root"])
    lock_path = Path(target["context_lock"])
    gates: list[dict[str, Any]] = []
    lock = _read_context_lock(lock_path)
    if not lock:
        gates.append(
            _gate(
                "project_lockfile",
                "fail",
                "Project lockfile is missing. Run `docpull install` or `docpull sync` intentionally.",
                artifacts={"context_lock": str(lock_path)},
            )
        )
    else:
        try:
            _validate_context_lock(load_project_config(root), lock)
        except ProjectError as err:
            gates.append(
                _gate("project_lockfile", "fail", str(err), artifacts={"context_lock": str(lock_path)})
            )
        else:
            gates.append(_gate("project_lockfile", "pass", "Project lockfile matches docpull.yaml."))
    if target.get("latest_run_id"):
        gates.append(_gate("latest_project_run", "pass", f"Latest run: {target['latest_run_id']}"))
    else:
        gates.append(_gate("latest_project_run", "fail", "Project has no completed run to check."))
    return gates


def _project_without_run_report(target: dict[str, Any], thresholds: CIThresholds) -> dict[str, Any]:
    gates = (
        _project_gates(target)
        if target["mode"] == "project"
        else [_gate("pack_exists", "fail", "Pack directory has no documents.ndjson.")]
    )
    return _build_report(
        target=target,
        thresholds=thresholds,
        pack_dir=None,
        gates=gates,
        score_payload=None,
        audit_payload=None,
    )


def _sidecar_gates(pack_dir: Path) -> list[dict[str, Any]]:
    current_count = len(_read_ndjson(pack_dir / "documents.ndjson"))
    manifest_count = _read_manifest_count(pack_dir)
    gates: list[dict[str, Any]] = []
    gates.append(_sidecar_gate(pack_dir / "pack.score.json", current_count, manifest_count))
    gates.append(_sidecar_gate(pack_dir / "pack.audit.json", current_count, manifest_count))
    return gates


def _sidecar_gate(path: Path, current_count: int, manifest_count: int | None) -> dict[str, Any]:
    name = f"{path.stem}_current"
    payload = _read_json(path, default=None)
    if not isinstance(payload, dict):
        return _gate(name, "fail", f"Missing required sidecar: {path.name}")
    summary = _dict_value(payload.get("summary"))
    sidecar_count = _optional_int(summary.get("record_count"))
    sidecar_doc_count = _optional_int(summary.get("document_count"))
    stale = sidecar_count is not None and sidecar_count != current_count
    stale_manifest = (
        manifest_count is not None and sidecar_doc_count is not None and sidecar_doc_count != manifest_count
    )
    if stale or stale_manifest:
        return _gate(
            name,
            "fail",
            f"{path.name} is stale relative to documents.ndjson or corpus.manifest.json.",
            details={
                "sidecar_record_count": sidecar_count,
                "documents_ndjson_record_count": current_count,
                "sidecar_document_count": sidecar_doc_count,
                "manifest_document_count": manifest_count,
            },
        )
    return _gate(name, "pass", f"{path.name} matches current corpus counts.")


def _coverage_gate(pack_dir: Path, thresholds: CIThresholds) -> dict[str, Any]:
    payload = _read_json(pack_dir / "coverage.report.json", default={})
    summary = _dict_value(payload.get("summary")) if isinstance(payload, dict) else {}
    confidence = str(summary.get("coverage_confidence") or "unknown")
    if confidence == "low":
        return _gate("coverage_confidence", "fail", "Coverage confidence is low.", value=confidence)
    if confidence == "medium":
        status: GateStatus = "fail" if thresholds.fail_on_medium_coverage else "warn"
        return _gate("coverage_confidence", status, "Coverage confidence is medium.", value=confidence)
    if confidence == "unknown":
        return _gate(
            "coverage_confidence",
            "warn",
            "No coverage.report.json confidence was found.",
            value=confidence,
        )
    return _gate("coverage_confidence", "pass", "Coverage confidence is acceptable.", value=confidence)


def _citation_coverage_gate(audit_payload: dict[str, Any], thresholds: CIThresholds) -> dict[str, Any]:
    dimensions = _dict_value(audit_payload.get("dimensions"))
    citation = _dict_value(dimensions.get("citation_coverage"))
    value = float(citation.get("value") or 0)
    status: GateStatus = "pass" if value >= thresholds.min_citation_coverage else "fail"
    return _gate(
        "citation_coverage",
        status,
        "Citation coverage meets threshold." if status == "pass" else "Citation coverage is below threshold.",
        value=value,
        threshold=thresholds.min_citation_coverage,
    )


def _eval_grade_gates(pack_dir: Path, *, prepare: bool) -> list[dict[str, Any]]:
    required = {
        "rights_manifest": "rights.manifest.json",
        "provenance_graph": "provenance.graph.json",
        "citation_index": "citation.index.json",
        "pack_card": "PACK_CARD.md",
    }
    missing = [filename for filename in required.values() if not (pack_dir / filename).exists()]
    if missing and prepare:
        try:
            prepare_eval_grade_pack(pack_dir)
        except EvalGradeError as err:
            return [_gate("eval_grade_artifacts", "fail", str(err), details={"missing": missing})]
        missing = [filename for filename in required.values() if not (pack_dir / filename).exists()]
    if missing:
        return [
            _gate(
                "eval_grade_artifacts",
                "warn",
                "Eval-grade artifacts are missing; rerun with `docpull ci --prepare`.",
                details={"missing": missing},
            )
        ]
    return [_gate("eval_grade_artifacts", "pass", "Eval-grade artifacts are present.")]


def _rights_gates(pack_dir: Path, thresholds: CIThresholds) -> list[dict[str, Any]]:
    payload = _read_json(pack_dir / "rights.manifest.json", default={})
    allowed = _dict_value(payload.get("allowed_use")) if isinstance(payload, dict) else {}
    gates: list[dict[str, Any]] = []
    if not thresholds.require_rights:
        unknown = [field for field in RIGHTS_FIELDS if str(allowed.get(field) or "unknown") == "unknown"]
        explicit = [field for field in RIGHTS_FIELDS if str(allowed.get(field) or "unknown") != "unknown"]
        status: GateStatus = "warn" if unknown and not explicit else "pass"
        gates.append(
            _gate(
                "rights_status",
                status,
                (
                    "Rights status is partially explicit."
                    if unknown and explicit
                    else "Rights status has unknown allowed-use fields."
                    if unknown
                    else "Rights status is explicit."
                ),
                details={"explicit": explicit, "unknown": unknown},
            )
        )
        return gates
    for field in thresholds.require_rights:
        value = str(allowed.get(field) or "unknown")
        gates.append(
            _gate(
                f"rights_{field}",
                "pass" if value in {"allowed", "allowed_with_conditions"} else "fail",
                f"Rights allowed_use.{field} is {value}.",
                value=value,
                threshold="allowed_or_allowed_with_conditions",
            )
        )
    return gates


def _basis_quality_gate(
    pack_dir: Path,
    thresholds: CIThresholds,
    *,
    fail_on_weak: bool,
) -> dict[str, Any]:
    path = pack_dir / "basis.ndjson"
    if not path.exists():
        missing_status: GateStatus = "fail" if fail_on_weak else "warn"
        return _gate(
            "basis_quality",
            missing_status,
            "Evidence basis is missing; run `docpull pack basis` or `docpull ci --prepare`.",
            threshold=thresholds.min_basis_supported_ratio,
            artifacts={"basis": "basis.ndjson"},
        )
    try:
        records = read_basis(path)
    except BasisError as err:
        return _gate("basis_quality", "fail", str(err), artifacts={"basis": "basis.ndjson"})
    report = basis_report(
        records,
        path=path,
        min_supported_ratio=thresholds.min_basis_supported_ratio,
    )
    summary = _dict_value(report.get("summary"))
    issues = _list_value(report.get("issues"))
    weak = bool(issues)
    status: GateStatus = "pass"
    if weak:
        status = "fail" if fail_on_weak else "warn"
    ratio = float(summary.get("supported_ratio") or 0)
    return _gate(
        "basis_quality",
        status,
        "Evidence basis supports required claims."
        if status == "pass"
        else "Evidence basis is weak or incomplete.",
        value=ratio,
        threshold=thresholds.min_basis_supported_ratio,
        details={"summary": summary, "issues": issues},
        artifacts={
            "basis": "basis.ndjson",
            "basis_report": "basis.report.json",
            "basis_markdown": "BASIS.md",
        },
    )


def _context_predictions_gate(
    pack_dir: Path,
    predictions_path: Path | None,
    thresholds: CIThresholds,
    *,
    grader: str = "deterministic",
) -> dict[str, Any]:
    if predictions_path is None:
        return _gate(
            "context_prediction_pass_rate",
            "skip",
            "No predictions were provided; context prediction grading skipped.",
        )
    try:
        report = freshdocs_bench(pack_dir, predictions_path=predictions_path, grader=grader)
    except EvalGradeError as err:
        return _gate("context_prediction_pass_rate", "fail", str(err))
    summary = _dict_value(report.get("summary"))
    pass_rate = summary.get("pass_rate")
    if pass_rate is None:
        return _gate("context_prediction_pass_rate", "fail", "No context predictions were graded.")
    value = float(pass_rate)
    return _gate(
        "context_prediction_pass_rate",
        "pass" if value >= thresholds.min_context_pass_rate else "fail",
        "Context prediction pass rate meets threshold."
        if value >= thresholds.min_context_pass_rate
        else "Context prediction pass rate is below threshold.",
        value=value,
        threshold=thresholds.min_context_pass_rate,
        artifacts={
            "prediction_report": "freshdocs.report.json",
        },
    )


def _max_age_gate(pack_dir: Path, thresholds: CIThresholds) -> dict[str, Any]:
    if thresholds.max_age_days is None:
        return _gate("max_age_days", "skip", "No max_age_days threshold configured.")
    fetched: list[datetime] = []
    for record in _read_ndjson(pack_dir / "documents.ndjson"):
        parsed = _parse_datetime(record.get("fetched_at"))
        if parsed:
            fetched.append(parsed)
    if not fetched:
        return _gate("max_age_days", "warn", "No fetched_at metadata was available for age checks.")
    oldest = min(fetched)
    age_days = (datetime.now(timezone.utc) - oldest).total_seconds() / 86400
    return _gate(
        "max_age_days",
        "pass" if age_days <= thresholds.max_age_days else "fail",
        "Fetched context age is within threshold."
        if age_days <= thresholds.max_age_days
        else "Fetched context age exceeds threshold.",
        value=round(age_days, 3),
        threshold=thresholds.max_age_days,
        details={"oldest_fetched_at": oldest.isoformat()},
    )


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _threshold_gate(name: str, message: str, *, value: int, threshold: int) -> dict[str, Any]:
    return _gate(
        name,
        "pass" if value >= threshold else "fail",
        message if value >= threshold else f"{message} Current value is below threshold.",
        value=value,
        threshold=threshold,
    )


def _build_report(
    *,
    target: dict[str, Any],
    thresholds: CIThresholds,
    pack_dir: Path | None,
    gates: list[dict[str, Any]],
    score_payload: dict[str, Any] | None,
    audit_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    fail_count = sum(1 for gate in gates if gate["status"] == "fail")
    warn_count = sum(1 for gate in gates if gate["status"] == "warn")
    pass_count = sum(1 for gate in gates if gate["status"] == "pass")
    skip_count = sum(1 for gate in gates if gate["status"] == "skip")
    payload: dict[str, Any] = {
        "schema_version": CI_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "mode": target["mode"],
        "input_path": target["input_path"],
        "pack_dir": str(pack_dir) if pack_dir else None,
        "passed": fail_count == 0,
        "summary": {
            "gate_count": len(gates),
            "pass_count": pass_count,
            "warn_count": warn_count,
            "fail_count": fail_count,
            "skip_count": skip_count,
        },
        "thresholds": {
            "min_pack_score": thresholds.min_pack_score,
            "min_audit_score": thresholds.min_audit_score,
            "min_citation_coverage": thresholds.min_citation_coverage,
            "min_context_pass_rate": thresholds.min_context_pass_rate,
            "min_basis_supported_ratio": thresholds.min_basis_supported_ratio,
            "fail_on_medium_coverage": thresholds.fail_on_medium_coverage,
            "max_age_days": thresholds.max_age_days,
            "require_rights": list(thresholds.require_rights),
        },
        "gates": gates,
        "artifacts": {
            "report": "context-ci.report.json",
            "markdown": "CONTEXT_CI.md",
        },
    }
    if target["mode"] == "project":
        payload["project"] = {
            "root": target.get("project_root"),
            "latest_run_id": target.get("latest_run_id"),
            "context_lock": target.get("context_lock"),
        }
    if score_payload:
        payload["pack_score"] = {
            "score": score_payload.get("score"),
            "grade": score_payload.get("grade"),
            "summary": score_payload.get("summary"),
        }
    if audit_payload:
        payload["pack_audit"] = {
            "score": audit_payload.get("score"),
            "grade": audit_payload.get("grade"),
            "summary": audit_payload.get("summary"),
        }
    return payload


def _write_ci_outputs(output_dir: Path, payload: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "context-ci.report.json"
    markdown_path = output_dir / "CONTEXT_CI.md"
    report_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(_ci_markdown(payload), encoding="utf-8")


def _ci_markdown(payload: dict[str, Any]) -> str:
    summary = _dict_value(payload.get("summary"))
    status = "passed" if payload.get("passed") else "failed"
    lines = [
        "# Context CI Report",
        "",
        f"- Status: **{status}**",
        f"- Mode: `{payload.get('mode')}`",
        f"- Pack: `{payload.get('pack_dir')}`",
        (
            f"- Gates: `{summary.get('pass_count', 0)}` pass, "
            f"`{summary.get('warn_count', 0)}` warn, "
            f"`{summary.get('fail_count', 0)}` fail, "
            f"`{summary.get('skip_count', 0)}` skip"
        ),
        "",
        "## Gates",
        "",
    ]
    for gate in payload.get("gates", []):
        if not isinstance(gate, dict):
            continue
        lines.append(f"- **{gate.get('status')}** `{gate.get('name')}` - {gate.get('message')}")
    return "\n".join(lines).rstrip() + "\n"


def _gate(
    name: str,
    status: GateStatus,
    message: str,
    *,
    value: Any = None,
    threshold: Any = None,
    details: dict[str, Any] | None = None,
    artifacts: dict[str, str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "status": status,
        "message": message,
    }
    if value is not None:
        payload["value"] = value
    if threshold is not None:
        payload["threshold"] = threshold
    if details:
        payload["details"] = details
    if artifacts:
        payload["artifacts"] = artifacts
    return payload


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise ContextCIError(f"Missing required file: {path}")
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise ContextCIError(f"Invalid NDJSON in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise ContextCIError(f"Invalid NDJSON in {path} line {index}: expected object")
        records.append(value)
    return records


def _read_json(path: Path, *, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise ContextCIError(f"Invalid JSON in {path}: {err}") from err


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _read_manifest_count(pack_dir: Path) -> int | None:
    manifest = _read_json(pack_dir / "corpus.manifest.json", default={})
    if not isinstance(manifest, dict):
        return None
    return _optional_int(manifest.get("document_count"))


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
