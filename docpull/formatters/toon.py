"""TOON (Terser Object Oriented Notation) formatter - compact format for LLMs."""

import re
from typing import Optional

from .base import BaseFormatter


class ToonFormatter(BaseFormatter):
    """TOON format converter (40-60% size reduction).

    TOON is a compact format optimized for LLM consumption,
    reducing file sizes significantly while preserving meaning.
    """

    def format_content(self, content: str, metadata: Optional[dict[str, any]] = None) -> str:
        """Convert markdown to TOON format.

        Args:
            content: Markdown content
            metadata: Optional metadata

        Returns:
            TOON formatted content
        """
        # Remove YAML frontmatter
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

        # Convert headers to compact format
        # # Header -> H1:Header
        content = re.sub(r"^#{6}\s+(.+)$", r"H6:\1", content, flags=re.MULTILINE)
        content = re.sub(r"^#{5}\s+(.+)$", r"H5:\1", content, flags=re.MULTILINE)
        content = re.sub(r"^#{4}\s+(.+)$", r"H4:\1", content, flags=re.MULTILINE)
        content = re.sub(r"^#{3}\s+(.+)$", r"H3:\1", content, flags=re.MULTILINE)
        content = re.sub(r"^#{2}\s+(.+)$", r"H2:\1", content, flags=re.MULTILINE)
        content = re.sub(r"^#\s+(.+)$", r"H1:\1", content, flags=re.MULTILINE)

        # Compact lists
        # - item -> •item
        content = re.sub(r"^\s*[-*]\s+", "•", content, flags=re.MULTILINE)

        # Compact code blocks
        # ```lang\ncode\n``` -> [CODE:lang]code[/CODE]
        def compact_code(match):
            lang = match.group(1) or ""
            code = match.group(2).strip()
            return f"[CODE:{lang}]{code}[/CODE]"

        content = re.sub(r"```(\w*)\n(.*?)\n```", compact_code, content, flags=re.DOTALL)

        # Compact inline code
        # `code` -> [c]code[/c]
        content = re.sub(r"`([^`]+)`", r"[c]\1[/c]", content)

        # Compact links
        # [text](url) -> [L:url]text[/L]
        content = re.sub(r"\[([^\]]+)\]\(([^\)]+)\)", r"[L:\2]\1[/L]", content)

        # Compact bold/italic
        # **bold** -> [b]bold[/b]
        content = re.sub(r"\*\*([^\*]+)\*\*", r"[b]\1[/b]", content)
        # *italic* -> [i]italic[/i]
        content = re.sub(r"\*([^\*]+)\*", r"[i]\1[/i]", content)

        # Remove excessive whitespace
        content = re.sub(r"\n{3,}", "\n\n", content)
        content = re.sub(r" {2,}", " ", content)

        # Add header with metadata if provided
        if metadata:
            header_parts = []
            if "url" in metadata:
                header_parts.append(f"URL:{metadata['url']}")
            if "title" in metadata:
                header_parts.append(f"TITLE:{metadata['title']}")

            if header_parts:
                content = "|".join(header_parts) + "\n---\n" + content

        return content.strip() + "\n"

    def get_file_extension(self) -> str:
        """Get TOON extension.

        Returns:
            '.toon'
        """
        return ".toon"
