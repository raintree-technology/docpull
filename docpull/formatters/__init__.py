"""Output format converters for documentation."""

from .base import BaseFormatter
from .json import JsonFormatter
from .markdown import MarkdownFormatter
from .sqlite import SqliteFormatter
from .toon import ToonFormatter

__all__ = [
    "BaseFormatter",
    "MarkdownFormatter",
    "ToonFormatter",
    "JsonFormatter",
    "SqliteFormatter",
]


def get_formatter(format_name: str, **kwargs) -> BaseFormatter:
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
        "json": JsonFormatter,
        "sqlite": SqliteFormatter,
    }

    formatter_class = formatters.get(format_name.lower())
    if not formatter_class:
        raise ValueError(
            f"Unknown format: {format_name}. " f"Available formats: {', '.join(formatters.keys())}"
        )

    return formatter_class(**kwargs)
