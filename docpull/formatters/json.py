"""JSON formatter - structured JSON output."""

import json
import re
from typing import Optional, Union

from .base import BaseFormatter


class JSONFormatter(BaseFormatter):
    """JSON format for structured data export.

    Converts markdown to structured JSON with metadata,
    headers, and content sections.
    """

    def extract_sections(self, content: str) -> list[dict[str, Union[str, int, None]]]:
        """Extract sections from markdown content.

        Args:
            content: Markdown content

        Returns:
            List of section dicts with level, title, content
        """
        # Remove frontmatter
        content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)

        sections: list[dict[str, Union[str, int, list[str]]]] = []
        lines = content.split("\n")
        current_section: Optional[dict[str, Union[str, int, list[str]]]] = None

        for line in lines:
            # Check for header
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)

            if header_match:
                # Save previous section
                if current_section:
                    sections.append(current_section)

                # Start new section
                level = len(header_match.group(1))
                title = header_match.group(2).strip()

                current_section = {"level": level, "title": title, "content": []}
            elif current_section:
                # Add to current section
                current_section["content"].append(line)  # type: ignore[union-attr]
            else:
                # Content before first header
                if not sections:
                    sections.append({"level": 0, "title": "", "content": [line]})
                else:
                    sections[0]["content"].append(line)  # type: ignore[union-attr]

        # Save last section
        if current_section:
            sections.append(current_section)

        # Join content lines
        for section in sections:
            section["content"] = "\n".join(section["content"]).strip()  # type: ignore[arg-type]

        return sections  # type: ignore[return-value]

    def format_content(
        self, content: str, metadata: Optional[dict[str, Union[str, int, None]]] = None
    ) -> str:
        """Convert to JSON format.

        Args:
            content: Markdown content
            metadata: Optional metadata

        Returns:
            JSON string
        """
        sections = self.extract_sections(content)

        output = {
            "metadata": metadata or {},
            "sections": sections,
            "full_content": content,
        }

        # Pretty print or compact based on options
        indent = self.options.get("indent", 2)
        if indent is False:
            return json.dumps(output, ensure_ascii=False)
        else:
            return json.dumps(output, indent=indent, ensure_ascii=False)

    def get_file_extension(self) -> str:
        """Get JSON extension.

        Returns:
            '.json'
        """
        return ".json"
