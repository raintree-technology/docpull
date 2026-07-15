"""Build content-free data and methodology bundles without marketing claims."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .comparison import compare_reports, comparison_markdown
from .integrity import file_sha256, load_portable_report, strict_json_file
from .models import BenchmarkSuite, ComparisonReport, PortableReport

_SAFE_NAME = re.compile(r"[^a-z0-9._-]+")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_KEYS = {
    "analysis_version",
    "files",
    "generated_at",
    "protocol_sha256",
    "schema_version",
    "scorer_version",
    "source_report_set_sha256",
    "source_reports",
    "status",
    "suite_name",
    "suite_sha256",
    "suite_version",
    "unavailable_systems",
}
_SOURCE_REPORT_KEYS = {"published_path", "published_sha256", "source_sha256", "system"}
_UNAVAILABLE_KEYS = {"reason", "system"}
_ROOT_FILES = {
    "COMPARISON.md",
    "METHODOLOGY.md",
    "README.md",
    "comparison.json",
    "suite.yaml",
}
_DATA_ONLY_STATUS = "data-only"
_PROVISIONAL_STATUS = "provisional-not-current-evidence-not-for-marketing"


def publish_results(
    suite_path: Path,
    report_paths: list[Path],
    *,
    output_dir: Path,
    unavailable: list[str] | None = None,
    provisional: bool = False,
) -> Path:
    suite = BenchmarkSuite.from_yaml(suite_path)
    reports = [load_portable_report(path) for path in report_paths]
    comparison = compare_reports(report_paths)
    suite_hash = file_sha256(suite_path)
    if suite_hash != comparison.suite_sha256:
        raise ValueError("suite does not match report suite hash")
    unavailable_rows = _parse_unavailable(unavailable or [])
    report_names = _publication_report_names(reports)

    output_dir = output_dir.expanduser().resolve()
    if output_dir.exists():
        raise ValueError(f"publication output already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        _write_publication(
            staging,
            suite_path=suite_path,
            suite=suite,
            suite_hash=suite_hash,
            report_paths=report_paths,
            reports=reports,
            report_names=report_names,
            comparison=comparison,
            unavailable_rows=unavailable_rows,
            provisional=provisional,
        )
        os.chmod(staging, 0o755)
        staging.replace(output_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output_dir


def _write_publication(
    output_dir: Path,
    *,
    suite_path: Path,
    suite: BenchmarkSuite,
    suite_hash: str,
    report_paths: list[Path],
    reports: list[PortableReport],
    report_names: dict[str, str],
    comparison: ComparisonReport,
    unavailable_rows: list[dict[str, str]],
    provisional: bool,
) -> None:
    reports_dir = output_dir / "reports"
    reports_dir.mkdir(mode=0o755)
    sources: list[dict[str, str]] = []
    for source, report in zip(report_paths, reports, strict=True):
        relative = f"reports/{report_names[report.manifest.system]}"
        destination = output_dir / relative
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
                "source_sha256": file_sha256(source),
                "published_path": relative,
                "published_sha256": file_sha256(destination),
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
    (output_dir / "README.md").write_text(_readme(suite, provisional, unavailable_rows), encoding="utf-8")
    hashes = {
        str(path.relative_to(output_dir)): file_sha256(path)
        for path in sorted(output_dir.rglob("*"))
        if path.is_file()
    }
    manifest = {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": _PROVISIONAL_STATUS if provisional else _DATA_ONLY_STATUS,
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
        "unavailable_systems": unavailable_rows,
        "files": hashes,
    }
    (output_dir / "publication.manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def sign_publication(bundle: Path, *, key: str | None = None) -> Path:
    unresolved = bundle.expanduser()
    verify_publication(unresolved)
    bundle = unresolved.resolve()
    manifest = bundle / "publication.manifest.json"
    verified_manifest_sha256 = file_sha256(manifest)
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
    try:
        if file_sha256(manifest) != verified_manifest_sha256:
            raise ValueError("publication manifest changed while it was being signed")
        verify_publication(bundle)
    except ValueError:
        signature.unlink(missing_ok=True)
        raise
    return signature


def verify_publication(
    bundle: Path,
    *,
    trusted_gpg_fingerprint: str | None = None,
) -> dict[str, str | int]:
    unresolved = bundle.expanduser()
    if unresolved.is_symlink():
        raise ValueError("publication bundle cannot be a symlink")
    bundle = unresolved.resolve()
    actual_files, actual_directories = _bundle_tree(bundle)
    manifest_path = bundle / "publication.manifest.json"
    try:
        manifest = strict_json_file(manifest_path)
        _validate_manifest(manifest)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("publication manifest is missing or invalid") from error

    expected_hashes: dict[str, str] = manifest["files"]
    expected_files = set(expected_hashes)
    if actual_files != expected_files:
        missing = sorted(expected_files - actual_files)
        extra = sorted(actual_files - expected_files)
        raise ValueError(f"publication file set mismatch; missing={missing} extra={extra}")
    expected_directories = {
        str(parent)
        for relative in expected_files
        for parent in PurePosixPath(relative).parents
        if str(parent) != "."
    }
    if actual_directories != expected_directories:
        missing = sorted(expected_directories - actual_directories)
        extra = sorted(actual_directories - expected_directories)
        raise ValueError(f"publication directory set mismatch; missing={missing} extra={extra}")
    for relative, expected in expected_hashes.items():
        path = bundle / relative
        if file_sha256(path) != expected:
            raise ValueError(f"publication file hash mismatch: {relative}")

    sources: list[dict[str, str]] = manifest["source_reports"]
    source_paths = {item["published_path"] for item in sources}
    if expected_files != _ROOT_FILES | source_paths:
        raise ValueError("publication manifest contains an unexpected file layout")
    report_paths = [bundle / item["published_path"] for item in sources]
    if len(report_paths) < 2:
        raise ValueError("publication must contain at least two portable reports")
    reports = [load_portable_report(path) for path in report_paths]
    reports_by_system = {report.manifest.system: report for report in reports}
    if len(reports_by_system) != len(reports):
        raise ValueError("publication contains duplicate report systems")
    for source in sources:
        system = source["system"]
        expected_path = f"reports/{_safe_name(system)}.report.json"
        if source["published_path"] != expected_path or system not in reports_by_system:
            raise ValueError("publication source report identity is invalid")
        if source["published_sha256"] != expected_hashes[source["published_path"]]:
            raise ValueError("publication source report hash conflicts with the file map")
    expected_source_set = _json_hash(sorted((item["system"], item["source_sha256"]) for item in sources))
    if manifest["source_report_set_sha256"] != expected_source_set:
        raise ValueError("publication source report set hash is invalid")

    comparison = compare_reports(report_paths)
    stored_comparison_raw = strict_json_file(bundle / "comparison.json")
    stored_comparison = ComparisonReport.model_validate(stored_comparison_raw)
    if comparison != stored_comparison:
        raise ValueError("publication comparison does not match regenerated report analysis")
    if (bundle / "COMPARISON.md").read_text(encoding="utf-8") != comparison_markdown(comparison):
        raise ValueError("publication comparison Markdown does not match regenerated analysis")

    suite_path = bundle / "suite.yaml"
    suite = BenchmarkSuite.from_yaml(suite_path)
    if file_sha256(suite_path) != manifest["suite_sha256"]:
        raise ValueError("publication suite hash does not match manifest")
    if comparison.suite_sha256 != manifest["suite_sha256"]:
        raise ValueError("publication reports do not match the bundled suite")
    derived_manifest = {
        "suite_name": suite.name,
        "suite_version": suite.version,
        "protocol_sha256": comparison.protocol_sha256,
        "scorer_version": comparison.scorer_version,
        "analysis_version": comparison.analysis_version,
    }
    for key, expected in derived_manifest.items():
        if manifest[key] != expected:
            raise ValueError(f"publication manifest {key} does not match regenerated evidence")

    unavailable_rows: list[dict[str, str]] = manifest["unavailable_systems"]
    provisional = manifest["status"] == _PROVISIONAL_STATUS
    if (bundle / "README.md").read_text(encoding="utf-8") != _readme(suite, provisional, unavailable_rows):
        raise ValueError("publication README does not match regenerated metadata")
    if (bundle / "METHODOLOGY.md").read_text(encoding="utf-8") != _methodology(
        suite,
        reports,
        comparison.protocol_sha256,
        comparison.analysis_version,
        provisional,
    ):
        raise ValueError("publication methodology does not match regenerated evidence")

    signer = "unsigned"
    signature = bundle / "publication.manifest.json.asc"
    if trusted_gpg_fingerprint:
        if not signature.is_file():
            raise ValueError("trusted GPG verification requires a detached publication signature")
        signer = _verify_gpg_signature(signature, manifest_path, trusted_gpg_fingerprint)
    return {"status": "valid", "file_count": len(actual_files), "signer": signer}


def _validate_manifest(manifest: Any) -> None:
    if not isinstance(manifest, dict) or set(manifest) != _MANIFEST_KEYS:
        raise ValueError("publication manifest schema is invalid")
    if manifest["schema_version"] != 3:
        raise ValueError("publication manifest must use schema version 3")
    if manifest["status"] not in {_DATA_ONLY_STATUS, _PROVISIONAL_STATUS}:
        raise ValueError("publication status is invalid")
    for key in ("suite_name", "suite_version", "scorer_version", "analysis_version"):
        if not isinstance(manifest[key], str) or not manifest[key]:
            raise ValueError(f"publication {key} is invalid")
    for key in ("suite_sha256", "protocol_sha256", "source_report_set_sha256"):
        if not _is_sha256(manifest[key]):
            raise ValueError(f"publication {key} is invalid")
    generated_at = manifest["generated_at"]
    if not isinstance(generated_at, str):
        raise ValueError("publication generation time is invalid")
    parsed_time = datetime.fromisoformat(generated_at)
    if parsed_time.tzinfo is None:
        raise ValueError("publication generation time must include a timezone")

    files = manifest["files"]
    if not isinstance(files, dict) or not files:
        raise ValueError("publication manifest file map is invalid")
    for relative, digest in files.items():
        _validate_relative_path(relative)
        if not _is_sha256(digest):
            raise ValueError("publication manifest file digest is invalid")

    sources = manifest["source_reports"]
    if not isinstance(sources, list) or len(sources) < 2:
        raise ValueError("publication source report list is invalid")
    systems: list[str] = []
    published_paths: list[str] = []
    for item in sources:
        if not isinstance(item, dict) or set(item) != _SOURCE_REPORT_KEYS:
            raise ValueError("publication source report entry is invalid")
        if not isinstance(item["system"], str) or not item["system"]:
            raise ValueError("publication source report system is invalid")
        _validate_relative_path(item["published_path"])
        if not _is_sha256(item["source_sha256"]) or not _is_sha256(item["published_sha256"]):
            raise ValueError("publication source report digest is invalid")
        systems.append(item["system"])
        published_paths.append(item["published_path"])
    if len(systems) != len(set(systems)) or len(published_paths) != len(set(published_paths)):
        raise ValueError("publication source reports must be unique")

    unavailable = manifest["unavailable_systems"]
    if not isinstance(unavailable, list):
        raise ValueError("publication unavailable system list is invalid")
    unavailable_systems: list[str] = []
    for item in unavailable:
        if (
            not isinstance(item, dict)
            or set(item) != _UNAVAILABLE_KEYS
            or not isinstance(item["system"], str)
            or not item["system"].strip()
            or not isinstance(item["reason"], str)
            or not item["reason"].strip()
        ):
            raise ValueError("publication unavailable system entry is invalid")
        unavailable_systems.append(item["system"])
    if len(unavailable_systems) != len(set(unavailable_systems)):
        raise ValueError("publication unavailable systems must be unique")
    if set(unavailable_systems) & set(systems):
        raise ValueError("published systems cannot also be unavailable")


def _bundle_tree(bundle: Path) -> tuple[set[str], set[str]]:
    if not bundle.is_dir():
        raise ValueError("publication bundle is not a directory")
    files: set[str] = set()
    directories: set[str] = set()
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise ValueError("publication bundle cannot contain symlinks")
        relative = str(path.relative_to(bundle))
        if path.is_file():
            if relative not in {"publication.manifest.json", "publication.manifest.json.asc"}:
                files.add(relative)
        elif path.is_dir():
            directories.add(relative)
        else:
            raise ValueError("publication bundle contains a non-regular entry")
    return files, directories


def _validate_relative_path(value: Any) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("publication path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("publication path is invalid")
    if value in {"publication.manifest.json", "publication.manifest.json.asc"}:
        raise ValueError("publication path cannot name an integrity sidecar")


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


def _readme(
    suite: BenchmarkSuite,
    provisional: bool,
    unavailable: list[dict[str, str]],
) -> str:
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
    if unavailable:
        lines.extend(["", "## Unavailable systems", "", "| System | Reason |", "| --- | --- |"])
        lines.extend(f"| {row['system']} | {row['reason']} |" for row in unavailable)
    return "\n".join(lines) + "\n"


def _parse_unavailable(values: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    systems: set[str] = set()
    for value in values:
        if "=" not in value:
            raise ValueError("--unavailable values must use SYSTEM=REASON")
        system, reason = (part.strip() for part in value.split("=", 1))
        if not system or not reason:
            raise ValueError("--unavailable requires both system and reason")
        if system in systems:
            raise ValueError("--unavailable systems must be unique")
        systems.add(system)
        rows.append({"system": system, "reason": reason})
    return rows


def _publication_report_names(reports: list[PortableReport]) -> dict[str, str]:
    names = {
        report.manifest.system: f"{_safe_name(report.manifest.system)}.report.json" for report in reports
    }
    if len(names) != len(reports):
        raise ValueError("publication accepts one report per system")
    if len(set(names.values())) != len(names):
        raise ValueError("publication system names collide as portable filenames")
    return names


def _safe_name(value: str) -> str:
    value = _SAFE_NAME.sub("-", value.casefold()).strip("-")
    if not value:
        raise ValueError("system name cannot be used as a filename")
    return value


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _json_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
