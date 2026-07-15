"""Verify rights-safe generated fixture manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast


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
