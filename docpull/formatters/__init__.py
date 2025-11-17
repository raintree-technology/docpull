"""Output format converters for documentation."""

from typing import Union

from .base import BaseFormatter
from .json import JSONFormatter
from .markdown import MarkdownFormatter
from .sqlite import SqliteFormatter
from .toon import ToonFormatter

# Alias for backward compatibility
JsonFormatter = JSONFormatter

__all__ = [
    "BaseFormatter",
    "MarkdownFormatter",
    "ToonFormatter",
    "JSONFormatter",
    "JsonFormatter",
    "SqliteFormatter",
]


def get_formatter(format_name: str, **kwargs: Union[str, int, bool]) -> BaseFormatter:
    """Get formatter instance by name.

    Args:
        format_name: Format name ('markdown', 'toon', 'json', 'sqlite')
        **kwargs: Formatter-specific configuration

    Returns:
        Formatter instance

    Raises:
        ValueError: If format name is unknown
    """
    formatters = {
        "markdown": MarkdownFormatter,
        "toon": ToonFormatter,
        "json": JSONFormatter,
        "sqlite": SqliteFormatter,
    }

    formatter_class = formatters.get(format_name.lower())
    if not formatter_class:
        raise ValueError(
            f"Unknown format: {format_name}. " f"Available formats: {', '.join(formatters.keys())}"
        )

    return formatter_class(**kwargs)  # type: ignore[no-any-return,abstract,arg-type]
