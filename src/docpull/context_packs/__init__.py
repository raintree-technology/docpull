"""Typed local context-pack workflows."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from ..surface import PUBLIC_CONTEXT_PACK_EXPORTS

_LAZY_EXPORTS = {
    "build_brand_pack": (".brand", "build_brand_pack"),
    **{name: (".dataset", name) for name in ("async_build_dataset_pack", "build_dataset_pack")},
    "build_feed_pack": (".feed", "build_feed_pack"),
    "build_image_pack": (".visuals", "build_image_pack"),
    "build_openapi_pack": (".openapi", "build_openapi_pack"),
    **{name: (".package", name) for name in ("async_build_package_pack", "build_package_pack")},
    **{name: (".paper", name) for name in ("async_build_paper_pack", "build_paper_pack")},
    "build_policy_pack": (".policy_pack", "build_policy_pack"),
    "build_relationship_pack": (".relationship", "build_relationship_pack"),
    "build_product_pack": (".product", "build_product_pack"),
    **{name: (".repo", name) for name in ("async_build_repo_pack", "build_repo_pack")},
    **{name: (".standards", name) for name in ("async_build_standards_pack", "build_standards_pack")},
    "build_styleguide_pack": (".styleguide", "build_styleguide_pack"),
    **{name: (".transcript", name) for name in ("async_build_transcript_pack", "build_transcript_pack")},
    **{name: (".wiki", name) for name in ("async_build_wiki_pack", "build_wiki_pack")},
    "capture_screenshot_pack": (".visuals", "capture_screenshot_pack"),
    "build_website_pack": (".website", "build_website_pack"),
    "validate_website_snapshot_pack": (".website", "validate_website_snapshot_pack"),
}

__all__ = list(PUBLIC_CONTEXT_PACK_EXPORTS)


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
    from .brand import build_brand_pack
    from .dataset import async_build_dataset_pack, build_dataset_pack
    from .feed import build_feed_pack
    from .openapi import build_openapi_pack
    from .package import async_build_package_pack, build_package_pack
    from .paper import async_build_paper_pack, build_paper_pack
    from .policy_pack import build_policy_pack
    from .product import build_product_pack
    from .relationship import build_relationship_pack
    from .repo import async_build_repo_pack, build_repo_pack
    from .standards import async_build_standards_pack, build_standards_pack
    from .styleguide import build_styleguide_pack
    from .transcript import async_build_transcript_pack, build_transcript_pack
    from .visuals import build_image_pack, capture_screenshot_pack
    from .website import build_website_pack, validate_website_snapshot_pack
    from .wiki import async_build_wiki_pack, build_wiki_pack


assert tuple(__all__) == PUBLIC_CONTEXT_PACK_EXPORTS
assert set(_LAZY_EXPORTS) == set(__all__)
