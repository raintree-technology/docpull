"""Local document parsing workflow for v3 context packs."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import mimetypes
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .conversion.chunking import TokenCounter, chunk_markdown
from .models.document import DocumentRecord
from .output_contract import default_rights_state, validate_pack_contract, validation_report_text
from .pipeline.manifest import CorpusManifest
from .time_utils import utc_now_iso

ParseBackend = Literal["auto", "markitdown", "unstructured", "text"]

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
        choices=["auto", "markitdown", "unstructured", "text"],
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
    if backend == "markitdown":
        content, metadata = _parse_markitdown(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title or _metadata_title(metadata),
            content=content,
            backend="markitdown",
            source_mime_type=source_mime_type,
            metadata=metadata,
        )
    if backend == "unstructured":
        content, metadata = _parse_unstructured(path)
        return _parsed_document(
            path,
            source_url=source_url,
            title=title or _metadata_title(metadata),
            content=content,
            backend="unstructured",
            source_mime_type=source_mime_type,
            metadata=metadata,
        )
    return _parse_auto(path, source_url=source_url, title=title, source_mime_type=source_mime_type)


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
    for backend in ("markitdown", "unstructured"):
        try:
            if backend == "markitdown":
                content, metadata = _parse_markitdown(path)
            else:
                content, metadata = _parse_unstructured(path)
        except DocumentParseError as err:
            errors.append(str(err))
            continue
        return _parsed_document(
            path,
            source_url=source_url,
            title=title or _metadata_title(metadata),
            content=content,
            backend=backend,
            source_mime_type=source_mime_type,
            metadata=metadata,
        )

    install_hint = (
        "Install an optional parser with `pip install 'docpull[markitdown]'`, "
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
        content = str(result)
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
        raise DocumentParseError(f"Parser backend {backend} produced no text for {path}.")
    return ParsedDocument(
        path=path,
        source_url=source_url,
        title=(title or path.stem or path.name).strip(),
        content=normalized_content,
        backend=backend,
        source_mime_type=source_mime_type,
        metadata=metadata,
    )


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
