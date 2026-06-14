"""Tests for SQLite output schema compatibility."""

from __future__ import annotations

import sqlite3

import pytest

from docpull.pipeline.base import PageContext
from docpull.pipeline.steps.save_sqlite import SqliteSaveStep, search_sqlite_documents


@pytest.mark.asyncio
async def test_sqlite_save_migrates_legacy_documents_table(tmp_path):
    db_path = tmp_path / "documents.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT UNIQUE NOT NULL,
            title TEXT,
            content TEXT,
            metadata TEXT,
            fetched_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    step = SqliteSaveStep(tmp_path)
    ctx = PageContext(
        url="https://example.com/page",
        output_path=tmp_path / "page.md",
        markdown="# Page\n\nBody",
        title="Page",
    )

    await step.execute(ctx)
    step.close()

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
        assert {"schema_version", "content_hash", "source_type", "extraction"} <= columns
        row = conn.execute("SELECT url, schema_version, content_hash FROM documents").fetchone()
        assert row[0] == "https://example.com/page"
        assert row[1] == 1
        assert row[2]
        fts_row = conn.execute("SELECT url FROM documents_fts WHERE documents_fts MATCH 'Body'").fetchone()
        assert fts_row == ("https://example.com/page",)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_sqlite_output_is_full_text_searchable(tmp_path):
    step = SqliteSaveStep(tmp_path)
    ctx = PageContext(
        url="https://example.com/install",
        output_path=tmp_path / "install.md",
        markdown="# Install\n\nUse the orbital wrench package manager for setup.",
        title="Install",
    )

    await step.execute(ctx)
    step.close()

    hits = search_sqlite_documents(tmp_path / "documents.db", "orbital")

    assert len(hits) == 1
    assert hits[0].url == "https://example.com/install"
    assert hits[0].title == "Install"
    assert "[orbital]" in hits[0].snippet


def test_sqlite_search_missing_database_returns_empty_without_creating_file(tmp_path):
    db_path = tmp_path / "missing.db"

    hits = search_sqlite_documents(db_path, "anything")

    assert hits == []
    assert not db_path.exists()
