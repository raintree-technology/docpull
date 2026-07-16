"""Shared local pack loading, provenance, and search helpers."""

from __future__ import annotations

import hashlib
import heapq
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .models.document import DocumentRecord
from .time_utils import utc_now_iso

PACK_READER_SCHEMA_VERSION = 1
DEFAULT_DOCUMENT_LIMIT = 50
MAX_DOCUMENT_LIMIT = 1000

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,}", re.IGNORECASE)
_SECRET_KEYS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "provider_key",
    "provider_api_key",
    "auth_header",
    "auth_bearer",
    "password",
    "secret",
    "client_secret",
    "headers",
    "request_headers",
    "raw_request",
    "raw_response",
}


class PackReadError(RuntimeError):
    """Raised when a local pack cannot be loaded safely."""


@dataclass(frozen=True)
class PackSource:
    """Stable source/citation entry for a pack URL."""

    citation_id: str
    url: str
    title: str | None
    path: str | None
    record_count: int
    document_ids: tuple[str, ...]
    content_hashes: tuple[str, ...]
    record_citations: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation_id": self.citation_id,
            "url": self.url,
            "title": self.title or self.url,
            "domain": _domain(self.url),
            "path": self.path,
            "record_count": self.record_count,
            "document_ids": list(self.document_ids),
            "content_hashes": list(self.content_hashes),
            "record_citations": list(self.record_citations),
        }


