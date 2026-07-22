"""LlamaIndex reader for local DocPull context packs."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._records import iter_pack_records


class DocpullPackReader:
    """Load a DocPull pack directory as LlamaIndex ``Document`` objects.

    Follows the LlamaIndex reader convention (``load_data`` plus
    ``lazy_load_data``). ``llama_index`` is imported only when documents are
    requested, so this module stays importable without the framework.
    """

    def __init__(self, pack_dir: Path | str) -> None:
        self.pack_dir = Path(pack_dir)

    def lazy_load_data(self) -> Iterator[Any]:
        """Yield one ``llama_index.core.schema.Document`` per pack record."""
        document_cls = _document_class()
        for record in iter_pack_records(self.pack_dir):
            yield document_cls(text=record["content"], metadata=record["metadata"])

    def load_data(self) -> list[Any]:
        """Load every pack record eagerly."""
        return list(self.lazy_load_data())


def _document_class() -> Any:
    try:
        module: Any = importlib.import_module("llama_index.core.schema")
    except ImportError as err:
        raise ImportError(
            "DocpullPackReader requires llama-index-core. Install it with `pip install llama-index-core`."
        ) from err
    return module.Document
