"""Canonical document record emitted by all structured output sinks."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field

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
    content_hash: str
    run: dict[str, Any] | None = None
    chunk_index: int | None = None
    chunk_id: str | None = None
    chunk_heading: str | None = None
    token_count: int | None = None

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
        return cls(
            document_id=document_id,
            url=url,
            title=title,
            content=content,
            metadata=metadata or {},
            extraction=extraction or {},
            source_type=source_type,
            content_hash=content_hash,
            run=run_identity.model_dump(mode="json") if run_identity else None,
            chunk_index=chunk_index,
            chunk_id=chunk_id,
            chunk_heading=chunk_heading,
            token_count=token_count,
        )


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"
