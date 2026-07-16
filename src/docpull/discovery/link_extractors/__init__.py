"""Link extraction strategies for URL discovery."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    "EnhancedLinkExtractor": (".enhanced", "EnhancedLinkExtractor"),
    "LinkExtractor": (".protocols", "LinkExtractor"),
    "StaticLinkExtractor": (".static", "StaticLinkExtractor"),
}
__all__ = ["LinkExtractor", "StaticLinkExtractor", "EnhancedLinkExtractor"]


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
    from .enhanced import EnhancedLinkExtractor
    from .protocols import LinkExtractor
    from .static import StaticLinkExtractor


assert set(_LAZY_EXPORTS) == set(__all__)
