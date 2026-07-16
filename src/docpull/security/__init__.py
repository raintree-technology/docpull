"""Security validation, robots policy, and safe download controls."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    **{name: (".download_policy", name) for name in ("SafeDownloadPolicy", "UnsafeDownloadError")},
    "RobotsChecker": (".robots", "RobotsChecker"),
    **{name: (".url_validator", name) for name in ("UrlValidationResult", "UrlValidator")},
}
__all__ = [
    "RobotsChecker",
    "SafeDownloadPolicy",
    "UnsafeDownloadError",
    "UrlValidationResult",
    "UrlValidator",
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
    from .download_policy import SafeDownloadPolicy, UnsafeDownloadError
    from .robots import RobotsChecker
    from .url_validator import UrlValidationResult, UrlValidator


assert set(_LAZY_EXPORTS) == set(__all__)
