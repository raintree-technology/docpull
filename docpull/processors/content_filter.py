"""Content filter processor - filters sections and content from markdown files."""

import re
from pathlib import Path
from typing import Optional

from .base import BaseProcessor, ProcessorContext, ProcessorResult


class ContentFilter(BaseProcessor):
    """Filter content from markdown files.

    Removes or truncates sections based on headers or regex patterns,
    useful for removing verbose examples while keeping schemas.

    Configuration:
        exclude_sections: List of header names to remove (e.g., ['Examples', 'Changelog'])
        regex_filters: List of dicts with 'pattern', 'action', 'max_length'
        case_sensitive: Whether section matching is case-sensitive (default: False)

    Example:
        # Remove Examples and Changelog sections
        ContentFilter({'exclude_sections': ['Examples', 'Changelog', 'Full Response']})

        # Truncate JSON code blocks
        ContentFilter({
            'regex_filters': [{
                'pattern': r'```json.*?```',
                'action': 'truncate',
                'max_length': 100
            }]
        })
    """

    def __init__(self, config: Optional[dict[str, any]] = None):
        """Initialize content filter.

        Args:
            config: Configuration dict
        """
        super().__init__(config)
        self.exclude_sections: list[str] = self.config.get("exclude_sections", [])
        self.regex_filters: list[dict] = self.config.get("regex_filters", [])
        self.case_sensitive: bool = self.config.get("case_sensitive", False)

        # Compile regex patterns
        self.compiled_patterns: list[tuple[re.Pattern, str, int]] = []
        for filter_spec in self.regex_filters:
            pattern = filter_spec.get("pattern")
            action = filter_spec.get("action", "remove")
            max_length = filter_spec.get("max_length", 100)

            if not pattern:
                continue

            flags = 0 if self.case_sensitive else re.IGNORECASE
            compiled = re.compile(pattern, flags | re.DOTALL)
            self.compiled_patterns.append((compiled, action, max_length))

    def remove_sections(self, content: str) -> tuple[str, int]:
        """Remove excluded sections from markdown content.

        Args:
            content: Markdown content

        Returns:
            Tuple of (filtered_content, sections_removed_count)
        """
        if not self.exclude_sections:
            return content, 0

        lines = content.split("\n")
        filtered_lines: list[str] = []
        in_excluded_section = False
        current_section_level = 0
        sections_removed = 0

        for line in lines:
            # Check if line is a header
            header_match = re.match(r"^(#{1,6})\s+(.+)$", line)

            if header_match:
                level = len(header_match.group(1))
                title = header_match.group(2).strip()

                # Check if this section should be excluded
                is_excluded = any(
                    excluded.lower() == title.lower() if not self.case_sensitive else excluded == title
                    for excluded in self.exclude_sections
                )

                if is_excluded:
                    in_excluded_section = True
                    current_section_level = level
                    sections_removed += 1
                    continue
                elif in_excluded_section and level <= current_section_level:
                    # End of excluded section
                    in_excluded_section = False

            # Add line if not in excluded section
            if not in_excluded_section:
                filtered_lines.append(line)

        return "\n".join(filtered_lines), sections_removed

    def apply_regex_filters(self, content: str) -> tuple[str, dict[str, int]]:
        """Apply regex-based filters to content.

        Args:
            content: Content to filter

        Returns:
            Tuple of (filtered_content, stats_dict)
        """
        stats = {"removed": 0, "truncated": 0}

        for pattern, action, max_length in self.compiled_patterns:
            matches = list(pattern.finditer(content))

            if action == "remove":
                content = pattern.sub("", content)
                stats["removed"] += len(matches)

            elif action == "truncate":

                def truncate_match(match, max_len=max_length):
                    matched_text = match.group(0)
                    if len(matched_text) > max_len:
                        stats["truncated"] += 1
                        return matched_text[:max_len] + "...[truncated]"
                    return matched_text

                content = pattern.sub(truncate_match, content)

        return content, stats

    def filter_file(self, file_path: Path) -> Optional[dict[str, any]]:
        """Filter content in a single file.

        Args:
            file_path: Path to file to filter

        Returns:
            Dict with filtering stats or None if error
        """
        try:
            # Read file
            with open(file_path, encoding="utf-8") as f:
                original_content = f.read()

            original_size = len(original_content)

            # Apply section filters
            content, sections_removed = self.remove_sections(original_content)

            # Apply regex filters
            content, regex_stats = self.apply_regex_filters(content)

            # Only write if content changed
            if content != original_content:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

                new_size = len(content)
                return {
                    "sections_removed": sections_removed,
                    "regex_removed": regex_stats["removed"],
                    "regex_truncated": regex_stats["truncated"],
                    "size_reduction": original_size - new_size,
                }

            return {
                "sections_removed": 0,
                "regex_removed": 0,
                "regex_truncated": 0,
                "size_reduction": 0,
            }

        except Exception as e:
            self.logger.error(f"Failed to filter {file_path}: {e}")
            return None

    def process(self, context: ProcessorContext) -> ProcessorResult:
        """Filter content from files.

        Args:
            context: ProcessorContext with files to filter

        Returns:
            ProcessorResult with filtered files
        """
        if not self.exclude_sections and not self.regex_filters:
            return ProcessorResult(files=context.files, metadata=context.metadata, stats={"enabled": False})

        filtered_count = 0
        total_sections_removed = 0
        total_regex_removed = 0
        total_regex_truncated = 0
        total_size_reduction = 0

        for file_path in context.files:
            if self.should_skip(file_path, context):
                continue

            # Only process markdown files
            if file_path.suffix.lower() not in (".md", ".markdown"):
                continue

            stats = self.filter_file(file_path)
            if stats:
                if stats["size_reduction"] > 0:
                    filtered_count += 1

                total_sections_removed += stats["sections_removed"]
                total_regex_removed += stats["regex_removed"]
                total_regex_truncated += stats["regex_truncated"]
                total_size_reduction += stats["size_reduction"]

        messages = [
            f"Content filter: processed {len(context.files)} files",
            f"Modified {filtered_count} files",
        ]

        if self.exclude_sections:
            messages.append(
                f"Removed {total_sections_removed} sections "
                f"(excluding: {', '.join(self.exclude_sections)})"
            )

        if self.regex_filters:
            messages.append(
                f"Regex filters: removed {total_regex_removed}, " f"truncated {total_regex_truncated} matches"
            )

        messages.append(f"Total size reduction: {total_size_reduction / 1024:.1f} KB")

        return ProcessorResult(
            files=context.files,
            metadata=context.metadata,
            stats={
                "filtered_files": filtered_count,
                "sections_removed": total_sections_removed,
                "regex_removed": total_regex_removed,
                "regex_truncated": total_regex_truncated,
                "size_reduction_bytes": total_size_reduction,
            },
            messages=messages,
        )
