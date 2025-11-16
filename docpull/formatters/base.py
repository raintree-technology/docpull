"""Base formatter interface."""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Union


class BaseFormatter(ABC):
    """Base class for output formatters.

    Formatters convert fetched content to different output formats
    (markdown, TOON, JSON, SQLite, etc.).
    """

    def __init__(self, output_dir: Optional[Path] = None, **kwargs: Union[str, int, bool]):
        """Initialize formatter.

        Args:
            output_dir: Output directory for formatted files
            **kwargs: Formatter-specific options
        """
        self.output_dir = Path(output_dir) if output_dir else Path(".")
        self.options = kwargs
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    @abstractmethod
    def format_content(
        self, content: str, metadata: Optional[dict[str, Union[str, int, None]]] = None
    ) -> str:
        """Format content to target format.

        Args:
            content: Content to format
            metadata: Optional metadata (url, title, etc.)

        Returns:
            Formatted content
        """
        pass

    @abstractmethod
    def get_file_extension(self) -> str:
        """Get file extension for this format.

        Returns:
            File extension including dot (e.g., '.md', '.json')
        """
        pass

    def format(  # noqa: A003
        self,
        content: str,
        metadata: Optional[dict[str, Union[str, int, None]]] = None,
    ) -> str:
        """Alias for format_content() for backward compatibility.

        Args:
            content: Content to format
            metadata: Optional metadata

        Returns:
            Formatted content
        """
        return self.format_content(content, metadata)

    def get_extension(self) -> str:
        """Alias for get_file_extension() for backward compatibility.

        Returns:
            File extension including dot
        """
        return self.get_file_extension()

    def save_formatted(
        self,
        content: str,
        file_path: Path,
        metadata: Optional[dict[str, Union[str, int, None]]] = None,
    ) -> Path:
        """Format and save content to file.

        Args:
            content: Content to format and save
            file_path: Destination file path
            metadata: Optional metadata

        Returns:
            Path to saved file
        """
        formatted = self.format_content(content, metadata)

        # Ensure output directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Save formatted content
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(formatted)

        self.logger.debug(f"Saved formatted content to {file_path}")

        return file_path
