"""Markdown formatter (default format)."""

from typing import Optional

from .base import BaseFormatter


class MarkdownFormatter(BaseFormatter):
    """Standard markdown format (current default).

    This is the default format used by docpull.
    """

    def format_content(self, content: str, metadata: Optional[dict[str, any]] = None) -> str:
        """Format as markdown with optional frontmatter.

        Args:
            content: Markdown content
            metadata: Optional metadata for frontmatter

        Returns:
            Markdown with frontmatter
        """
        if not metadata or not self.options.get("include_frontmatter", True):
            return content

        # Add YAML frontmatter
        frontmatter = ["---"]

        if "url" in metadata:
            frontmatter.append(f"url: {metadata['url']}")
        if "title" in metadata:
            frontmatter.append(f"title: {metadata['title']}")
        if "fetched_at" in metadata:
            frontmatter.append(f"fetched_at: {metadata['fetched_at']}")

        frontmatter.append("---\n")

        return "\n".join(frontmatter) + content

    def get_file_extension(self) -> str:
        """Get markdown extension.

        Returns:
            '.md'
        """
        return ".md"
