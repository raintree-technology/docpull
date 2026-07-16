"""HTTP client protocols, transport, and rate limiting."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    "AsyncHttpClient": (".client", "AsyncHttpClient"),
    "HttpClient": (".protocols", "HttpClient"),
    "HttpResponse": (".protocols", "HttpResponse"),
    "AdaptiveRateLimiter": (".rate_limiter", "AdaptiveRateLimiter"),
    "PerHostRateLimiter": (".rate_limiter", "PerHostRateLimiter"),
}

__all__ = [
    "AdaptiveRateLimiter",
    "AsyncHttpClient",
    "HttpClient",
    "HttpResponse",
    "PerHostRateLimiter",
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
    from .client import AsyncHttpClient
    from .protocols import HttpClient, HttpResponse
    from .rate_limiter import AdaptiveRateLimiter, PerHostRateLimiter


assert set(_LAZY_EXPORTS) == set(__all__)
