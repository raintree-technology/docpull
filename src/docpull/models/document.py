"""Canonical document record emitted by all structured output sinks."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

from ..output_contract import content_type_base, default_rights_state
from ..time_utils import utc_now_iso
from .run import DOCUMENT_RECORD_SCHEMA_VERSION, RunIdentity


class DocumentRecord(BaseModel):
    """Versioned logical document shape independent of output container."""

    schema_version: int = DOCUMENT_RECORD_SCHEMA_VERSION
    document_id: str
    url: str
    title: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    extraction: dict[str, Any] = Field(default_factory=dict)
    source_type: str | None = None
    fetched_at: str = Field(default_factory=utc_now_iso)
    rendered_at: str | None = None
    content_type: str = "text/markdown"
    mime_type: str = "text/markdown"
    content_hash: str
    run: dict[str, Any] | None = None
    route: dict[str, Any] = Field(default_factory=dict)
    rights: dict[str, Any] = Field(default_factory=default_rights_state)
    source_citation_id: str | None = None
    record_citation_id: str | None = None
    chunk_index: int | None = None
    chunk_id: str | None = None
    chunk_heading: str | None = None
    token_count: int | None = None
    cik: str | None = None
    accession_number: str | None = None
    form: str | None = None
    filing_date: str | None = None
    issuer_name: str | None = None
    primary_document_url: str | None = None
    retrieved_at: str | None = None

    @classmethod
    def from_page(
        cls,
        *,
        url: str,
        content: str,
        title: str | None = None,
        metadata: dict[str, Any] | None = None,
        extraction: dict[str, Any] | None = None,
        source_type: str | None = None,
        run_identity: RunIdentity | None = None,
        content_type: str | None = None,
        mime_type: str | None = None,
        rendered_at: str | None = None,
        route: dict[str, Any] | None = None,
        rights: dict[str, Any] | None = None,
        source_citation_id: str | None = None,
        record_citation_id: str | None = None,
        chunk_index: int | None = None,
        chunk_heading: str | None = None,
        token_count: int | None = None,
    ) -> DocumentRecord:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document_id = _stable_id("doc", url, content_hash)
        chunk_id = None
        if chunk_index is not None:
            chunk_id = _stable_id(
                "chunk",
                url,
                str(chunk_index),
                chunk_heading or "",
                content_hash,
            )
        doc_metadata = metadata or {}
        normalized_content_type = (content_type or "text/markdown").strip() or "text/markdown"
        normalized_mime_type = mime_type or content_type_base(normalized_content_type) or "text/markdown"
        normalized_token_count = token_count if token_count is not None else _estimate_token_count(content)
        return cls(
            document_id=document_id,
            url=url,
            title=title or url,
            content=content,
            metadata=doc_metadata,
            extraction=extraction or {},
            source_type=source_type,
            rendered_at=rendered_at,
            content_type=normalized_content_type,
            mime_type=normalized_mime_type,
            content_hash=content_hash,
            run=run_identity.model_dump(mode="json") if run_identity else None,
            route=route or {"name": "unknown"},
            rights=rights or default_rights_state(),
            source_citation_id=source_citation_id,
            record_citation_id=record_citation_id,
            chunk_index=chunk_index,
            chunk_id=chunk_id,
            chunk_heading=chunk_heading,
            token_count=normalized_token_count,
            cik=_metadata_string(doc_metadata, "cik"),
            accession_number=_metadata_string(doc_metadata, "accession_number"),
            form=_metadata_string(doc_metadata, "form"),
            filing_date=_metadata_string(doc_metadata, "filing_date"),
            issuer_name=_metadata_string(doc_metadata, "issuer_name"),
            primary_document_url=_metadata_string(doc_metadata, "primary_document_url"),
            retrieved_at=_metadata_string(doc_metadata, "retrieved_at"),
        )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _metadata_string(metadata: dict[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _estimate_token_count(content: str) -> int:
    """Cheap fallback token count for contract completeness."""
    words = content.split()
    return max(1, len(words)) if content.strip() else 0
