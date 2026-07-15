"""Private subprocess entry point for isolated remote-document parsing."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import TypedDict, cast
from urllib.parse import unquote, urlparse

from .document_parse import DocumentParseError, ParseBackend, parse_one_document


class _WorkerRequest(TypedDict):
    backend: ParseBackend
    max_output_bytes: int
    source_url: str


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
            raise DocumentParseError("Parsed output exceeded its limit.")
    except Exception as err:  # noqa: BLE001
        payload = {
            "backend": "",
            "content": "",
            "error_code": _safe_error_code(err),
            "metadata": {},
            "source_mime_type": "application/pdf",
            "source_url": source_url,
            "status": "error",
            "title": "",
        }
        serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    _write_private(result_path, serialized)
    return 0 if payload["status"] == "ok" else 1


def _safe_error_code(error: Exception) -> str:
    message = str(error).casefold()
    if "encrypted pdf" in message:
        return "encrypted"
    if "image-only" in message:
        return "image_only"
    if "no extractable text" in message or "no text" in message or "no pages" in message:
        return "empty"
    if "malformed" in message or "truncated" in message:
        return "malformed"
    if "output" in message and "limit" in message:
        return "output_limit"
    return "worker_failure"


def _read_request(path: Path) -> _WorkerRequest:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"backend", "max_output_bytes", "source_url"}:
        raise DocumentParseError("Invalid worker request.")
    backend = payload.get("backend")
    maximum = payload.get("max_output_bytes")
    source_url = payload.get("source_url")
    if backend not in {"auto", "pypdf", "markitdown", "unstructured"}:
        raise DocumentParseError("Invalid worker backend.")
    if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
        raise DocumentParseError("Invalid worker output limit.")
    if not isinstance(source_url, str) or not source_url:
        raise DocumentParseError("Invalid worker source URL.")
    return {
        "backend": cast(ParseBackend, backend),
        "max_output_bytes": maximum,
        "source_url": source_url,
    }


def _source_title(source_url: str) -> str | None:
    filename = Path(unquote(urlparse(source_url).path)).stem.strip()
    return filename or None


def _write_private(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)


if __name__ == "__main__":
    raise SystemExit(main())
