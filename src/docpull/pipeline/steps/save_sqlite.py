"""SqliteSaveStep - SQLite output pipeline step."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


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
    ) -> None:
        """
        Initialize the SQLite save step.

        Args:
            base_output_dir: Directory to write the database file
            filename: Name of the output database file
        """
        self._base_dir = base_output_dir.resolve()
        self._db_path = self._base_dir / filename
        self._conn: Optional[sqlite3.Connection] = None
        self._document_count = 0
        self._pending_count = 0  # Track uncommitted documents

    def _ensure_db(self) -> sqlite3.Connection:
        """Ensure the database and table exist."""
        if self._conn is None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path)

            # Create table with index on URL
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE NOT NULL,
                    title TEXT,
                    content TEXT,
                    metadata TEXT,
                    fetched_at TEXT
                )
            """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON documents(url)")
            self._conn.commit()

            logger.info(f"Initialized SQLite database at {self._db_path}")

        return self._conn

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
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

        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO documents
                   (url, title, content, metadata, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    ctx.url,
                    ctx.title,
                    ctx.markdown,
                    json.dumps(ctx.metadata, ensure_ascii=False),
                    datetime.now().isoformat(),
                ),
            )
            # Only count if a row was actually inserted (not ignored)
            if cursor.rowcount > 0:
                self._document_count += 1
                self._pending_count += 1

            # Batch commits for performance
            if self._pending_count >= self.BATCH_SIZE:
                conn.commit()
                self._pending_count = 0

            if emit:
                emit(
                    FetchEvent(
                        type=EventType.PAGE_SAVED,
                        url=ctx.url,
                        message=f"Saved to SQLite ({self._document_count} docs)",
                    )
                )

        except sqlite3.Error as e:
            logger.error(f"SQLite error saving {ctx.url}: {e}")
            ctx.error = f"SQLite error: {e}"

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

    def close(self) -> None:
        """Close the database connection, committing any pending changes."""
        if self._conn:
            # Commit any remaining uncommitted documents
            if self._pending_count > 0:
                self._conn.commit()
                self._pending_count = 0
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
