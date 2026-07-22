"""Framework loaders for local DocPull context packs."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = ["DocpullPackLoader", "DocpullPackReader"]

_EXPORTS = {
    "DocpullPackLoader": ".langchain",
    "DocpullPackReader": ".llamaindex",
}


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .langchain import DocpullPackLoader
    from .llamaindex import DocpullPackReader
