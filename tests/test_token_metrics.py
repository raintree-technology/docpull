"""Tests for token economics aggregates in corpus.manifest.json."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.models.document import DocumentRecord
from docpull.pipeline.manifest import CorpusManifest


def _finalize(manifest: CorpusManifest, base_dir: Path) -> dict:
    manifest.finalize()
    return json.loads((base_dir / "corpus.manifest.json").read_text(encoding="utf-8"))


def test_token_metrics_from_document_records(tmp_path: Path) -> None:
    manifest = CorpusManifest(tmp_path, output_format="markdown")
    for index, tokens in enumerate((100, 300)):
        manifest.add_record(
            DocumentRecord.from_page(
                url=f"https://docs.example.com/{index}",
                content=f"# Page {index}",
                token_count=tokens,
            )
        )

    payload = _finalize(manifest, tmp_path)

    assert payload["token_metrics"] == {
        "total_tokens": 400,
        "page_count": 2,
        "tokens_per_page": 200.0,
    }


def test_token_metrics_prefer_chunk_records(tmp_path: Path) -> None:
    manifest = CorpusManifest(tmp_path, output_format="ndjson")
    # A document-level record plus its chunks: only chunk tokens must count,
    # otherwise the parent document double-counts.
    manifest.add_record(
        DocumentRecord.from_page(
            url="https://docs.example.com/a",
            content="# A",
            token_count=500,
        )
    )
    for chunk_index, tokens in enumerate((120, 180)):
        manifest.add_record(
            DocumentRecord.from_page(
                url="https://docs.example.com/a",
                content=f"chunk {chunk_index}",
                chunk_index=chunk_index,
                chunk_heading="Section",
                token_count=tokens,
            )
        )

    payload = _finalize(manifest, tmp_path)

    assert payload["token_metrics"]["total_tokens"] == 300
    assert payload["token_metrics"]["page_count"] == 1
    assert payload["token_metrics"]["tokens_per_page"] == 300.0


def test_token_metrics_absent_without_token_counts(tmp_path: Path) -> None:
    manifest = CorpusManifest(tmp_path, output_format="markdown")
    record = DocumentRecord.from_page(url="https://docs.example.com/a", content="# A")
    record.token_count = None
    manifest.add_record(record)

    payload = _finalize(manifest, tmp_path)

    assert "token_metrics" not in payload
