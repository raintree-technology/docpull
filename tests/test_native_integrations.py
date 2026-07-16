from __future__ import annotations

from docpull.native_integrations import (
    NativeIntegrationContextSource,
    native_integration_context_documents,
)


def test_native_integration_context_documents_are_metadata_only() -> None:
    docs = native_integration_context_documents(
        [
            NativeIntegrationContextSource(
                provider="jira",
                ref="GOV-123",
                title="Release blocker issue",
                url="https://example.atlassian.net/browse/GOV-123",
                summary="Reviewer accepted the mitigation plan.",
                metadata={"status": "Done"},
            )
        ]
    )

    doc = docs[0]
    assert doc.source_type == "integration:jira"
    assert doc.metadata["storageMode"] == "metadata_only"
    assert doc.metadata["provider"] == "jira"
    assert doc.metadata["ref"] == "GOV-123"
    assert doc.rights["state"] == "metadata_only"
    assert "Reviewer accepted" in doc.content
