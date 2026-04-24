"""Content conversion for docpull (HTML to Markdown, frontmatter)."""

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

__all__ = [
    # Protocols
    "ContentExtractor",
    "MarkdownConverter",
    "SpecialCaseExtractor",
    # Implementations
    "MainContentExtractor",
    "HtmlToMarkdown",
    "FrontmatterBuilder",
    # Special-case extractors
    "DEFAULT_SPECIAL_CHAIN",
    "SpecialCaseResult",
    "detect_source_type",
    "find_mdx_source_url",
    "looks_like_spa",
    # Chunking
    "Chunk",
    "TokenCounter",
    "chunk_markdown",
]
