"""JsonSaveStep - JSON output pipeline step with streaming writes."""

import contextlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Optional, TextIO

from ...models.events import EventType, FetchEvent
from ..base import EventEmitter, PageContext

logger = logging.getLogger(__name__)


class JsonSaveStep:
    """
    Pipeline step that streams documents to a JSON file.

    Uses streaming writes to avoid holding all documents in memory.
    Each document is written immediately, with the final structure
    assembled on finalize().

    The output format is:
    {
        "generated_at": "...",
        "document_count": N,
        "documents": [...]
    }

    Example:
        json_step = JsonSaveStep(base_output_dir=Path("./docs"))

        # Execute for each page (streams to temp file)
        for url in urls:
            ctx = await json_step.execute(ctx)

        # Finalize to complete the JSON file
        json_step.finalize()
    """

    name = "save_json"

    def __init__(
        self,
        base_output_dir: Path,
        filename: str = "documents.json",
    ) -> None:
        """
        Initialize the JSON save step.

        Args:
            base_output_dir: Directory to write the JSON file
            filename: Name of the output JSON file
        """
        self._base_dir = base_output_dir.resolve()
        self._output_file = self._base_dir / filename
        self._document_count = 0
        self._temp_file: Optional[TextIO] = None
        self._temp_path: Optional[str] = None
        self._first_doc = True

    def _ensure_temp_file(self) -> TextIO:
        """Create temp file for streaming writes if not already open."""
        if self._temp_file is None:
            self._base_dir.mkdir(parents=True, exist_ok=True)
            fd, self._temp_path = tempfile.mkstemp(
                suffix=".json",
                prefix=".docpull_",
                dir=self._base_dir,
            )
            self._temp_file = os.fdopen(fd, "w", encoding="utf-8")
            # Write opening structure - we'll complete it in finalize()
            self._temp_file.write('{\n  "documents": [\n')
            self._first_doc = True
        return self._temp_file

    async def execute(
        self,
        ctx: PageContext,
        emit: Optional[EventEmitter] = None,
    ) -> PageContext:
        """
        Execute the JSON streaming step.

        Args:
            ctx: Page context with content to save
            emit: Optional callback to emit events

        Returns:
            PageContext (unchanged)
        """
        if ctx.should_skip or not ctx.markdown:
            return ctx

        doc = {
            "url": ctx.url,
            "title": ctx.title,
            "content": ctx.markdown,
            "metadata": ctx.metadata,
            "fetched_at": datetime.now().isoformat(),
        }

        f = self._ensure_temp_file()

        # Write comma separator between documents
        if not self._first_doc:
            f.write(",\n")
        self._first_doc = False

        # Write document with indentation
        doc_json = json.dumps(doc, indent=2, ensure_ascii=False)
        # Indent each line by 4 spaces (2 for documents array + 2 for item)
        indented = "\n".join("    " + line for line in doc_json.split("\n"))
        f.write(indented)

        self._document_count += 1

        if emit:
            emit(
                FetchEvent(
                    type=EventType.PAGE_SAVED,
                    url=ctx.url,
                    message=f"Streamed to JSON ({self._document_count} docs)",
                )
            )

        return ctx

    def finalize(self) -> Path:
        """
        Complete the JSON file and move to final location.

        Returns:
            Path to the written JSON file
        """
        if self._temp_file is None:
            # No documents written - create empty structure
            self._base_dir.mkdir(parents=True, exist_ok=True)
            output = {
                "generated_at": datetime.now().isoformat(),
                "document_count": 0,
                "documents": [],
            }
            with open(self._output_file, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved 0 documents to {self._output_file}")
            return self._output_file

        try:
            # Close the documents array and add metadata
            self._temp_file.write("\n  ],\n")
            self._temp_file.write(f'  "generated_at": "{datetime.now().isoformat()}",\n')
            self._temp_file.write(f'  "document_count": {self._document_count}\n')
            self._temp_file.write("}\n")
            self._temp_file.close()
            self._temp_file = None

            # Atomic rename
            os.replace(self._temp_path, self._output_file)
            logger.info(f"Saved {self._document_count} documents to {self._output_file}")

        except Exception:
            # Clean up on error
            if self._temp_file:
                self._temp_file.close()
                self._temp_file = None
            if self._temp_path and os.path.exists(self._temp_path):
                os.unlink(self._temp_path)
            raise

        return self._output_file

    def __del__(self) -> None:
        """Clean up temp file if not finalized."""
        if self._temp_file:
            with contextlib.suppress(Exception):
                self._temp_file.close()
        if self._temp_path and os.path.exists(self._temp_path):
            with contextlib.suppress(Exception):
                os.unlink(self._temp_path)

    @property
    def document_count(self) -> int:
        """Return the number of saved documents."""
        return self._document_count