@dataclass(frozen=True)
class LocalPack:
    """In-memory view of a DocPull pack directory."""

    pack_dir: Path
    manifest: dict[str, Any]
    metadata: dict[str, Any]
    metadata_path: Path | None
    documents: tuple[DocumentRecord, ...]
    sources: tuple[PackSource, ...]
    document_source: str
    sqlite_path: Path | None = None
    _source_index: dict[str, PackSource] = field(init=False, repr=False, compare=False)
    _document_index: dict[str, DocumentRecord] = field(init=False, repr=False, compare=False)
    _first_document_by_url: dict[str, DocumentRecord] = field(init=False, repr=False, compare=False)
    _record_citation_index: dict[tuple[str, str], str] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        """Build immutable-pack lookup indexes once instead of once per record."""
        source_index = {source.url: source for source in self.sources}
        document_index: dict[str, DocumentRecord] = {}
        first_document_by_url: dict[str, DocumentRecord] = {}
        for record in self.documents:
            document_index.setdefault(record.document_id, record)
            if record.chunk_id:
                document_index.setdefault(record.chunk_id, record)
            first_document_by_url.setdefault(record.url, record)

        record_citation_index: dict[tuple[str, str], str] = {}
        for source in self.sources:
            for item in source.record_citations:
                record_key = item.get("record_key")
                if not isinstance(record_key, str):
                    continue
                record_citation_index.setdefault(
                    (source.url, record_key),
                    str(item.get("record_citation_id") or ""),
                )

        object.__setattr__(self, "_source_index", source_index)
        object.__setattr__(self, "_document_index", document_index)
        object.__setattr__(self, "_first_document_by_url", first_document_by_url)
        object.__setattr__(self, "_record_citation_index", record_citation_index)

    @property
    def citation_by_url(self) -> dict[str, str]:
        return {source.url: source.citation_id for source in self.sources}

    @property
    def source_by_url(self) -> dict[str, PackSource]:
        return {source.url: source for source in self.sources}

    def source_for_url(self, url: str) -> PackSource | None:
        """Return a source in constant time without exposing the internal index."""
        return self._source_index.get(url)

    def first_document_for_url(self, url: str) -> DocumentRecord | None:
        """Return the first record for a source URL in constant time."""
        return self._first_document_by_url.get(url)

    def record_citation_id(self, record: DocumentRecord) -> str | None:
        source = self.source_for_url(record.url)
        if source is None:
            return None
        key = record.chunk_id or record.document_id
        return self._record_citation_index.get((record.url, key), source.citation_id)

    def health_payload(self) -> dict[str, Any]:
        return {
            "schema_version": PACK_READER_SCHEMA_VERSION,
            "status": "ok",
            "generated_at": utc_now_iso(),
            "pack_dir": str(self.pack_dir),
            "readonly": True,
            "document_count": len(self.documents),
            "source_count": len(self.sources),
            "document_source": self.document_source,
            "sqlite_fts_available": self.sqlite_path is not None,
        }

    def documents_payload(self, *, limit: int, offset: int) -> dict[str, Any]:
        normalized_limit = _clamp_limit(limit)
        normalized_offset = max(0, offset)
        records = self.documents[normalized_offset : normalized_offset + normalized_limit]
        return {
            "schema_version": PACK_READER_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "pack_dir": str(self.pack_dir),
            "total": len(self.documents),
            "count": len(records),
            "limit": normalized_limit,
            "offset": normalized_offset,
            "documents": [self.document_payload(record, include_content=False) for record in records],
        }

    def document_payload(self, record: DocumentRecord, *, include_content: bool) -> dict[str, Any]:
        source = self.source_for_url(record.url)
        payload: dict[str, Any] = {
            "schema_version": record.schema_version,
            "document_id": record.document_id,
            "chunk_id": record.chunk_id,
            "url": record.url,
            "title": record.title,
            "content_hash": record.content_hash,
            "citation_id": source.citation_id if source else None,
            "record_citation_id": self.record_citation_id(record),
            "source_path": source.path if source else None,
            "source_type": record.source_type,
            "fetched_at": record.fetched_at,
            "rendered_at": record.rendered_at,
            "content_type": record.content_type,
            "mime_type": record.mime_type,
            "chunk_index": record.chunk_index,
            "chunk_heading": record.chunk_heading,
            "token_count": record.token_count,
            "route": sanitize_metadata(record.route),
            "rights": sanitize_metadata(record.rights),
            "metadata": sanitize_metadata(record.metadata),
            "extraction": sanitize_metadata(record.extraction),
        }
        if include_content:
            payload["content"] = record.content
        return {key: value for key, value in payload.items() if value is not None}

    def find_document(self, document_id: str) -> DocumentRecord | None:
        return self._document_index.get(document_id)

    def citations_payload(self) -> dict[str, Any]:
        return {
            "schema_version": PACK_READER_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "pack_dir": str(self.pack_dir),
            "source_count": len(self.sources),
            "record_count": len(self.documents),
            "sources": [source.to_dict() for source in self.sources],
        }

    def search_payload(self, query: str, *, limit: int) -> dict[str, Any]:
        if not query.strip():
            raise PackReadError("search query must be non-empty")
        normalized_limit = _clamp_limit(limit)
        results, engine = _search_with_sqlite(self, query, normalized_limit)
        if results is None:
            results = _search_by_scan(self, query, normalized_limit)
            engine = "scan"
        citation_ids = {str(result.get("citation_id")) for result in results if result.get("citation_id")}
        return {
            "schema_version": PACK_READER_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "pack_dir": str(self.pack_dir),
            "query": query,
            "engine": engine,
            "limit": normalized_limit,
            "record_count": len(self.documents),
            "result_count": len(results),
            "results": results,
            "citations": [source.to_dict() for source in self.sources if source.citation_id in citation_ids],
        }


def load_pack(pack_dir: Path | str) -> LocalPack:
    """Load a DocPull pack from files without network access."""
    root = Path(pack_dir).expanduser().resolve()
    if not root.exists():
        raise PackReadError(f"Pack directory does not exist: {root}")
    if not root.is_dir():
        raise PackReadError(f"Pack path is not a directory: {root}")

    manifest = _read_json(root / "corpus.manifest.json", required=False)
    if not isinstance(manifest, dict):
        manifest = {}
    metadata, metadata_path = _read_pack_metadata_entry(root)
    documents, document_source, sqlite_path = _read_documents(root, manifest, metadata)
    sources = _build_sources(root, manifest, metadata, documents)
    return LocalPack(
        pack_dir=root,
        manifest=manifest,
        metadata=metadata,
        metadata_path=metadata_path,
        documents=tuple(documents),
        sources=tuple(sources),
        document_source=document_source,
        sqlite_path=sqlite_path,
    )


