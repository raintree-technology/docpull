"""SqliteSaveStep - SQLite output pipeline step."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ...models.document import DocumentRecord
from ...models.events import EventType, FetchEvent
from ...models.run import RunIdentity
from ...output_contract import document_context_fields, record_key
from ..base import EventEmitter, PageContext
from ..manifest import CorpusManifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SqliteSearchResult:
    """One full-text search hit from a docpull SQLite output database."""

    url: str
    title: str | None
    snippet: str
    rank: float
    record_key: str | None = None


class SqliteSaveStep:
    """
    Pipeline step that saves documents to a SQLite database.

    Creates a database with a documents table and inserts each page.
    Uses INSERT OR IGNORE to skip duplicates, then counts actual insertions.

    Example:
        sqlite_step = SqliteSaveStep(base_output_dir=Path("./docs"))

        # Execute for each page
        for url in urls:
            ctx = await sqlite_step.execute(ctx)

        # Close the database connection
        sqlite_step.close()
    """

    name = "save_sqlite"

    # Batch size for commits (balances performance vs durability)
    BATCH_SIZE = 50

    def __init__(
        self,
        base_output_dir: Path,
        filename: str = "documents.db",
        run_identity: RunIdentity | None = None,
        emit_chunks: bool = False,
    ) -> None:
        """
        Initialize the SQLite save step.

        Args:
            base_output_dir: Directory to write the database file
            filename: Name of the output database file
        """
        self._base_dir = base_output_dir.resolve()
        self._db_path = self._base_dir / filename
        self._emit_chunks = emit_chunks
        self._conn: sqlite3.Connection | None = None
        self._document_count = 0
        self._pending_count = 0  # Track uncommitted documents
        self._run_identity = run_identity
        self._manifest = CorpusManifest(
            self._base_dir,
            output_format="sqlite",
            run_identity=run_identity,
        )

    def _ensure_db(self) -> sqlite3.Connection:
        """Ensure the database and table exist."""
        if self._conn is None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)

            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version INTEGER NOT NULL DEFAULT 3,
                    record_key TEXT UNIQUE,
                    document_id TEXT,
                    chunk_id TEXT,
                    chunk_index INTEGER,
                    chunk_heading TEXT,
                    token_count INTEGER,
                    url TEXT NOT NULL,
                    title TEXT,
                    content TEXT,
                    content_hash TEXT,
                    source_type TEXT,
                    content_type TEXT,
                    mime_type TEXT,
                    rendered_at TEXT,
                    route TEXT,
                    rights TEXT,
                    source_citation_id TEXT,
                    record_citation_id TEXT,
                    metadata TEXT,
                    extraction TEXT,
                    fetched_at TEXT
                )
            """)
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS run_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            self._ensure_columns(
                self._conn,
                "documents",
                {
                    "schema_version": "INTEGER NOT NULL DEFAULT 3",
                    "record_key": "TEXT",
                    "document_id": "TEXT",
                    "chunk_id": "TEXT",
                    "chunk_index": "INTEGER",
                    "chunk_heading": "TEXT",
                    "token_count": "INTEGER",  # nosec B105
                    "content_hash": "TEXT",
                    "source_type": "TEXT",
                    "content_type": "TEXT",
                    "mime_type": "TEXT",
                    "rendered_at": "TEXT",
                    "route": "TEXT",
                    "rights": "TEXT",
                    "source_citation_id": "TEXT",
                    "record_citation_id": "TEXT",
                    "extraction": "TEXT",
                },
            )
            self._conn.execute("UPDATE documents SET document_id = record_key WHERE document_id IS NULL")
            self._conn.execute("UPDATE documents SET record_key = document_id WHERE record_key IS NULL")
            self._ensure_fts(self._conn)
            if self._run_identity:
                self._conn.execute(
                    "INSERT OR REPLACE INTO run_metadata (key, value) VALUES (?, ?)",
                    ("run", json.dumps(self._run_identity.model_dump(mode="json"), ensure_ascii=False)),
                )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON documents(url)")
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_record_key ON documents(record_key)"
            )
            self._conn.commit()

            logger.info(f"Initialized SQLite database at {self._db_path}")

        return self._conn

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
        """Add schema-v1 columns when opening a DB created by older docpull."""
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    @staticmethod
    def _ensure_fts(conn: sqlite3.Connection) -> None:
        """Create and backfill the local full-text search index."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(documents_fts)")}
        if existing and "record_key" not in existing:
            conn.execute("DROP TABLE documents_fts")
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                record_key UNINDEXED,
                url UNINDEXED,
                title,
                content,
                content_hash UNINDEXED
            )
        """)
        conn.execute("""
            INSERT INTO documents_fts (record_key, url, title, content, content_hash)
            SELECT d.record_key, d.url, d.title, d.content, d.content_hash
            FROM documents d
            WHERE d.content IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM documents_fts f
                  WHERE f.record_key = d.record_key
                     OR (f.record_key IS NULL AND f.url = d.url)
              )
        """)

    async def execute(
        self,
        ctx: PageContext,
        emit: EventEmitter | None = None,
    ) -> PageContext:
        """
        Execute the SQLite save step.

        Args:
            ctx: Page context with content to save
            emit: Optional callback to emit events

        Returns:
            PageContext (unchanged)
        """
        if ctx.should_skip or not ctx.markdown:
            return ctx

        conn = self._ensure_db()
        records = self._records_from_context(ctx)

        try:
            inserted = 0
            for record in records:
                row_key = record_key(record)
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO documents
                       (schema_version, record_key, document_id, chunk_id, chunk_index,
                        chunk_heading, token_count, url, title, content, content_hash,
                        source_type, content_type, mime_type, rendered_at, route, rights,
                        source_citation_id, record_citation_id, metadata, extraction, fetched_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.schema_version,
                        row_key,
                        record.document_id,
                        record.chunk_id,
                        record.chunk_index,
                        record.chunk_heading,
                        record.token_count,
                        record.url,
                        record.title,
                        record.content,
                        record.content_hash,
                        record.source_type,
                        record.content_type,
                        record.mime_type,
                        record.rendered_at,
                        json.dumps(record.route, ensure_ascii=False),
                        json.dumps(record.rights, ensure_ascii=False),
                        record.source_citation_id,
                        record.record_citation_id,
                        json.dumps(record.metadata, ensure_ascii=False),
                        json.dumps(record.extraction, ensure_ascii=False),
                        record.fetched_at,
                    ),
                )
                # Only count if a row was actually inserted (not ignored)
                if cursor.rowcount > 0:
                    inserted += 1
                    self._pending_count += 1
                    self._manifest.add_record(record, self._db_path)
                    conn.execute(
                        """
                        INSERT INTO documents_fts (record_key, url, title, content, content_hash)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (row_key, record.url, record.title, record.content, record.content_hash),
                    )
            self._document_count += inserted

            # Batch commits for performance
            if self._pending_count >= self.BATCH_SIZE:
                conn.commit()
                self._pending_count = 0

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.PAGE_SAVED,
                        url=ctx.url,
                        message=f"Saved to SQLite ({self._document_count} records)",
                    )
                )
            ctx.persisted_path = self._db_path

        except sqlite3.Error as e:
            logger.error(f"SQLite error saving {ctx.url}: {e}")
            ctx.mark_failed(f"SQLite error: {e}")

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.FETCH_FAILED,
                        url=ctx.url,
                        error=str(e),
                        message=f"SQLite error: {e}",
                    )
                )

        return ctx

    def _records_from_context(self, ctx: PageContext) -> list[DocumentRecord]:
        if self._emit_chunks and ctx.chunks:
            records: list[DocumentRecord] = []
            for chunk in ctx.chunks:
                records.append(
                    DocumentRecord.from_page(
                        url=ctx.url,
                        title=ctx.title,
                        content=str(getattr(chunk, "text", "")),
                        metadata=ctx.metadata,
                        extraction=ctx.extraction_info,
                        source_type=ctx.source_type,
                        run_identity=self._run_identity,
                        **document_context_fields(ctx, output_format="sqlite"),
                        chunk_index=getattr(chunk, "index", 0),
                        chunk_heading=getattr(chunk, "heading", None),
                        token_count=getattr(chunk, "token_count", None),
                    )
                )
            return records
        return [
            DocumentRecord.from_page(
                url=ctx.url,
                title=ctx.title,
                content=ctx.markdown or "",
                metadata=ctx.metadata,
                extraction=ctx.extraction_info,
                source_type=ctx.source_type,
                run_identity=self._run_identity,
                **document_context_fields(ctx, output_format="sqlite"),
            )
        ]

    def close(self) -> None:
        """Close the database connection, committing any pending changes."""
        if self._conn:
            # Commit any remaining uncommitted documents
            if self._pending_count > 0:
                self._conn.commit()
                self._pending_count = 0
            self._manifest.finalize()
            self._conn.close()
            self._conn = None
            logger.info(f"Closed SQLite database with {self._document_count} documents")

    @property
    def document_count(self) -> int:
        """Return the number of saved documents."""
        return self._document_count

    @property
    def db_path(self) -> Path:
        """Return the path to the database file."""
        return self._db_path


def search_sqlite_documents(
    db_path: Path,
    query: str,
    *,
    limit: int = 10,
) -> list[SqliteSearchResult]:
    """Search a docpull SQLite output database with FTS5.

    Args:
        db_path: Path to ``documents.db``.
        query: FTS5 query string.
        limit: Maximum number of hits to return.

    Returns:
        Search hits ordered by FTS rank.
    """
    if not query.strip():
        return []
    if limit < 1:
        return []
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT
                record_key,
                url,
                title,
                snippet(documents_fts, 3, '[', ']', ' ... ', 24) AS snippet,
                bm25(documents_fts) AS rank
            FROM documents_fts
            WHERE documents_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
    finally:
        conn.close()
    return [
        SqliteSearchResult(
            record_key=str(row[0]) if row[0] is not None else None,
            url=str(row[1]),
            title=str(row[2]) if row[2] is not None else None,
            snippet=str(row[3] or ""),
            rank=float(row[4] or 0.0),
        )
        for row in rows
    ]
