"""Private subprocess entry point for isolated remote-document parsing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import unquote, urlparse

from .document_parse import (
    DocumentParseError,
    ParseBackend,
    _apply_resource_limits,
    _reject_duplicate_json_keys,
    _reject_nonstandard_json_constant,
    parse_one_document,
)


class _WorkerRequest(TypedDict):
    backend: ParseBackend
    max_output_bytes: int
    memory_mib: int
    source_url: str
    timeout_seconds: int


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--source", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    source_path = Path(args.source)
    result_path = Path(args.result)
    source_url = ""
    try:
        request = _read_request(Path(args.request))
        source_url = request["source_url"]
        _apply_resource_limits(
            request["timeout_seconds"],
            request["memory_mib"],
            request["max_output_bytes"],
        )
        parsed = parse_one_document(
            source_path,
            backend=request["backend"],
            source_url=source_url,
            title=_source_title(source_url),
        )
        payload = {
            "backend": parsed.backend,
            "content": parsed.content,
            "error_code": "",
            "metadata": parsed.metadata,
            "source_mime_type": parsed.source_mime_type,
            "source_url": parsed.source_url,
            "status": "ok",
            "title": parsed.title,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if (
            len(parsed.content.encode("utf-8")) > request["max_output_bytes"]
            or len(serialized) > request["max_output_bytes"]
        ):
            raise DocumentParseError("Parsed output exceeded its limit.", code="output_limit")
    except Exception as err:  # noqa: BLE001
        payload = {
            "backend": "",
            "content": "",
            "error_code": err.code if isinstance(err, DocumentParseError) else "worker_failure",
            "metadata": {},
            "source_mime_type": "application/pdf",
            "source_url": source_url,
            "status": "error",
            "title": "",
        }
        serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    _write_private(result_path, serialized)
    return 0 if payload["status"] == "ok" else 1


def _read_request(path: Path) -> _WorkerRequest:
    payload = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_nonstandard_json_constant,
    )
    if not isinstance(payload, dict) or set(payload) != {
        "backend",
        "max_output_bytes",
        "memory_mib",
        "source_url",
        "timeout_seconds",
    }:
        raise DocumentParseError("Invalid worker request.")
    backend = payload.get("backend")
    maximum = payload.get("max_output_bytes")
    memory_mib = payload.get("memory_mib")
    source_url = payload.get("source_url")
    timeout_seconds = payload.get("timeout_seconds")
    if backend not in {"auto", "pypdf", "markitdown", "unstructured"}:
        raise DocumentParseError("Invalid worker backend.")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise DocumentParseError("Invalid worker output limit.")
    if not isinstance(memory_mib, int) or isinstance(memory_mib, bool) or memory_mib < 64:
        raise DocumentParseError("Invalid worker memory limit.")
    if not isinstance(source_url, str) or not source_url:
        raise DocumentParseError("Invalid worker source URL.")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise DocumentParseError("Invalid worker timeout.")
    return {
        "backend": cast(ParseBackend, backend),
        "max_output_bytes": maximum,
        "memory_mib": memory_mib,
        "source_url": source_url,
        "timeout_seconds": timeout_seconds,
    }


def _source_title(source_url: str) -> str | None:
    filename = Path(unquote(urlparse(source_url).path)).stem.strip()
    return filename or None


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


if __name__ == "__main__":
    raise SystemExit(main())
