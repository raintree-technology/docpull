"""Verify rights-safe generated fixture manifests and resolve fixture inputs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from .models import BenchmarkInput, CrawlInput, ExtractInput

FIXTURE_URL_PREFIX = "https://raintree-technology.github.io/docpull/bench-fixtures/"
FIXTURES_ROOT = Path(__file__).resolve().parents[2] / "fixtures"


def fixture_path_for_url(url: str) -> Path | None:
    """Resolve a hosted controlled-corpus URL to its committed fixture file."""
    if not url.startswith(FIXTURE_URL_PREFIX):
        return None
    relative = url[len(FIXTURE_URL_PREFIX) :].partition("?")[0].partition("#")[0]
    root = FIXTURES_ROOT.resolve()
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate if candidate.is_file() else None


def fixture_html_inputs(inputs: BenchmarkInput) -> list[Path]:
    """Committed HTML files forming a case's raw input, when the case maps to fixtures."""
    if isinstance(inputs, ExtractInput):
        path = fixture_path_for_url(inputs.url)
        return [path] if path is not None else []
    if isinstance(inputs, CrawlInput):
        start = fixture_path_for_url(inputs.url)
        if start is None:
            return []
        return sorted(path for path in start.parent.glob("*.html") if path.is_file())
    return []


def verify_fixture_manifest(manifest_path: Path) -> dict[str, Any]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("fixture manifest is not schema v1")
    root = manifest_path.parent
    errors: list[str] = []
    for entry in payload.get("files", []):
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            errors.append(f"unsafe path: {relative}")
            continue
        path = root / relative
        if not path.is_file():
            errors.append(f"missing: {relative}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            errors.append(f"hash mismatch: {relative}")
        if path.stat().st_size != entry["bytes"]:
            errors.append(f"size mismatch: {relative}")
    if errors:
        raise ValueError("fixture verification failed: " + "; ".join(errors))
    return cast(dict[str, Any], payload)
