"""Metadata-only context adapters for native provider integrations.

These helpers intentionally do not enforce release/runtime gates. They turn
provider-linked operational context into DocPull document records that can be
packed, cited, diffed, and handed to downstream consumers as evidence refs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models.document import DocumentRecord

NativeIntegrationProvider = Literal[
    "github",
    "vercel",
    "datadog",
    "snowflake",
    "slack",
    "jira",
    "aws",
    "gcp",
    "azure",
]

NATIVE_INTEGRATION_PROVIDERS: tuple[NativeIntegrationProvider, ...] = (
    "github",
    "vercel",
    "datadog",
    "snowflake",
    "slack",
    "jira",
    "aws",
    "gcp",
    "azure",
)


@dataclass(frozen=True)
class NativeIntegrationContextSource:
    provider: NativeIntegrationProvider
    ref: str
    title: str
    url: str | None = None
    summary: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def native_integration_context_documents(
    sources: list[NativeIntegrationContextSource],
) -> list[DocumentRecord]:
    return [_source_to_document(source) for source in sources]


def _source_to_document(source: NativeIntegrationContextSource) -> DocumentRecord:
    url = source.url or f"integration://{source.provider}/{source.ref}"
    content = "\n".join(
        line
        for line in [
            f"# {source.title}",
            "",
            f"- Provider: `{source.provider}`",
            f"- Reference: `{source.ref}`",
            "",
            source.summary or "Metadata-only provider context captured as DocPull evidence.",
        ]
        if line is not None
    )
    metadata = {
        "storageMode": "metadata_only",
        "provider": source.provider,
        "ref": source.ref,
        **source.metadata,
    }
    return DocumentRecord.from_page(
        url=url,
        title=source.title,
        content=content,
        metadata=metadata,
        source_type=f"integration:{source.provider}",
        route={"name": "native-integration-context", "provider": source.provider},
        rights={
            "state": "metadata_only",
            "basis": "customer_provided_reference",
            "notes": "No raw provider secret or customer row payload is stored.",
        },
    )
