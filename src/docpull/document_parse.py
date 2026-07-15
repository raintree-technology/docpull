"""Local document parsing workflow for v3 context packs."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import importlib
import json
import math
import mimetypes
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

from .conversion.chunking import TokenCounter, chunk_markdown
from .models.document import DocumentRecord
from .output_contract import default_rights_state, validate_pack_contract, validation_report_text
from .pipeline.manifest import CorpusManifest
from .time_utils import utc_now_iso

ParseBackend = Literal["auto", "pypdf", "markitdown", "unstructured", "text"]
RemoteDocumentErrorCode = Literal[
    "encrypted",
    "empty",
    "image_only",
    "malformed",
    "output_limit",
    "worker_failure",
]

REMOTE_DOCUMENT_MAX_INPUT_BYTES = 50 * 1024 * 1024
REMOTE_DOCUMENT_MAX_OUTPUT_BYTES = 100 * 1024 * 1024
REMOTE_DOCUMENT_DEFAULT_TIMEOUT_SECONDS = 60
REMOTE_DOCUMENT_DEFAULT_MEMORY_MIB = 1024
_REMOTE_RESULT_REQUIRED_KEYS = {
    "backend",
    "content",
    "error_code",
    "metadata",
    "source_mime_type",
    "source_url",
    "status",
    "title",
}
_REMOTE_BACKENDS = {"auto", "pypdf", "markitdown", "unstructured"}
_REMOTE_ERROR_CODES = {
    "encrypted",
    "empty",
    "image_only",
    "malformed",
    "output_limit",
    "worker_failure",
}
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_ALPHA_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

TEXT_COMPATIBLE_SUFFIXES = {
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".jsonl",
    ".markdown",
    ".md",
    ".ndjson",
    ".rst",
    ".text",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
TEXT_COMPATIBLE_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "application/x-ndjson",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
}


class DocumentParseError(RuntimeError):
    """Raised when a local document cannot be parsed into a pack."""

    def __init__(
        self,
        message: str,
        *,
        code: RemoteDocumentErrorCode = "worker_failure",
    ) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedDocument:
    """Markdown content produced by a parser backend."""

    path: Path
    source_url: str
    title: str
    content: str
    backend: str
    source_mime_type: str
    metadata: dict[str, Any]


def parse_documents(
    paths: Sequence[str | Path],
    output_dir: str | Path,
    *,
    backend: ParseBackend = "auto",
    source_url: str | None = None,
    title: str | None = None,
    emit_chunks: bool = True,
    chunk_tokens: int = 4000,
    validate_level: Literal["raw", "agent", "eval"] = "raw",
) -> dict[str, Any]:
    """Parse local files into a DocPull output contract v3 pack."""
    input_paths = [_resolve_input_path(path) for path in paths]
    if not input_paths:
        raise DocumentParseError("At least one input file is required.")
    if source_url and len(input_paths) != 1:
        raise DocumentParseError("--source-url can only be used with one input file.")
    if title and len(input_paths) != 1:
        raise DocumentParseError("--title can only be used with one input file.")
    if chunk_tokens <= 0:
        raise DocumentParseError("--chunk-tokens must be greater than zero.")

    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_dir = output_root / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)

    manifest = CorpusManifest(output_root, output_format="document-parse")
    counter = TokenCounter()
    records: list[DocumentRecord] = []
    parsed_documents: list[ParsedDocument] = []

    for index, input_path in enumerate(input_paths, start=1):
        parsed = parse_one_document(
            input_path,
            backend=backend,
            source_url=source_url or input_path.as_uri(),
            title=title,
        )
        parsed_documents.append(parsed)
        markdown_path = source_dir / f"{index:03d}-{_safe_slug(input_path.stem) or 'document'}.md"
        markdown_path.write_text(parsed.content.rstrip() + "\n", encoding="utf-8")
        emitted = _records_for_parsed_document(
            parsed,
            document_index=index,
            emit_chunks=emit_chunks,
            chunk_tokens=chunk_tokens,
            counter=counter,
        )
        for record in emitted:
            records.append(record)
            manifest.add_record(record, markdown_path)

    documents_path = output_root / "documents.ndjson"
    documents_path.write_text(
        "".join(
            json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    manifest_path = manifest.finalize()

    parse_result = _parse_result_payload(
        output_root=output_root,
        requested_backend=backend,
        parsed_documents=parsed_documents,
        records=records,
        emit_chunks=emit_chunks,
        chunk_tokens=chunk_tokens,
        artifacts={
            "documents": documents_path,
            "manifest": manifest_path,
            "sources": output_root / "sources.md",
            "acquisition_routes": output_root / "acquisition.routes.json",
        },
    )
    result_path = output_root / "parse.result.json"
    result_path.write_text(json.dumps(parse_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    validation = validate_pack_contract(output_root, level=validate_level)
    parse_result["validation"] = validation
    result_path.write_text(json.dumps(parse_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return parse_result


def run_parse_cli(argv: list[str]) -> int:
    """Run the ``docpull parse`` CLI."""
    parser = _create_parse_parser()
    args = parser.parse_args(argv)
    validate_level: Literal["raw", "agent", "eval"] = "raw"
    if args.eval_grade:
        validate_level = "eval"
    elif args.prepare:
        validate_level = "agent"

    try:
        result = parse_documents(
            args.paths,
            args.output_dir,
            backend=args.backend,
            source_url=args.source_url,
            title=args.title,
            emit_chunks=not args.no_chunks,
            chunk_tokens=args.chunk_tokens,
            validate_level="raw",
        )
        if args.prepare or args.eval_grade:
            from .pack_tools import prepare_pack

            pack_dir = Path(args.output_dir).expanduser().resolve()
            prepare_pack(
                pack_dir,
                default_search=False,
                graph=False,
                eval_grade=bool(args.eval_grade),
            )
            result["validation"] = validate_pack_contract(pack_dir, level=validate_level)
            result["prepared_level"] = validate_level
            result_path = pack_dir / "parse.result.json"
            result_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    except DocumentParseError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1

    validation = result.get("validation")
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(_result_text(result))
        if isinstance(validation, dict) and validation.get("status") != "pass":
            print(validation_report_text(validation), file=sys.stderr)

    return 0 if isinstance(validation, dict) and validation.get("status") == "pass" else 1


def _create_parse_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull parse",
        description="Parse local files into a DocPull output contract v3 pack",
    )
    parser.add_argument("paths", nargs="+", help="Local files to parse")
    parser.add_argument("-o", "--output-dir", required=True, help="Directory for the generated pack")
    parser.add_argument(
        "--backend",
        choices=["auto", "pypdf", "markitdown", "unstructured", "text"],
        default="auto",
        help="Parser backend (default: auto)",
    )
    parser.add_argument("--source-url", help="Override source URL for a single input")
    parser.add_argument("--title", help="Override title for a single input")
    parser.add_argument(
        "--no-chunks",
        action="store_true",
        help="Write one record per input file instead of token-bounded chunk records",
    )
    parser.add_argument(
        "--chunk-tokens",
        type=int,
        default=4000,
        metavar="TOKENS",
        help="Soft token budget per emitted chunk when chunking is enabled",
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Also write agent-level sidecars after parsing",
    )
    parser.add_argument(
        "--eval-grade",
        action="store_true",
        help="Also write eval-grade sidecars after parsing",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="CLI result format (default: text)",
    )
    return parser


def parse_one_document(
    path: Path,
    *,
    backend: ParseBackend,
    source_url: str,
    title: str | None,
) -> ParsedDocument:
    """Parse one local file into normalized Markdown without writing a pack."""
    source_mime_type = _guess_mime_type(path)
    if backend == "text":
        content, metadata = _parse_text(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title,
            content=content,
            backend="text",
            source_mime_type=source_mime_type,
            metadata=metadata,
        )
    if backend == "pypdf":
        if source_mime_type != "application/pdf":
            raise DocumentParseError("The pypdf backend only supports PDF inputs.")
        content, metadata = _parse_pypdf(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title or _metadata_title(metadata),
            content=content,
            backend="pypdf",
            source_mime_type=source_mime_type,
            metadata=metadata,
        )
    if backend == "markitdown":
        structure = _validate_pdf_structure(path) if source_mime_type == "application/pdf" else {}
        content, metadata = _parse_markitdown(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title or _metadata_title(metadata),
            content=content,
            backend="markitdown",
            source_mime_type=source_mime_type,
            metadata={**structure, **metadata},
        )
    if backend == "unstructured":
        structure = _validate_pdf_structure(path) if source_mime_type == "application/pdf" else {}
        content, metadata = _parse_unstructured(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title or _metadata_title(metadata),
            content=content,
            backend="unstructured",
            source_mime_type=source_mime_type,
            metadata={**structure, **metadata},
        )
    return _parse_auto(path, source_url=source_url, title=title, source_mime_type=source_mime_type)


def parse_remote_document_bytes(
    body: bytes,
    *,
    source_url: str,
    content_type: str,
    backend: ParseBackend = "auto",
    timeout_seconds: int = REMOTE_DOCUMENT_DEFAULT_TIMEOUT_SECONDS,
    memory_mib: int = REMOTE_DOCUMENT_DEFAULT_MEMORY_MIB,
) -> ParsedDocument:
    """Parse remote document bytes in a dedicated, resource-bounded subprocess."""
    prepared = _prepare_remote_document(
        body,
        source_url=source_url,
        content_type=content_type,
        backend=backend,
        timeout_seconds=timeout_seconds,
        memory_mib=memory_mib,
    )
    with prepared as request:
        command = _remote_worker_command(request)
        process = subprocess.Popen(  # noqa: S603
            command,
            env=_remote_worker_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as err:
            _terminate_process_group(process.pid)
            process.communicate()
            raise DocumentParseError("Remote document parsing exceeded its wall-time limit.") from err
        if process.returncode != 0 and not request.result_path.exists():
            raise DocumentParseError("Remote document worker failed safely.")
        return _read_remote_worker_result(request)


async def parse_remote_document_bytes_async(
    body: bytes,
    *,
    source_url: str,
    content_type: str,
    backend: ParseBackend = "auto",
    timeout_seconds: int = REMOTE_DOCUMENT_DEFAULT_TIMEOUT_SECONDS,
    memory_mib: int = REMOTE_DOCUMENT_DEFAULT_MEMORY_MIB,
) -> ParsedDocument:
    """Asynchronously supervise the dedicated remote-document worker."""
    prepared = _prepare_remote_document(
        body,
        source_url=source_url,
        content_type=content_type,
        backend=backend,
        timeout_seconds=timeout_seconds,
        memory_mib=memory_mib,
    )
    with prepared as request:
        process = await asyncio.create_subprocess_exec(
            *_remote_worker_command(request),
            env=_remote_worker_environment(),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.CancelledError:
            _terminate_process_group(process.pid)
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
        except TimeoutError as err:
            _terminate_process_group(process.pid)
            with contextlib.suppress(ProcessLookupError):
                await process.wait()
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise DocumentParseError("Remote document parsing exceeded its wall-time limit.") from err
        if process.returncode != 0 and not request.result_path.exists():
            raise DocumentParseError("Remote document worker failed safely.")
        return _read_remote_worker_result(request)


@dataclass
class _RemoteDocumentRequest:
    temporary_directory: tempfile.TemporaryDirectory[str]
    source_path: Path
    request_path: Path
    result_path: Path
    source_url: str
    media_type: str
    backend: ParseBackend
    source_bytes: int
    source_sha256: str

    def __enter__(self) -> _RemoteDocumentRequest:
        return self

    def __exit__(self, *_args: object) -> None:
        self.temporary_directory.cleanup()


def _prepare_remote_document(
    body: bytes,
    *,
    source_url: str,
    content_type: str,
    backend: ParseBackend,
    timeout_seconds: int,
    memory_mib: int,
) -> _RemoteDocumentRequest:
    if not isinstance(body, bytes):
        raise DocumentParseError("Remote document body must be bytes.")
    if not isinstance(content_type, str):
        raise DocumentParseError("Remote document content type must be a string.")
    media_type = content_type.split(";", 1)[0].strip().casefold()
    if media_type != "application/pdf":
        raise DocumentParseError(f"Unsupported remote document content type: {media_type or 'unknown'}")
    if len(body) > REMOTE_DOCUMENT_MAX_INPUT_BYTES:
        raise DocumentParseError("Remote PDF exceeds the 50 MiB input limit.")
    if not body.startswith(b"%PDF-"):
        raise DocumentParseError("Remote PDF response did not contain a PDF signature.")
    if not isinstance(backend, str) or backend not in _REMOTE_BACKENDS:
        raise DocumentParseError("Invalid remote document backend.")
    if not isinstance(source_url, str) or not source_url.strip() or source_url != source_url.strip():
        raise DocumentParseError("Remote document source URL must not be empty.")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise DocumentParseError("Remote document timeout must be greater than zero.")
    if not isinstance(memory_mib, int) or isinstance(memory_mib, bool) or memory_mib < 64:
        raise DocumentParseError("Remote document memory limit must be at least 64 MiB.")

    temporary_directory = tempfile.TemporaryDirectory(prefix="docpull-remote-document-")
    root = Path(temporary_directory.name)
    os.chmod(root, 0o700)
    source_path = root / "source.pdf"
    request_path = root / "request.json"
    result_path = root / "result.json"
    _write_private_bytes(source_path, body)
    _write_private_bytes(
        request_path,
        json.dumps(
            {
                "backend": backend,
                "max_output_bytes": REMOTE_DOCUMENT_MAX_OUTPUT_BYTES,
                "memory_mib": memory_mib,
                "source_url": source_url,
                "timeout_seconds": timeout_seconds,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8"),
    )
    return _RemoteDocumentRequest(
        temporary_directory=temporary_directory,
        source_path=source_path,
        request_path=request_path,
        result_path=result_path,
        source_url=source_url,
        media_type=media_type,
        backend=backend,
        source_bytes=len(body),
        source_sha256=hashlib.sha256(body).hexdigest(),
    )


def _remote_worker_command(request: _RemoteDocumentRequest) -> list[str]:
    return [
        sys.executable,
        "-m",
        "docpull.document_worker",
        "--source",
        str(request.source_path),
        "--result",
        str(request.result_path),
        "--request",
        str(request.request_path),
    ]


def _remote_worker_environment() -> dict[str, str]:
    allowed = {
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PATH",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    return environment


def _apply_resource_limits(timeout_seconds: int, memory_mib: int, maximum_output_bytes: int) -> None:
    if os.name != "posix":
        return
    import resource

    cpu_seconds = max(1, math.ceil(timeout_seconds))
    memory_bytes = memory_mib * 1024 * 1024
    with contextlib.suppress(OSError, ValueError):
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if hasattr(resource, "RLIMIT_AS"):
        with contextlib.suppress(OSError, ValueError):
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    if hasattr(resource, "RLIMIT_FSIZE"):
        with contextlib.suppress(OSError, ValueError):
            resource.setrlimit(resource.RLIMIT_FSIZE, (maximum_output_bytes, maximum_output_bytes))


def _terminate_process_group(pid: int) -> None:
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
    else:
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)


def _read_remote_worker_result(request: _RemoteDocumentRequest) -> ParsedDocument:
    try:
        raw_result = _read_private_result(request.result_path, REMOTE_DOCUMENT_MAX_OUTPUT_BYTES)
    except _RemoteResultTooLargeError as err:
        raise DocumentParseError(
            "Remote document parsed output exceeds the 100 MiB limit.",
            code="output_limit",
        ) from err
    except OSError as err:
        raise DocumentParseError("Remote document worker returned no validated result.") from err
    try:
        payload = json.loads(
            raw_result.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
        raise DocumentParseError("Remote document worker returned an invalid result.") from err
    if not isinstance(payload, dict) or set(payload) != _REMOTE_RESULT_REQUIRED_KEYS:
        raise DocumentParseError("Remote document worker returned an invalid result schema.")
    status = payload.get("status")
    if status not in {"ok", "error"}:
        raise DocumentParseError("Remote document worker returned an invalid result status.")
    if (
        payload.get("source_url") != request.source_url
        or payload.get("source_mime_type") != request.media_type
    ):
        raise DocumentParseError("Remote document worker returned conflicting source identity.")
    if status == "error":
        if (
            payload.get("backend") != ""
            or payload.get("content") != ""
            or payload.get("metadata") != {}
            or payload.get("title") != ""
            or payload.get("error_code") not in _REMOTE_ERROR_CODES
        ):
            raise DocumentParseError("Remote document worker returned an invalid error result.")
        messages = {
            "encrypted": "Encrypted PDF input is not supported.",
            "empty": "PDF contains no extractable text.",
            "image_only": "PDF appears image-only and requires an explicitly configured OCR backend.",
            "malformed": "Malformed or truncated PDF container.",
            "output_limit": "Remote document parsed output exceeds the 100 MiB limit.",
        }
        error_code = payload["error_code"]
        raise DocumentParseError(
            messages.get(error_code, "Remote document worker failed safely."),
            code=error_code,
        )
    content = payload.get("content")
    metadata = payload.get("metadata")
    title = payload.get("title")
    parsed_backend = payload.get("backend")
    if (
        not isinstance(content, str)
        or not content.strip()
        or not isinstance(title, str)
        or not title.strip()
        or not isinstance(parsed_backend, str)
        or payload.get("error_code") != ""
    ):
        raise DocumentParseError("Remote document worker returned invalid field types.")
    if not isinstance(metadata, dict) or not all(isinstance(key, str) for key in metadata):
        raise DocumentParseError("Remote document worker returned invalid metadata.")
    if parsed_backend not in {"pypdf", "markitdown", "unstructured"}:
        raise DocumentParseError("Remote document worker returned an invalid parser identity.")
    if request.backend != "auto" and parsed_backend != request.backend:
        raise DocumentParseError("Remote document worker returned a conflicting parser identity.")
    if len(content.encode("utf-8")) > REMOTE_DOCUMENT_MAX_OUTPUT_BYTES:
        raise DocumentParseError("Remote document parsed output exceeds the 100 MiB limit.")
    return ParsedDocument(
        path=Path("remote.pdf"),
        source_url=request.source_url,
        title=title,
        content=content,
        backend=parsed_backend,
        source_mime_type=request.media_type,
        metadata={
            **metadata,
            "remote_source_retained": False,
            "source_bytes": request.source_bytes,
            "source_sha256": request.source_sha256,
        },
    )


class _RemoteResultTooLargeError(OSError):
    """Raised before an oversized worker result is read into memory."""


def _read_private_result(path: Path, maximum_bytes: int) -> bytes:
    file_stat = path.lstat()
    if not stat.S_ISREG(file_stat.st_mode) or not _is_private_file_mode(file_stat.st_mode):
        raise OSError("worker result is not a private regular file")
    if file_stat.st_size > maximum_bytes:
        raise _RemoteResultTooLargeError
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened_stat = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened_stat.st_mode) or not _is_private_file_mode(opened_stat.st_mode):
            raise OSError("worker result changed during validation")
        if (opened_stat.st_dev, opened_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
            raise OSError("worker result changed during validation")
        body = stream.read(maximum_bytes + 1)
    if len(body) > maximum_bytes:
        raise _RemoteResultTooLargeError
    return body


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> NoReturn:
    raise ValueError(f"non-standard JSON constant: {value}")


def _is_private_file_mode(mode: int) -> bool:
    return os.name != "posix" or stat.S_IMODE(mode) == 0o600


def _write_private_bytes(path: Path, body: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)


_parse_one = parse_one_document


def _parse_auto(path: Path, *, source_url: str, title: str | None, source_mime_type: str) -> ParsedDocument:
    if _is_text_compatible(path, source_mime_type):
        content, metadata = _parse_text(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title,
            content=content,
            backend="text",
            source_mime_type=source_mime_type,
            metadata=metadata,
        )

    errors: list[str] = []
    pdf_metadata: dict[str, Any] = {}
    if source_mime_type == "application/pdf":
        try:
            content, pdf_metadata = _parse_pypdf(path)
        except DocumentParseError as err:
            if err.code in {"encrypted", "malformed", "empty"}:
                raise
            errors.append(str(err))
        else:
            if content.strip():
                return _parsed_document(
                    path,
                    source_url=source_url,
                    title=title or _metadata_title(pdf_metadata),
                    content=content,
                    backend="pypdf",
                    source_mime_type=source_mime_type,
                    metadata=pdf_metadata,
                )
            errors.append("pypdf produced no text")
    for backend in ("markitdown", "unstructured"):
        try:
            if backend == "markitdown":
                content, metadata = _parse_markitdown(path)
            else:
                content, metadata = _parse_unstructured(path)
            parsed = _parsed_document(
                path,
                source_url=source_url,
                title=title or _metadata_title(metadata) or _metadata_title(pdf_metadata),
                content=content,
                backend=backend,
                source_mime_type=source_mime_type,
                metadata={**pdf_metadata, **metadata},
            )
        except DocumentParseError as err:
            errors.append(str(err))
            continue
        return parsed

    if source_mime_type == "application/pdf" and pdf_metadata:
        if int(pdf_metadata.get("image_count", 0)) > 0:
            raise DocumentParseError(
                "PDF appears image-only and requires an explicitly configured OCR backend.",
                code="image_only",
            )
        raise DocumentParseError("PDF contains no extractable text.", code="empty")

    install_hint = (
        "Install an optional parser with `pip install 'docpull[pdf]'`, "
        "`pip install 'docpull[markitdown]'`, "
        "`pip install 'docpull[unstructured]'`, or `pip install 'docpull[parse]'`."
    )
    detail = " ".join(errors)
    raise DocumentParseError(f"No parser backend was available for {path}. {install_hint} {detail}".strip())


def _parse_text(path: Path) -> tuple[str, dict[str, Any]]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        encoding = "utf-8-replacement"
    return text, {"encoding": encoding, "bytes_read": len(raw)}


def _parse_pypdf(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        module = importlib.import_module("pypdf")
    except ImportError as err:
        raise DocumentParseError(
            "pypdf parsing requires the optional dependency. Install it with `pip install 'docpull[pdf]'`."
        ) from err
    reader_class = getattr(module, "PdfReader", None)
    if reader_class is None:
        raise DocumentParseError("Installed pypdf package does not expose PdfReader.")
    try:
        reader = reader_class(str(path), strict=False)
        if bool(reader.is_encrypted):
            raise DocumentParseError("Encrypted PDF input is not supported.", code="encrypted")
        pages = list(reader.pages)
    except DocumentParseError:
        raise
    except Exception as err:  # noqa: BLE001
        raise DocumentParseError("Malformed or truncated PDF container.", code="malformed") from err
    if not pages:
        raise DocumentParseError("PDF contains no pages.", code="empty")

    blocks: list[str] = []
    warnings: list[str] = []
    image_count = 0
    for page_index, page in enumerate(pages, start=1):
        image_count += _pypdf_page_image_count(page)
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001
            warnings.append(f"page {page_index}: text extraction failed")
            continue
        if text.strip():
            blocks.append(text.strip())
    metadata: dict[str, Any] = {
        "bytes_read": path.stat().st_size,
        "page_count": len(pages),
        "image_count": image_count,
        "extraction_warnings": warnings,
    }
    document_metadata = getattr(reader, "metadata", None)
    metadata_title = getattr(document_metadata, "title", None)
    if isinstance(metadata_title, str) and metadata_title.strip():
        metadata["result_title"] = metadata_title.strip()
    return "\n\n".join(blocks), metadata


def _validate_pdf_structure(path: Path) -> dict[str, Any]:
    content, metadata = _parse_pypdf(path)
    if not content.strip() and int(metadata.get("image_count", 0)) > 0:
        raise DocumentParseError(
            "PDF appears image-only and requires an explicitly configured OCR backend.",
            code="image_only",
        )
    return metadata


def _pypdf_page_image_count(page: Any) -> int:
    try:
        resources = page.get("/Resources") or {}
        xobjects = resources.get("/XObject") or {}
        return sum(1 for value in xobjects.values() if value.get_object().get("/Subtype") == "/Image")
    except Exception:  # noqa: BLE001
        return 0


def _parse_markitdown(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        module = importlib.import_module("markitdown")
    except ImportError as err:
        raise DocumentParseError(
            "MarkItDown parsing requires the optional dependency. "
            "Install it with `pip install 'docpull[markitdown]'` or `pip install 'docpull[parse]'`."
        ) from err

    converter_class = getattr(module, "MarkItDown", None)
    if converter_class is None:
        raise DocumentParseError("Installed markitdown package does not expose MarkItDown.")
    converter = converter_class()
    try:
        result = converter.convert(str(path))
    except Exception as err:  # noqa: BLE001
        raise DocumentParseError(f"MarkItDown failed to parse {path}: {err}") from err

    content = _first_string(
        getattr(result, "text_content", None),
        getattr(result, "markdown", None),
        getattr(result, "text", None),
    )
    if content is None:
        content = result if isinstance(result, str) else ""
    return content, {
        "result_title": _first_string(getattr(result, "title", None)),
        "bytes_read": path.stat().st_size,
    }


def _parse_unstructured(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        module = importlib.import_module("unstructured.partition.auto")
    except ImportError as err:
        raise DocumentParseError(
            "Unstructured parsing requires the optional dependency. "
            "Install it with `pip install 'docpull[unstructured]'` or `pip install 'docpull[parse]'`."
        ) from err

    partition = getattr(module, "partition", None)
    if partition is None:
        raise DocumentParseError("Installed unstructured package does not expose partition().")
    try:
        elements = list(partition(filename=str(path)))
    except Exception as err:  # noqa: BLE001
        raise DocumentParseError(f"Unstructured failed to parse {path}: {err}") from err

    blocks = [str(element).strip() for element in elements if str(element).strip()]
    categories = sorted(
        {
            str(getattr(element, "category", "")).strip()
            for element in elements
            if str(getattr(element, "category", "")).strip()
        }
    )
    return "\n\n".join(blocks), {
        "element_count": len(elements),
        "element_categories": categories,
        "bytes_read": path.stat().st_size,
    }


def _parsed_document(
    path: Path,
    *,
    source_url: str,
    title: str | None,
    content: str,
    backend: str,
    source_mime_type: str,
    metadata: dict[str, Any],
) -> ParsedDocument:
    normalized_content = content.strip()
    if not normalized_content:
        if source_mime_type == "application/pdf" and int(metadata.get("image_count", 0)) > 0:
            raise DocumentParseError(
                f"PDF appears image-only and requires an explicitly configured OCR backend: {path}",
                code="image_only",
            )
        raise DocumentParseError(f"Parser backend {backend} produced no text for {path}.")
    source_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return ParsedDocument(
        path=path,
        source_url=source_url,
        title=(title or path.stem or path.name).strip(),
        content=normalized_content,
        backend=backend,
        source_mime_type=source_mime_type,
        metadata={
            **metadata,
            "parser": backend,
            "source_sha256": source_sha256,
            **_extraction_quality_metadata(normalized_content),
        },
    )


def _extraction_quality_metadata(content: str) -> dict[str, Any]:
    tokens = _TOKEN_RE.findall(content)
    alphabetic_tokens = [token for token in tokens if _ALPHA_TOKEN_RE.fullmatch(token)]
    long_tokens = [token for token in alphabetic_tokens if len(token) >= 25]
    alphabetic_count = len(alphabetic_tokens)
    return {
        "token_count": len(tokens),
        "alphabetic_token_count": alphabetic_count,
        "average_alphabetic_token_length": (
            round(sum(map(len, alphabetic_tokens)) / alphabetic_count, 4) if alphabetic_count else 0.0
        ),
        "long_alphabetic_token_count": len(long_tokens),
        "long_alphabetic_token_rate": round(len(long_tokens) / alphabetic_count, 8)
        if alphabetic_count
        else 0.0,
        "longest_alphabetic_token_length": max(map(len, alphabetic_tokens), default=0),
        "fused_word_proxy_count": len(long_tokens),
        "fused_word_proxy_rate": round(len(long_tokens) / alphabetic_count, 8) if alphabetic_count else 0.0,
    }


def _records_for_parsed_document(
    parsed: ParsedDocument,
    *,
    document_index: int,
    emit_chunks: bool,
    chunk_tokens: int,
    counter: TokenCounter,
) -> list[DocumentRecord]:
    source_hash = hashlib.sha256(parsed.content.encode("utf-8")).hexdigest()
    chunks = chunk_markdown(parsed.content, max_tokens=chunk_tokens, counter=counter) if emit_chunks else []
    if not chunks:
        token_count = counter.count(parsed.content)
        return [
            _record_from_text(
                parsed,
                text=parsed.content,
                source_hash=source_hash,
                document_index=document_index,
                record_index=1,
                token_count=token_count,
            )
        ]
    records: list[DocumentRecord] = []
    for record_index, chunk in enumerate(chunks, start=1):
        records.append(
            _record_from_text(
                parsed,
                text=chunk.text,
                source_hash=source_hash,
                document_index=document_index,
                record_index=record_index,
                chunk_index=chunk.index,
                chunk_heading=chunk.heading,
                token_count=chunk.token_count,
            )
        )
    return records


def _record_from_text(
    parsed: ParsedDocument,
    *,
    text: str,
    source_hash: str,
    document_index: int,
    record_index: int,
    token_count: int,
    chunk_index: int | None = None,
    chunk_heading: str | None = None,
) -> DocumentRecord:
    path_stat = parsed.path.stat()
    route = {
        "name": "local-document-parse",
        "backend": parsed.backend,
        "output_format": "document-parse",
        "input_path": str(parsed.path),
        "source_mime_type": parsed.source_mime_type,
        "bytes_read": path_stat.st_size,
    }
    metadata = {
        "source_path": str(parsed.path),
        "source_filename": parsed.path.name,
        "source_mime_type": parsed.source_mime_type,
        "source_document_hash": source_hash,
        "parse_backend": parsed.backend,
        **parsed.metadata,
    }
    extraction = {
        "workflow": "document-parse",
        "parser": parsed.backend,
        "parsed_at": utc_now_iso(),
        "output_content_type": "text/markdown",
    }
    return DocumentRecord.from_page(
        url=parsed.source_url,
        title=parsed.title,
        content=text,
        metadata=metadata,
        extraction=extraction,
        source_type="local_document",
        content_type="text/markdown",
        mime_type="text/markdown",
        rendered_at=utc_now_iso(),
        route=route,
        rights=default_rights_state(),
        source_citation_id=f"S{document_index}",
        record_citation_id=f"S{document_index}.{record_index}",
        chunk_index=chunk_index,
        chunk_heading=chunk_heading,
        token_count=token_count,
    )


def _parse_result_payload(
    *,
    output_root: Path,
    requested_backend: ParseBackend,
    parsed_documents: list[ParsedDocument],
    records: list[DocumentRecord],
    emit_chunks: bool,
    chunk_tokens: int,
    artifacts: dict[str, Path],
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "generated_at": utc_now_iso(),
        "workflow": "document-parse",
        "pack_dir": str(output_root),
        "requested_backend": requested_backend,
        "backend_count": _backend_counts(parsed_documents),
        "document_count": len(parsed_documents),
        "record_count": len(records),
        "chunk_count": sum(1 for record in records if record.chunk_id),
        "emit_chunks": emit_chunks,
        "chunk_tokens": chunk_tokens,
        "inputs": [
            {
                "path": str(parsed.path),
                "source_url": parsed.source_url,
                "title": parsed.title,
                "backend": parsed.backend,
                "source_mime_type": parsed.source_mime_type,
            }
            for parsed in parsed_documents
        ],
        "artifacts": {key: _relative_display(output_root, value) for key, value in artifacts.items()},
    }


def _backend_counts(parsed_documents: list[ParsedDocument]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for parsed in parsed_documents:
        counts[parsed.backend] = counts.get(parsed.backend, 0) + 1
    return counts


def _resolve_input_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise DocumentParseError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise DocumentParseError(f"Input path is not a file: {path}")
    return path


def _guess_mime_type(path: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(path.name)
    return (guessed or "application/octet-stream").lower()


def _is_text_compatible(path: Path, mime_type: str) -> bool:
    return (
        path.suffix.lower() in TEXT_COMPATIBLE_SUFFIXES
        or mime_type.startswith("text/")
        or mime_type in TEXT_COMPATIBLE_MIME_TYPES
    )


def _metadata_title(metadata: dict[str, Any]) -> str | None:
    return _first_string(metadata.get("result_title"), metadata.get("title"))


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _safe_slug(value: str) -> str:
    slug = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")[:80]


def _relative_display(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _result_text(result: dict[str, Any]) -> str:
    validation_raw = result.get("validation")
    validation: dict[str, Any] = validation_raw if isinstance(validation_raw, dict) else {}
    count_line = (
        f"Documents: {result.get('document_count')}  "
        f"Records: {result.get('record_count')}  "
        f"Chunks: {result.get('chunk_count')}"
    )
    lines = [
        f"Wrote document parse pack: {result.get('pack_dir')}",
        count_line,
        f"Backends: {json.dumps(result.get('backend_count', {}), sort_keys=True)}",
        f"Validation: {validation.get('status', 'unknown')} ({validation.get('level', 'raw')})",
        f"Next: docpull pack prepare {result.get('pack_dir')} --eval-grade"
        if validation.get("level") == "raw"
        else "",
    ]
    return "\n".join(line for line in lines if line).rstrip()


__all__ = ["DocumentParseError", "ParsedDocument", "parse_documents", "run_parse_cli"]
