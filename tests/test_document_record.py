"""Tests for stable document and chunk identity."""

from __future__ import annotations

from docpull.models.document import DocumentRecord


def test_document_id_is_stable_for_same_url_and_content() -> None:
    first = DocumentRecord.from_page(
        url="https://docs.example.com/a",
        title="A",
        content="# A\n\nBody",
    )
    second = DocumentRecord.from_page(
        url="https://docs.example.com/a",
        title="Changed title",
        content="# A\n\nBody",
    )

    assert first.document_id == second.document_id
    assert first.content_hash == second.content_hash


def test_document_id_changes_when_content_changes() -> None:
    first = DocumentRecord.from_page(url="https://docs.example.com/a", content="old")
    second = DocumentRecord.from_page(url="https://docs.example.com/a", content="new")

    assert first.document_id != second.document_id
    assert first.content_hash != second.content_hash


def test_chunk_id_includes_chunk_position_heading_and_content() -> None:
    first = DocumentRecord.from_page(
        url="https://docs.example.com/a",
        content="same chunk",
        chunk_index=0,
        chunk_heading="Install",
    )
    same = DocumentRecord.from_page(
        url="https://docs.example.com/a",
        content="same chunk",
        chunk_index=0,
        chunk_heading="Install",
    )
    moved = DocumentRecord.from_page(
        url="https://docs.example.com/a",
        content="same chunk",
        chunk_index=1,
        chunk_heading="Install",
    )

    assert first.chunk_id == same.chunk_id
    assert first.chunk_id != moved.chunk_id