def _local_pack_from_records(
    pack_dir: Path,
    records: list[dict[str, Any]],
    *,
    metadata: dict[str, Any],
    metadata_path: Path | None,
) -> LocalPack:
    """Build a LocalPack from an already-read NDJSON corpus."""
    manifest = _read_json(pack_dir / "corpus.manifest.json", required=False)
    if not isinstance(manifest, dict):
        manifest = {}
    documents = _coerce_records(records)
    sources = _build_sources(pack_dir, manifest, metadata, documents)
    return LocalPack(
        pack_dir=pack_dir,
        manifest=manifest,
        metadata=metadata,
        metadata_path=metadata_path,
        documents=tuple(documents),
        sources=tuple(sources),
        document_source="documents.ndjson",
        sqlite_path=None,
    )


def sanitize_metadata(value: Any) -> Any:
    """Remove credential-like keys from exported or served metadata."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                continue
            cleaned[key_text] = sanitize_metadata(item)
        return cleaned
    if isinstance(value, list):
        return [sanitize_metadata(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_metadata(item) for item in value]
    return value


def resolve_pack_path(pack_dir: Path, value: Any) -> Path | None:
    """Resolve a relative pack path and refuse traversal or absolute paths."""
    if not isinstance(value, str) or not value:
        return None
    candidate = Path(value)
    if candidate.is_absolute():
        return None
    root = pack_dir.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _read_documents(
    pack_dir: Path,
    manifest: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[list[DocumentRecord], str, Path | None]:
    for path in _structured_record_candidates(pack_dir, manifest, metadata):
        if not path.exists():
            continue
        if path.suffix == ".ndjson":
            return _coerce_records(_read_ndjson(path)), _relative_display(pack_dir, path), None
        if path.suffix == ".json":
            return _coerce_records(_read_documents_json(path)), _relative_display(pack_dir, path), None
        if path.suffix == ".db":
            return _coerce_records(_read_sqlite(path)), _relative_display(pack_dir, path), path

    records = _read_manifest_files(pack_dir, manifest)
    if records:
        return _coerce_records(records), "corpus.manifest.json", None
    raise PackReadError(
        f"No readable documents found in {pack_dir}; expected documents.ndjson, documents.json, "
        "documents.db, or manifest records with output_path files."
    )


def _structured_record_candidates(
    pack_dir: Path,
    manifest: dict[str, Any],
    metadata: dict[str, Any],
) -> list[Path]:
    candidates: list[Path] = []

    artifacts = metadata.get("artifacts")
    if isinstance(artifacts, dict):
        for key, value in artifacts.items():
            key_text = str(key).lower()
            if "document" not in key_text:
                continue
            resolved = resolve_pack_path(pack_dir, value)
            if resolved is not None:
                candidates.append(resolved)

    candidates.extend(
        [
            pack_dir / "documents.ndjson",
            pack_dir / "documents.json",
            pack_dir / "documents.db",
        ]
    )

    manifest_records = manifest.get("records")
    if isinstance(manifest_records, list):
        for item in manifest_records:
            if not isinstance(item, dict):
                continue
            resolved = resolve_pack_path(pack_dir, item.get("output_path"))
            if resolved is not None and resolved.suffix in {".ndjson", ".json", ".db"}:
                candidates.append(resolved)

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _read_manifest_files(pack_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    manifest_records = manifest.get("records")
    if not isinstance(manifest_records, list):
        return []
    records: list[dict[str, Any]] = []
    for item in manifest_records:
        if not isinstance(item, dict):
            continue
        output_path = resolve_pack_path(pack_dir, item.get("output_path"))
        if output_path is None or output_path.suffix in {".db", ".json", ".ndjson"}:
            continue
        if not output_path.exists() or not output_path.is_file():
            continue
        data = dict(item)
        data["content"] = output_path.read_text(encoding="utf-8")
        records.append(data)
    return records


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise PackReadError(f"Invalid NDJSON in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise PackReadError(f"Invalid NDJSON in {path} line {index}: expected object")
        records.append(value)
    return records


def _read_documents_json(path: Path) -> list[dict[str, Any]]:
    value = _read_json(path)
    if isinstance(value, dict) and isinstance(value.get("documents"), list):
        raw_records = value["documents"]
    elif isinstance(value, list):
        raw_records = value
    else:
        raise PackReadError(f"Invalid documents JSON in {path}: expected documents list")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(raw_records, start=1):
        if not isinstance(item, dict):
            raise PackReadError(f"Invalid documents JSON in {path} item {index}: expected object")
        records.append(item)
    return records


def _read_sqlite(path: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)").fetchall()}
        required = {"url", "content"}
        if not required <= columns:
            raise PackReadError(f"SQLite pack is missing required columns in {path}")
        selected = [
            "schema_version",
            "record_key",
            "document_id",
            "chunk_id",
            "chunk_index",
            "chunk_heading",
            "token_count",
            "url",
            "title",
            "content",
            "content_hash",
            "source_type",
            "content_type",
            "mime_type",
            "rendered_at",
            "route",
            "rights",
            "source_citation_id",
            "record_citation_id",
            "metadata",
            "extraction",
            "fetched_at",
        ]
        present = [column for column in selected if column in columns]
        # Bandit B608 false positive: selected columns come from the hard-coded whitelist above.
        rows = conn.execute(
            f"SELECT {', '.join(present)} FROM documents ORDER BY id"  # nosec B608
        ).fetchall()
    except sqlite3.Error as err:
        raise PackReadError(f"Invalid SQLite pack {path}: {err}") from err
    finally:
        conn.close()

    records: list[dict[str, Any]] = []
    for row in rows:
        data = dict(zip(present, row, strict=True))
        for key in ("metadata", "extraction", "route", "rights"):
            if isinstance(data.get(key), str) and data[key]:
                try:
                    parsed = json.loads(str(data[key]))
                except json.JSONDecodeError:
                    parsed = {}
                data[key] = parsed if isinstance(parsed, dict) else {}
        records.append(data)
    return records


def _coerce_records(records: list[dict[str, Any]]) -> list[DocumentRecord]:
    return [_coerce_record(record) for record in records]


def _coerce_record(record: dict[str, Any]) -> DocumentRecord:
    data = dict(record)
    url = str(data.get("url") or "")
    if not url:
        raise PackReadError("Document record is missing url")
    content = data.get("content")
    if content is None:
        content = ""
    content_text = str(content)
    data["content"] = content_text
    content_hash = str(data.get("content_hash") or "").strip()
    if not content_hash:
        content_hash = hashlib.sha256(content_text.encode("utf-8")).hexdigest()
        data["content_hash"] = content_hash
    if not data.get("document_id"):
        data["document_id"] = _stable_id("doc", url, content_hash)
    if not isinstance(data.get("metadata"), dict):
        data["metadata"] = {}
    if not isinstance(data.get("extraction"), dict):
        data["extraction"] = {}
    if not isinstance(data.get("route"), dict):
        data["route"] = {}
    if not isinstance(data.get("rights"), dict):
        data["rights"] = {}
    return DocumentRecord.model_validate(data)


def _read_json(path: Path, *, required: bool = True) -> Any:
    if not path.exists():
        if required:
            raise PackReadError(f"Missing required file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise PackReadError(f"Invalid JSON in {path}: {err}") from err


def _read_pack_metadata_entry(pack_dir: Path) -> tuple[dict[str, Any], Path | None]:
    direct = _read_json(pack_dir / "parallel.pack.json", required=False)
    if isinstance(direct, dict):
        return direct, pack_dir / "parallel.pack.json"
    for candidate in sorted(pack_dir.glob("*.pack.json")):
        parsed = _read_json(candidate, required=False)
        if isinstance(parsed, dict):
            return parsed, candidate
    return {}, None


def _build_sources(
    pack_dir: Path,
    manifest: dict[str, Any],
    metadata: dict[str, Any],
    documents: list[DocumentRecord],
) -> list[PackSource]:
    declared = _declared_source_entries(pack_dir, metadata)
    output_path_by_url = _manifest_paths_by_url(pack_dir, manifest)
    docs_by_url: dict[str, list[DocumentRecord]] = {}
    for record in documents:
        docs_by_url.setdefault(record.url, []).append(record)

    ordered_urls: list[str] = []
    entry_by_url: dict[str, dict[str, Any]] = {}
    for entry in declared:
        url = str(entry.get("url") or "")
        if not url or url in entry_by_url:
            continue
        ordered_urls.append(url)
        entry_by_url[url] = entry
    for record in documents:
        if record.url not in entry_by_url:
            ordered_urls.append(record.url)
            entry_by_url[record.url] = {"url": record.url, "title": record.title}

    sources: list[PackSource] = []
    for index, url in enumerate(ordered_urls, start=1):
        url_docs = docs_by_url.get(url, [])
        entry = entry_by_url[url]
        path = _source_path(pack_dir, entry.get("path")) or output_path_by_url.get(url)
        title = str(entry.get("title") or "").strip() or (url_docs[0].title if url_docs else url)
        record_citations = tuple(
            {
                "record_citation_id": f"S{index}.{record_index}",
                "source_citation_id": f"S{index}",
                "record_key": record.chunk_id or record.document_id,
                "document_id": record.document_id,
                "chunk_id": record.chunk_id,
                "content_hash": record.content_hash,
                "title": record.title or title,
            }
            for record_index, record in enumerate(url_docs, start=1)
        )
        sources.append(
            PackSource(
                citation_id=f"S{index}",
                url=url,
                title=title,
                path=path,
                record_count=len(url_docs),
                document_ids=tuple(record.document_id for record in url_docs),
                content_hashes=tuple(
                    sorted({record.content_hash for record in url_docs if record.content_hash})
                ),
                record_citations=record_citations,
            )
        )
    return sources


def _declared_source_entries(pack_dir: Path, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    sources = metadata.get("sources")
    if not isinstance(sources, list):
        return []
    entries: list[dict[str, Any]] = []
    for source in sources:
        if not isinstance(source, dict) or not source.get("url"):
            continue
        entry = dict(source)
        path = _source_path(pack_dir, entry.get("path"))
        if path is not None:
            entry["path"] = path
        entries.append(entry)
    return entries


def _manifest_paths_by_url(pack_dir: Path, manifest: dict[str, Any]) -> dict[str, str]:
    records = manifest.get("records")
    if not isinstance(records, list):
        return {}
    paths: dict[str, str] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url or url in paths:
            continue
        path = _source_path(pack_dir, item.get("output_path"))
        if path:
            paths[url] = path
    return paths


def _source_path(pack_dir: Path, value: Any) -> str | None:
    resolved = resolve_pack_path(pack_dir, value)
    if resolved is None or not resolved.exists():
        return None
    return _relative_display(pack_dir, resolved)


def _search_with_sqlite(
    pack: LocalPack,
    query: str,
    limit: int,
) -> tuple[list[dict[str, Any]], str] | tuple[None, str]:
    if pack.sqlite_path is None:
        return None, "scan"
    try:
        from .pipeline.steps.save_sqlite import search_sqlite_documents

        hits = search_sqlite_documents(pack.sqlite_path, query, limit=limit)
    except Exception:
        return None, "scan"
    results: list[dict[str, Any]] = []
    for rank, hit in enumerate(hits, start=1):
        record = pack.find_document(hit.record_key or "") or pack.first_document_for_url(hit.url)
        source = pack.source_for_url(hit.url)
        results.append(
            {
                "rank": rank,
                "score": hit.rank,
                "engine": "sqlite-fts",
                "document_id": record.document_id if record else None,
                "chunk_id": record.chunk_id if record else None,
                "citation_id": source.citation_id if source else None,
                "record_citation_id": pack.record_citation_id(record) if record else None,
                "url": hit.url,
                "title": hit.title or (record.title if record else hit.url),
                "content_hash": record.content_hash if record else None,
                "excerpt": hit.snippet,
            }
        )
    return results, "sqlite-fts"


def _search_by_scan(pack: LocalPack, query: str, limit: int) -> list[dict[str, Any]]:
    terms = sorted(set(_keywords(query)))
    phrase = _clean_text(query).lower()
    scored: list[dict[str, Any]] = []
    for record in pack.documents:
        title = record.title or record.url
        content = record.content or ""
        score, matched_terms = _scan_score(
            terms=terms,
            phrase=phrase,
            title=title,
            url=record.url,
            content=content,
        )
        if score <= 0:
            continue
        source = pack.source_for_url(record.url)
        scored.append(
            {
                "score": score,
                "engine": "scan",
                "document_id": record.document_id,
                "chunk_id": record.chunk_id,
                "citation_id": source.citation_id if source else None,
                "record_citation_id": pack.record_citation_id(record),
                "url": record.url,
                "title": title,
                "content_hash": record.content_hash,
                "matched_terms": matched_terms,
                "_content": content or title,
            }
        )
    top_results = heapq.nsmallest(
        limit,
        scored,
        key=lambda item: (
            -int(item.get("score", 0)),
            str(item.get("citation_id") or ""),
            str(item.get("document_id") or ""),
        ),
    )
    for result in top_results:
        content = str(result.pop("_content"))
        result["excerpt"] = _excerpt(content, terms, phrase)
    return [{"rank": rank, **item} for rank, item in enumerate(top_results, start=1)]


def _scan_score(
    *,
    terms: list[str],
    phrase: str,
    title: str,
    url: str,
    content: str,
) -> tuple[int, list[str]]:
    title_text = _clean_text(title).lower()
    url_text = url.lower()
    content_text = _clean_text(content).lower()
    score = 0
    matched: list[str] = []
    for term in terms:
        title_hits = _term_count(title_text, term)
        url_hits = _term_count(url_text, term)
        content_hits = _term_count(content_text, term)
        if title_hits or url_hits or content_hits:
            matched.append(term)
        score += min(title_hits, 3) * 8
        score += min(url_hits, 3) * 3
        score += min(content_hits, 10) * 2
    if phrase and len(phrase) >= 4:
        if phrase in title_text:
            score += 20
        if phrase in content_text:
            score += 10
    if len(matched) > 1:
        score += len(matched) * 3
    return score, matched


def _excerpt(content: str, terms: list[str], phrase: str) -> str:
    cleaned = _clean_text(content)
    if not cleaned:
        return ""
    lowered = cleaned.lower()
    positions = [lowered.find(term) for term in terms if lowered.find(term) != -1]
    if phrase:
        phrase_position = lowered.find(phrase)
        if phrase_position != -1:
            positions.append(phrase_position)
    if not positions:
        return _truncate(cleaned, 360)
    position = min(positions)
    start = max(0, position - 120)
    end = min(len(cleaned), position + 300)
    prefix = "..." if start else ""
    suffix = "..." if end < len(cleaned) else ""
    return _truncate(prefix + cleaned[start:end].strip(" ,.;:-") + suffix, 360)


def _documents_by_url(documents: tuple[DocumentRecord, ...]) -> dict[str, DocumentRecord]:
    by_url: dict[str, DocumentRecord] = {}
    for record in documents:
        by_url.setdefault(record.url, record)
    return by_url


def _documents_by_record_key(documents: tuple[DocumentRecord, ...]) -> dict[str, DocumentRecord]:
    by_key: dict[str, DocumentRecord] = {}
    for record in documents:
        by_key.setdefault(record.chunk_id or record.document_id, record)
    return by_key


def _is_secret_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return (
        normalized in _SECRET_KEYS
        or normalized.endswith("_secret")
        or normalized.endswith("_password")
        or normalized.endswith("_api_key")
        or normalized.endswith("_token")
    )


def _keywords(value: str) -> list[str]:
    return [match.group(0).lower() for match in _WORD_RE.finditer(value)]


def _term_count(text: str, term: str) -> int:
    if not text or not term:
        return 0
    return len(re.findall(rf"\b{re.escape(term)}\b", text, flags=re.IGNORECASE))


def _clean_text(value: str) -> str:
    text = re.sub(r"\[([^\]]+)]\([^)]+\)", r"\1", value)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = text.replace("`", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _truncate(value: str, max_chars: int) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def _clamp_limit(value: int) -> int:
    if value < 1:
        return DEFAULT_DOCUMENT_LIMIT
    return min(value, MAX_DOCUMENT_LIMIT)


def _relative_display(pack_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(pack_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"
