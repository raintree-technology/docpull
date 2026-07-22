"""LangChain document loader for local DocPull context packs."""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ._records import iter_pack_records


class DocpullPackLoader:
    """Load a DocPull pack directory as LangChain ``Document`` objects.

    Implements the LangChain ``BaseLoader`` duck type (``lazy_load`` and
    ``load``). ``langchain_core`` is imported only when documents are
    requested, so this module stays importable without the framework.
    """

    def __init__(self, pack_dir: Path | str) -> None:
        self.pack_dir = Path(pack_dir)

    def lazy_load(self) -> Iterator[Any]:
        """Yield one ``langchain_core.documents.Document`` per pack record."""
        document_cls = _document_class()
        for record in iter_pack_records(self.pack_dir):
            yield document_cls(page_content=record["content"], metadata=record["metadata"])

    def load(self) -> list[Any]:
        """Load every pack record eagerly."""
        return list(self.lazy_load())


def _document_class() -> Any:
    try:
        module: Any = importlib.import_module("langchain_core.documents")
    except ImportError as err:
        raise ImportError(
            "DocpullPackLoader requires langchain-core. Install it with `pip install langchain-core`."
        ) from err
    return module.Document
