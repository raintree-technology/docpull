"""Security validation, robots policy, and safe download controls."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    **{name: (".download_policy", name) for name in ("SafeDownloadPolicy", "UnsafeDownloadError")},
    **{name: (".injection", name) for name in ("InjectionScreenResult", "InjectionSpan", "screen_text")},
    **{
        name: (".optout", name)
        for name in ("OptOutDecision", "evaluate_optout", "parse_robots_meta", "parse_x_robots_tag")
    },
    "RobotsChecker": (".robots", "RobotsChecker"),
    **{name: (".url_validator", name) for name in ("UrlValidationResult", "UrlValidator")},
}
__all__ = [
    "InjectionScreenResult",
    "InjectionSpan",
    "OptOutDecision",
    "RobotsChecker",
    "SafeDownloadPolicy",
    "UnsafeDownloadError",
    "UrlValidationResult",
    "UrlValidator",
    "evaluate_optout",
    "parse_robots_meta",
    "parse_x_robots_tag",
    "screen_text",
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
    from .injection import InjectionScreenResult, InjectionSpan, screen_text
    from .optout import OptOutDecision, evaluate_optout, parse_robots_meta, parse_x_robots_tag
    from .robots import RobotsChecker
    from .url_validator import UrlValidationResult, UrlValidator


assert set(_LAZY_EXPORTS) == set(__all__)
