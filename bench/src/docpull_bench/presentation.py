"""Render deterministic presentation metadata for immutable publication bundles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("publication manifest must be an object")
    return payload


def verify_legacy_publication(bundle: Path) -> dict[str, Any]:
    """Verify the committed file map without upgrading historical semantics."""
    bundle = bundle.resolve()
    manifest = _load_manifest(bundle / "publication.manifest.json")
    schema_version = manifest.get("schema_version")
    files = manifest.get("files")
    if schema_version not in {1, 2, 3} or not isinstance(files, dict) or not files:
        raise ValueError("historical publication manifest schema is invalid")
    for relative, expected_digest in files.items():
        if not isinstance(relative, str) or not isinstance(expected_digest, str):
            raise ValueError("historical publication file map is invalid")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("historical publication file path is unsafe")
        source = bundle / path
        if not source.is_file() or _sha256(source) != expected_digest:
            raise ValueError(f"historical publication file changed: {relative}")
    return {"status": "valid", "schema_version": schema_version, "file_count": len(files)}


def _present_source(readme: str, source_bundle: str) -> str:
    """Demote source headings and keep its relative links pointed at the source."""
    readme = re.sub(r"^(#{1,5}) ", lambda match: f"#{match.group(1)} ", readme, flags=re.MULTILINE)

    def rewrite_link(match: re.Match[str]) -> str:
        label, destination = match.groups()
        if destination.startswith(("#", "http://", "https://", "mailto:")):
            return match.group(0)
        return f"[{label}]({source_bundle}/{destination})"

    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", rewrite_link, readme)


def _summary(*, source_bundle: str, source_digest: str, source_manifest: dict[str, Any], readme: str) -> str:
    schema = source_manifest.get("schema_version", "unknown")
    status = source_manifest.get("status", "historical")
    return (
        "# Historical benchmark presentation\n\n"
        "**Presentation only — this is not new experimental evidence.**\n\n"
        f"This page presents the committed [source bundle]({source_bundle}/README.md) "
        "without changing it. Interpret results under the source bundle’s methodology, "
        "status, and limitations.\n\n"
        f"- Source publication schema: `{schema}`\n"
        f"- Source status: `{status}`\n"
        f"- Source manifest SHA-256: `{source_digest}`\n\n"
        "## Source bundle summary\n\n"
        f"{_present_source(readme, source_bundle).rstrip()}\n"
    )


def create_presentation(bundle: Path, *, output_dir: Path) -> Path:
    bundle = bundle.resolve()
    output_dir = output_dir.resolve()
    manifest_path = bundle / "publication.manifest.json"
    readme_path = bundle / "README.md"
    if not manifest_path.is_file() or not readme_path.is_file():
        raise ValueError("source bundle must contain README.md and publication.manifest.json")
    verify_legacy_publication(bundle)
    refreshing = output_dir.exists()
    if refreshing:
        existing = _load_manifest(output_dir / "presentation.manifest.json")
        if existing.get("kind") != "historical-publication-presentation":
            raise ValueError(f"refusing to replace non-presentation output: {output_dir}")

    source_bundle = Path(os.path.relpath(bundle, output_dir)).as_posix()
    source_manifest = _load_manifest(manifest_path)
    source_digest = _sha256(manifest_path)
    summary = _summary(
        source_bundle=source_bundle,
        source_digest=source_digest,
        source_manifest=source_manifest,
        readme=readme_path.read_text(encoding="utf-8"),
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        summary_path = staging / "SUMMARY.md"
        summary_path.write_text(summary, encoding="utf-8")
        presentation_manifest = {
            "schema_version": 4,
            "kind": "historical-publication-presentation",
            "source_bundle": source_bundle,
            "source_manifest_sha256": source_digest,
            "source_publication_schema": source_manifest.get("schema_version"),
            "source_status": source_manifest.get("status", "historical"),
            "files": {"SUMMARY.md": _sha256(summary_path)},
        }
        (staging / "presentation.manifest.json").write_text(
            json.dumps(presentation_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if refreshing:
            summary_path.replace(output_dir / "SUMMARY.md")
            (staging / "presentation.manifest.json").replace(output_dir / "presentation.manifest.json")
            staging.rmdir()
        else:
            staging.replace(output_dir)
    except BaseException:
        for path in staging.glob("*"):
            path.unlink()
        staging.rmdir()
        raise
    return output_dir


def verify_presentation(output_dir: Path) -> dict[str, Any]:
    output_dir = output_dir.resolve()
    manifest = _load_manifest(output_dir / "presentation.manifest.json")
    if manifest.get("schema_version") != 4 or manifest.get("kind") != "historical-publication-presentation":
        raise ValueError("presentation manifest schema is invalid")

    source_bundle = (output_dir / str(manifest.get("source_bundle"))).resolve()
    source_manifest_path = source_bundle / "publication.manifest.json"
    source_readme_path = source_bundle / "README.md"
    source_digest = _sha256(source_manifest_path)
    if source_digest != manifest.get("source_manifest_sha256"):
        raise ValueError("source publication manifest changed")

    source_manifest = _load_manifest(source_manifest_path)
    expected = _summary(
        source_bundle=str(manifest["source_bundle"]),
        source_digest=source_digest,
        source_manifest=source_manifest,
        readme=source_readme_path.read_text(encoding="utf-8"),
    )
    summary_path = output_dir / "SUMMARY.md"
    if summary_path.read_text(encoding="utf-8") != expected:
        raise ValueError("presentation summary does not match the source bundle")
    if manifest.get("files") != {"SUMMARY.md": _sha256(summary_path)}:
        raise ValueError("presentation file map is invalid")

    return {"status": "valid", "source_manifest_sha256": source_digest}
