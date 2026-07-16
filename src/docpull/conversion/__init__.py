"""Content conversion, extraction, frontmatter, and chunking."""
# ruff: noqa: F401 - TYPE_CHECKING imports document lazy public re-exports.

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

_LAZY_EXPORTS = {
    **{name: (".chunking", name) for name in ("Chunk", "TokenCounter", "chunk_markdown")},
    "MainContentExtractor": (".extractor", "MainContentExtractor"),
    **{name: (".markdown", name) for name in ("FrontmatterBuilder", "HtmlToMarkdown")},
    **{name: (".protocols", name) for name in ("ContentExtractor", "MarkdownConverter")},
    **{
        name: (".special_cases", target)
        for name, target in (
            ("DEFAULT_SPECIAL_CHAIN", "DEFAULT_CHAIN"),
            ("SpecialCaseExtractor", "SpecialCaseExtractor"),
            ("SpecialCaseResult", "SpecialCaseResult"),
            ("detect_source_type", "detect_source_type"),
            ("find_mdx_source_url", "find_mdx_source_url"),
            ("looks_like_spa", "looks_like_spa"),
        )
    },
}

__all__ = [
    "ContentExtractor",
    "MarkdownConverter",
    "SpecialCaseExtractor",
    "MainContentExtractor",
    "HtmlToMarkdown",
    "FrontmatterBuilder",
    "DEFAULT_SPECIAL_CHAIN",
    "SpecialCaseResult",
    "detect_source_type",
    "find_mdx_source_url",
    "looks_like_spa",
    "Chunk",
    "TokenCounter",
    "chunk_markdown",
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
    from .chunking import Chunk, TokenCounter, chunk_markdown
    from .extractor import MainContentExtractor
    from .markdown import FrontmatterBuilder, HtmlToMarkdown
    from .protocols import ContentExtractor, MarkdownConverter
    from .special_cases import (
        DEFAULT_CHAIN as DEFAULT_SPECIAL_CHAIN,
    )
    from .special_cases import (
        SpecialCaseExtractor,
        SpecialCaseResult,
        detect_source_type,
        find_mdx_source_url,
        looks_like_spa,
    )


assert set(_LAZY_EXPORTS) == set(__all__)
