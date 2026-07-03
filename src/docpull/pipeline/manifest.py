"""Corpus manifest support for output sinks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..models.document import DocumentRecord
from ..models.run import RunIdentity
from ..output_contract import OUTPUT_CONTRACT_SCHEMA_VERSION, write_raw_contract_sidecars
from ..time_utils import utc_now_iso


class CorpusManifest:
    """Collect stable record metadata and write ``corpus.manifest.json``."""

    def __init__(
        self,
        base_output_dir: Path,
        *,
        output_format: str,
        run_identity: RunIdentity | None = None,
        filename: str = "corpus.manifest.json",
    ) -> None:
        self._base_dir = base_output_dir.resolve()
        self._path = self._base_dir / filename
        self._output_format = output_format
        self._run_identity = run_identity
        self._records: list[dict[str, Any]] = []
        self._seen: set[str] = set()

    def add_record(self, record: DocumentRecord, output_path: Path | str | None = None) -> None:
        record_key = record.chunk_id or record.document_id
        if record_key in self._seen:
            return
        self._seen.add(record_key)
        item: dict[str, Any] = {
            "schema_version": record.schema_version,
            "document_id": record.document_id,
            "url": record.url,
            "title": record.title,
            "content_hash": record.content_hash,
            "fetched_at": record.fetched_at,
            "rendered_at": record.rendered_at,
            "content_type": record.content_type,
            "mime_type": record.mime_type,
            "token_count": record.token_count,
            "route": record.route,
            "rights": record.rights,
        }
        if record.source_type is not None:
            item["source_type"] = record.source_type
        if record.source_citation_id is not None:
            item["source_citation_id"] = record.source_citation_id
        if record.record_citation_id is not None:
            item["record_citation_id"] = record.record_citation_id
        if record.chunk_index is not None:
            item["chunk_index"] = record.chunk_index
        if record.chunk_id is not None:
            item["chunk_id"] = record.chunk_id
        if record.chunk_heading is not None:
            item["chunk_heading"] = record.chunk_heading
        if record.token_count is not None:
            item["token_count"] = record.token_count
        for key in (
            "cik",
            "accession_number",
            "form",
            "filing_date",
            "issuer_name",
            "primary_document_url",
            "retrieved_at",
        ):
            value = getattr(record, key)
            if value is not None:
                item[key] = value
        source_document_hash = record.metadata.get("source_document_hash")
        if source_document_hash:
            item["source_document_hash"] = str(source_document_hash)
        if isinstance(output_path, str):
            item["output_path"] = output_path
        elif output_path is not None:
            try:
                item["output_path"] = str(output_path.resolve().relative_to(self._base_dir))
            except ValueError:
                item["output_path"] = str(output_path)
        self._records.append({key: value for key, value in item.items() if value is not None})

    def finalize(self) -> Path:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
            "output_contract_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "output_format": self._output_format,
            "run": self._run_identity.model_dump(mode="json") if self._run_identity else None,
            "document_count": len({item["document_id"] for item in self._records}),
            "record_count": len(self._records),
            "chunk_count": sum(1 for item in self._records if "chunk_id" in item),
            "records": self._records,
        }
        self._path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        write_raw_contract_sidecars(
            self._base_dir,
            manifest_payload=payload,
            output_format=self._output_format,
        )
        return self._path
