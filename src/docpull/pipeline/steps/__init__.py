"""Pipeline steps for fetch, conversion, deduplication, and persistence."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    "ChunkStep": (".chunk", "ChunkStep"),
    "ConvertStep": (".convert", "ConvertStep"),
    "DedupStep": (".dedup", "DedupStep"),
    "FetchStep": (".fetch", "FetchStep"),
    "MetadataStep": (".metadata", "MetadataStep"),
    "RenderStep": (".render", "RenderStep"),
    "SaveStep": (".save", "SaveStep"),
    "JsonSaveStep": (".save_json", "JsonSaveStep"),
    "NdjsonSaveStep": (".save_ndjson", "NdjsonSaveStep"),
    "OkfSaveStep": (".save_okf", "OkfSaveStep"),
    **{
        name: (".save_sqlite", name)
        for name in ("SqliteSaveStep", "SqliteSearchResult", "search_sqlite_documents")
    },
    "ValidateStep": (".validate", "ValidateStep"),
}

__all__ = [
    "ChunkStep",
    "ConvertStep",
    "DedupStep",
    "FetchStep",
    "JsonSaveStep",
    "MetadataStep",
    "NdjsonSaveStep",
    "OkfSaveStep",
    "RenderStep",
    "SaveStep",
    "SqliteSaveStep",
    "SqliteSearchResult",
    "ValidateStep",
    "search_sqlite_documents",
]


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .chunk import ChunkStep
    from .convert import ConvertStep
    from .dedup import DedupStep
    from .fetch import FetchStep
    from .metadata import MetadataStep
    from .render import RenderStep
    from .save import SaveStep
    from .save_json import JsonSaveStep
    from .save_ndjson import NdjsonSaveStep
    from .save_okf import OkfSaveStep
    from .save_sqlite import SqliteSaveStep, SqliteSearchResult, search_sqlite_documents
    from .validate import ValidateStep


assert set(_LAZY_EXPORTS) == set(__all__)
