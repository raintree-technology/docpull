"""Post-processing pipeline for fetched documentation."""

from .base import BaseProcessor, ProcessorContext, ProcessorResult
from .content_filter import ContentFilter
from .deduplicator import Deduplicator
from .language_filter import LanguageFilter
from .size_limiter import SizeLimiter

__all__ = [
    "BaseProcessor",
    "ProcessorContext",
    "ProcessorResult",
    "Deduplicator",
    "LanguageFilter",
    "SizeLimiter",
    "ContentFilter",
]
