"""Tests for formatter modules."""


import pytest

from docpull.formatters import get_formatter
from docpull.formatters.json import JSONFormatter
from docpull.formatters.markdown import MarkdownFormatter
from docpull.formatters.toon import TOONFormatter


class TestFormatterFactory:
    """Test formatter factory function."""

    def test_get_markdown_formatter(self):
        """Test getting markdown formatter."""
        formatter = get_formatter("markdown")
        assert isinstance(formatter, MarkdownFormatter)

    def test_get_toon_formatter(self):
        """Test getting TOON formatter."""
        formatter = get_formatter("toon")
        assert isinstance(formatter, TOONFormatter)

    def test_get_json_formatter(self):
        """Test getting JSON formatter."""
        formatter = get_formatter("json")
        assert isinstance(formatter, JSONFormatter)

    def test_get_invalid_formatter(self):
        """Test getting invalid formatter."""
        with pytest.raises(ValueError):
            get_formatter("invalid")


class TestMarkdownFormatter:
    """Test MarkdownFormatter."""

    def test_markdown_formatter_init(self):
        """Test markdown formatter initialization."""
        formatter = MarkdownFormatter()
        assert formatter is not None

    def test_format_with_frontmatter(self):
        """Test formatting with YAML frontmatter."""
        formatter = MarkdownFormatter()

        content = "# Test Title\n\nTest content."
        metadata = {"url": "https://example.com", "title": "Test", "fetched": "2025-11-16"}

        result = formatter.format(content, metadata)

        assert "---" in result
        assert "url: https://example.com" in result
        assert "title: Test" in result
        assert "# Test Title" in result
        assert "Test content." in result

    def test_format_without_metadata(self):
        """Test formatting without metadata."""
        formatter = MarkdownFormatter()

        content = "# Test Title\n\nTest content."

        result = formatter.format(content, {})

        assert result == content

    def test_get_extension(self):
        """Test getting file extension."""
        formatter = MarkdownFormatter()
        assert formatter.get_extension() == ".md"


class TestTOONFormatter:
    """Test TOONFormatter."""

    def test_toon_formatter_init(self):
        """Test TOON formatter initialization."""
        formatter = TOONFormatter()
        assert formatter is not None

    def test_format_compact(self):
        """Test TOON compact formatting."""
        formatter = TOONFormatter()

        content = """# Main Title

## Section 1

Content for section 1.

### Subsection 1.1

Subsection content.

## Section 2

Content for section 2.
"""

        result = formatter.format(content, {})

        # TOON should be more compact
        assert len(result) < len(content)
        assert "\n\n\n" not in result  # No triple newlines

    def test_format_preserves_structure(self):
        """Test TOON preserves document structure."""
        formatter = TOONFormatter()

        content = """# Title
## Section
Content
### Subsection
More content
"""

        result = formatter.format(content, {})

        # Should preserve headers
        assert "# Title" in result or "Title" in result
        assert "Section" in result
        assert "Content" in result

    def test_get_extension(self):
        """Test getting file extension."""
        formatter = TOONFormatter()
        assert formatter.get_extension() == ".toon"


class TestJSONFormatter:
    """Test JSONFormatter."""

    def test_json_formatter_init(self):
        """Test JSON formatter initialization."""
        formatter = JSONFormatter()
        assert formatter is not None

    def test_format_to_json(self):
        """Test formatting to JSON."""
        formatter = JSONFormatter()

        content = """# Main Title

Introduction text.

## Section 1

Section 1 content.

## Section 2

Section 2 content.
"""
        metadata = {"url": "https://example.com", "title": "Test Doc"}

        result = formatter.format(content, metadata)

        # Should be valid JSON
        import json

        data = json.loads(result)

        assert "metadata" in data
        assert "content" in data
        assert data["metadata"]["url"] == "https://example.com"
        assert data["metadata"]["title"] == "Test Doc"

    def test_format_sections(self):
        """Test JSON sections parsing."""
        formatter = JSONFormatter()

        content = """# Title
Intro

## Section 1
Content 1

## Section 2
Content 2
"""

        result = formatter.format(content, {})

        import json

        data = json.loads(result)

        # Should have sections
        assert "sections" in data or "content" in data

    def test_get_extension(self):
        """Test getting file extension."""
        formatter = JSONFormatter()
        assert formatter.get_extension() == ".json"

    def test_format_empty_content(self):
        """Test formatting empty content."""
        formatter = JSONFormatter()

        result = formatter.format("", {})

        import json

        data = json.loads(result)

        assert "content" in data or "metadata" in data


class TestFormatterComparison:
    """Test comparison between formatters."""

    def test_size_comparison(self):
        """Test TOON is more compact than Markdown."""
        content = """# Documentation Title

## Overview

This is a comprehensive overview section with detailed information.

## Installation

Follow these steps to install:

1. First step with detailed explanation
2. Second step with more details
3. Third step with even more information

## Usage

Here's how to use this feature with extensive examples.

### Example 1

Example content here.

### Example 2

More example content here.

## API Reference

Detailed API documentation.
"""

        markdown_formatter = MarkdownFormatter()
        toon_formatter = TOONFormatter()

        markdown_result = markdown_formatter.format(content, {})
        toon_result = toon_formatter.format(content, {})

        # TOON should be smaller (more compact)
        assert len(toon_result) <= len(markdown_result)

    def test_all_formatters_preserve_content(self):
        """Test all formatters preserve essential content."""
        content = "# Test\n\nImportant content."
        metadata = {"url": "https://example.com"}

        markdown_formatter = MarkdownFormatter()
        toon_formatter = TOONFormatter()
        json_formatter = JSONFormatter()

        md_result = markdown_formatter.format(content, metadata)
        toon_result = toon_formatter.format(content, metadata)
        json_result = json_formatter.format(content, metadata)

        # All should preserve the essential text
        assert "Test" in md_result
        assert "Test" in toon_result
        assert "Test" in json_result

        assert "Important content" in md_result
        assert "Important content" in toon_result
        assert "Important content" in json_result
