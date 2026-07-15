"""Build content-free data and methodology bundles without marketing claims."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .comparison import compare_reports, comparison_markdown
from .models import BenchmarkSuite, PortableReport

_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")


def publish_results(
    suite_path: Path,
    report_paths: list[Path],
    *,
    output_dir: Path,
    unavailable: list[str] | None = None,
    provisional: bool = False,
) -> Path:
    suite = BenchmarkSuite.from_yaml(suite_path)
    reports = [PortableReport.model_validate_json(path.read_text(encoding="utf-8")) for path in report_paths]
    comparison = compare_reports(report_paths)
    suite_hash = _file_sha256(suite_path)
    if suite_hash != comparison.suite_sha256:
        raise ValueError("suite does not match report suite hash")
    if output_dir.exists():
        raise ValueError(f"publication output already exists: {output_dir}")

    output_dir.mkdir(parents=True)
    reports_dir = output_dir / "reports"
    reports_dir.mkdir()
    sources: list[dict[str, str]] = []
    for source, report in zip(report_paths, reports, strict=True):
        destination = reports_dir / f"{_safe_name(report.manifest.system)}.report.json"
        public = report.model_copy(
            update={
                "observations": [
                    observation.model_copy(update={"artifacts": {}}) for observation in report.observations
                ]
            }
        )
        destination.write_text(public.model_dump_json(indent=2) + "\n", encoding="utf-8")
        sources.append(
            {
                "system": report.manifest.system,
                "source_sha256": _file_sha256(source),
                "published_path": str(destination.relative_to(output_dir)),
                "published_sha256": _file_sha256(destination),
            }
        )

    (output_dir / "suite.yaml").write_bytes(suite_path.read_bytes())
    (output_dir / "comparison.json").write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
    (output_dir / "COMPARISON.md").write_text(comparison_markdown(comparison), encoding="utf-8")
    (output_dir / "METHODOLOGY.md").write_text(
        _methodology(
            suite,
            reports,
            comparison.protocol_sha256,
            comparison.analysis_version,
            provisional,
        ),
        encoding="utf-8",
    )
    (output_dir / "README.md").write_text(_readme(suite, provisional, unavailable or []), encoding="utf-8")
    hashes = {
        str(path.relative_to(output_dir)): _file_sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "provisional-not-current-evidence-not-for-marketing" if provisional else "data-only",
        "suite_name": suite.name,
        "suite_version": suite.version,
        "suite_sha256": suite_hash,
        "protocol_sha256": comparison.protocol_sha256,
        "scorer_version": comparison.scorer_version,
        "analysis_version": comparison.analysis_version,
        "source_report_set_sha256": _json_hash(
            sorted((item["system"], item["source_sha256"]) for item in sources)
        ),
        "source_reports": sources,
        "unavailable_systems": _parse_unavailable(unavailable or []),
        "files": hashes,
    }
    (output_dir / "publication.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output_dir


def sign_publication(bundle: Path, *, key: str | None = None) -> Path:
    bundle = bundle.resolve()
    verify_publication(bundle)
    manifest = bundle / "publication.manifest.json"
    signature = bundle / "publication.manifest.json.asc"
    if signature.exists():
        raise ValueError("publication signature already exists")
    command = ["gpg", "--batch", "--yes", "--armor", "--detach-sign", "--output", str(signature)]
    if key:
        command.extend(["--local-user", key])
    command.append(str(manifest))
    try:
        result = subprocess.run(command, capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("GPG publication signing failed") from error
    if result.returncode != 0 or not signature.is_file():
        signature.unlink(missing_ok=True)
        raise ValueError("GPG publication signing failed")
    return signature


def verify_publication(
    bundle: Path,
    *,
    trusted_gpg_fingerprint: str | None = None,
) -> dict[str, str | int]:
    bundle = bundle.resolve()
    manifest_path = bundle / "publication.manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("publication manifest is missing or invalid") from error
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 3:
        raise ValueError("publication manifest must use schema version 3")
    expected_hashes = manifest.get("files")
    if not isinstance(expected_hashes, dict) or not all(
        isinstance(path, str) and isinstance(digest, str) for path, digest in expected_hashes.items()
    ):
        raise ValueError("publication manifest file map is invalid")
    actual_files = {
        str(path.relative_to(bundle))
        for path in bundle.rglob("*")
        if path.is_file() and path.name not in {"publication.manifest.json", "publication.manifest.json.asc"}
    }
    if actual_files != set(expected_hashes):
        missing = sorted(set(expected_hashes) - actual_files)
        extra = sorted(actual_files - set(expected_hashes))
        raise ValueError(f"publication file set mismatch; missing={missing} extra={extra}")
    for relative, expected in expected_hashes.items():
        path = (bundle / relative).resolve()
        if bundle not in path.parents or _file_sha256(path) != expected:
            raise ValueError(f"publication file hash mismatch: {relative}")

    report_paths = sorted((bundle / "reports").glob("*.report.json"))
    if len(report_paths) < 2:
        raise ValueError("publication must contain at least two portable reports")
    for path in report_paths:
        PortableReport.model_validate_json(path.read_text(encoding="utf-8"))
    comparison = compare_reports(report_paths)
    stored_comparison = json.loads((bundle / "comparison.json").read_text(encoding="utf-8"))
    if comparison.model_dump(mode="json") != stored_comparison:
        raise ValueError("publication comparison does not match regenerated report analysis")
    suite_path = bundle / "suite.yaml"
    BenchmarkSuite.from_yaml(suite_path)
    if _file_sha256(suite_path) != manifest.get("suite_sha256"):
        raise ValueError("publication suite hash does not match manifest")
    if comparison.suite_sha256 != manifest.get("suite_sha256"):
        raise ValueError("publication reports do not match the bundled suite")

    signer = "unsigned"
    signature = bundle / "publication.manifest.json.asc"
    if trusted_gpg_fingerprint:
        if not signature.is_file():
            raise ValueError("trusted GPG verification requires a detached publication signature")
        signer = _verify_gpg_signature(
            signature,
            manifest_path,
            trusted_gpg_fingerprint,
        )
    return {"status": "valid", "file_count": len(actual_files), "signer": signer}


def _verify_gpg_signature(signature: Path, payload: Path, trusted_fingerprint: str) -> str:
    try:
        result = subprocess.run(
            ["gpg", "--batch", "--status-fd", "1", "--verify", str(signature), str(payload)],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError("GPG publication verification failed") from error
    valid = {
        line.split()[2].upper()
        for line in result.stdout.decode(errors="replace").splitlines()
        if line.startswith("[GNUPG:] VALIDSIG ") and len(line.split()) >= 3
    }
    trusted = trusted_fingerprint.replace(" ", "").upper()
    if result.returncode != 0 or trusted not in valid:
        raise ValueError("publication signature is not from the trusted GPG fingerprint")
    return trusted


def _methodology(
    suite: BenchmarkSuite,
    reports: list[PortableReport],
    protocol_hash: str,
    analysis_version: str,
    provisional: bool,
) -> str:
    lanes = ", ".join(sorted({case.input.lane.value for case in suite.cases}))
    lines = [
        "# Methodology",
        "",
        "This bundle is generated benchmark data and methodology, not a marketing claim.",
        "Gold expectations were retained by the harness and were not sent to adapters.",
        "One deterministic canonical scorer per lane produced the stored assertion vectors.",
        "No LLM judge or cross-lane composite was used.",
        "",
        f"Suite version: `{suite.version}`",
        f"Protocol SHA-256: `{protocol_hash}`",
        f"Analysis version: `{analysis_version}`",
        f"Lanes: {lanes}",
        "",
        "| System | Version | Revision | Dirty | Environment | Network | Cache | Retry | Trials |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for report in sorted(reports, key=lambda item: item.manifest.system):
        manifest = report.manifest
        lines.append(
            f"| {manifest.system} | `{manifest.adapter_version}` | "
            f"`{manifest.git_revision or 'unknown'}` | {manifest.git_dirty} | "
            f"{manifest.environment_label} | {manifest.network_isolation} | "
            f"{manifest.cache_policy} | {manifest.retry_policy} | {manifest.repeat} |"
        )
    lines.extend(
        [
            "",
            "Portable reports contain URLs after query sanitization, hashes, lengths, timings, usage, "
            "cost classifications, statuses, and score vectors. Fetched bodies are excluded.",
        ]
    )
    if provisional:
        lines.extend(
            [
                "",
                "WARNING: This is a migrated historical fixture. It is not current evidence and is "
                "not approved for marketing use.",
            ]
        )
    return "\n".join(lines) + "\n"


def _readme(suite: BenchmarkSuite, provisional: bool, unavailable: list[str]) -> str:
    status = (
        "PROVISIONAL — NOT CURRENT EVIDENCE — NOT FOR MARKETING"
        if provisional
        else "DATA AND METHODOLOGY ONLY — NARRATIVE FINDINGS REQUIRE HUMAN REVIEW"
    )
    lines = [
        f"# {suite.name} {suite.version}",
        "",
        f"**{status}**",
        "",
        "This bundle intentionally does not generate product claims or name a winner. See "
        "`COMPARISON.md` for lane-local deterministic results and `METHODOLOGY.md` for run metadata.",
        "Conditional quality covers completed acquisitions only. Provider spend excludes local "
        "compute and operator time, and non-comparable latency must not be ranked.",
    ]
    rows = _parse_unavailable(unavailable)
    if rows:
        lines.extend(["", "## Unavailable systems", "", "| System | Reason |", "| --- | --- |"])
        lines.extend(f"| {row['system']} | {row['reason']} |" for row in rows)
    return "\n".join(lines) + "\n"


def _parse_unavailable(values: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for value in values:
        if "=" not in value:
            raise ValueError("--unavailable values must use SYSTEM=REASON")
        system, reason = value.split("=", 1)
        if not system.strip() or not reason.strip():
            raise ValueError("--unavailable requires both system and reason")
        rows.append({"system": system.strip(), "reason": reason.strip()})
    return rows


def _safe_name(value: str) -> str:
    value = _SAFE_NAME.sub("-", value.casefold()).strip("-")
    if not value:
        raise ValueError("system name cannot be used as a filename")
    return value


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
