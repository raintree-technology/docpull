"""SQLite formatter - searchable database output."""

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Optional

from .base import BaseFormatter


class SqliteFormatter(BaseFormatter):
    """SQLite database format for searchable documentation.

    Creates a SQLite database with full-text search capabilities.
    All documents are stored in a single database file.
    """

    def __init__(self, output_dir: Path, **kwargs):
        """Initialize SQLite formatter.

        Args:
            output_dir: Output directory
            **kwargs: Options (db_name: database filename)
        """
        super().__init__(output_dir, **kwargs)

        self.db_name = self.options.get("db_name", "docs.db")
        self.db_path = self.output_dir / self.db_name

        self._init_database()

    def _init_database(self):
        """Initialize database schema."""
        self.output_dir.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Create tables
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                url TEXT,
                title TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                fetched_at TEXT,
                size INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS sections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                section_order INTEGER NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            )
        """
        )

        # Create FTS (Full-Text Search) virtual table
        cursor.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                file_path,
                url,
                title,
                content,
                content='documents',
                content_rowid='id'
            )
        """
        )

        # Create triggers to keep FTS in sync
        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, file_path, url, title, content)
                VALUES (new.id, new.file_path, new.url, new.title, new.content);
            END
        """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
                DELETE FROM documents_fts WHERE rowid = old.id;
            END
        """
        )

        cursor.execute(
            """
            CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
                UPDATE documents_fts SET
                    file_path = new.file_path,
                    url = new.url,
                    title = new.title,
                    content = new.content
                WHERE rowid = new.id;
            END
        """
        )

        # Create indexes
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_url ON documents(url)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_hash ON documents(content_hash)")

        conn.commit()
        conn.close()

        self.logger.info(f"Initialized SQLite database at {self.db_path}")

    def _extract_title(self, content: str) -> Optional[str]:
        """Extract title from content (first H1 header).

        Args:
            content: Markdown content

        Returns:
            Title or None
        """
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else None

    def _extract_sections(self, content: str) -> list:
        """Extract sections for section table.

        Args:
            content: Markdown content

        Returns:
            List of (level, title, content) tuples
        """
        # Remove frontmatter
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

        sections = []
        lines = content.split("\n")
        current_section = None
        section_order = 0

        for line in lines:
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)

            if header_match:
                if current_section:
                    sections.append(current_section)
                    section_order += 1

                level = len(header_match.group(1))
                title = header_match.group(2).strip()

                current_section = (level, title, [], section_order)
            elif current_section:
                current_section[2].append(line)

        if current_section:
            sections.append(current_section)

        # Convert to tuples
        return [(lvl, title, "\n".join(content).strip(), order) for lvl, title, content, order in sections]

    def format_content(self, content: str, metadata: Optional[dict[str, any]] = None) -> str:
        """Insert content into SQLite database.

        Args:
            content: Markdown content
            metadata: Metadata with url, file_path, etc.

        Returns:
            Status message (not actually used by save_formatted)
        """
        metadata = metadata or {}

        file_path = metadata.get("file_path", "unknown")
        url = metadata.get("url")
        title = metadata.get("title") or self._extract_title(content)
        fetched_at = metadata.get("fetched_at")
        size = len(content)

        # Compute hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        try:
            # Insert or replace document
            cursor.execute(
                """
                INSERT OR REPLACE INTO documents
                (file_path, url, title, content, content_hash, fetched_at, size)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
                (file_path, url, title, content, content_hash, fetched_at, size),
            )

            doc_id = cursor.lastrowid

            # Insert sections
            sections = self._extract_sections(content)
            for level, section_title, section_content, section_order in sections:
                cursor.execute(
                    """
                    INSERT INTO sections (document_id, level, title, content, section_order)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    (doc_id, level, section_title, section_content, section_order),
                )

            conn.commit()

            self.logger.debug(f"Inserted document into database: {file_path}")

            return f"Inserted into SQLite: {file_path}"

        except Exception as e:
            conn.rollback()
            self.logger.error(f"Failed to insert {file_path}: {e}")
            raise
        finally:
            conn.close()

    def save_formatted(
        self, content: str, file_path: Path, metadata: Optional[dict[str, any]] = None
    ) -> Path:
        """Save to SQLite database (overrides base method).

        Args:
            content: Content to save
            file_path: Original file path (used as document identifier)
            metadata: Metadata

        Returns:
            Path to database file
        """
        # Add file_path to metadata
        metadata = metadata or {}
        metadata["file_path"] = str(file_path)

        # Insert into database
        self.format_content(content, metadata)

        return self.db_path

    def get_file_extension(self) -> str:
        """Get SQLite extension.

        Returns:
            '.db'
        """
        return ".db"
