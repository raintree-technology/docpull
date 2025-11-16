"""Tests for processor modules."""

from docpull.processors import (
    ContentFilter,
    Deduplicator,
    LanguageFilter,
    ProcessorContext,
    ProcessorPipeline,
    SizeLimiter,
)


class TestProcessorContext:
    """Test ProcessorContext."""

    def test_context_creation(self, tmp_path):
        """Test creating a processor context."""
        files = [tmp_path / "test.md"]
        metadata = {files[0]: {"title": "Test"}}

        context = ProcessorContext(files=files, metadata=metadata, output_dir=tmp_path)

        assert context.files == files
        assert context.metadata == metadata
        assert context.output_dir == tmp_path

    def test_context_empty_metadata(self, tmp_path):
        """Test context with empty metadata."""
        files = [tmp_path / "test.md"]

        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        assert len(context.metadata) == 0


class TestLanguageFilter:
    """Test LanguageFilter processor."""

    def test_language_filter_init(self):
        """Test language filter initialization."""
        config = {"include": ["en"]}
        processor = LanguageFilter(config)

        assert processor.config == config

    def test_detect_language_from_url(self):
        """Test language detection from URL."""
        processor = LanguageFilter({"include": ["en"]})

        # Test various URL patterns
        assert processor._detect_language("/docs/en/guide") == "en"
        assert processor._detect_language("/docs_en_guide") == "en"
        assert processor._detect_language("/en-us/docs") == "en"
        assert processor._detect_language("/fr/docs") == "fr"
        assert processor._detect_language("/docs/guide") is None

    def test_filter_by_language_include(self, tmp_path):
        """Test filtering by included language."""
        # Create test files
        en_file = tmp_path / "docs_en.md"
        fr_file = tmp_path / "docs_fr.md"
        en_file.write_text("# English")
        fr_file.write_text("# French")

        files = [en_file, fr_file]
        metadata = {
            en_file: {"url": "https://example.com/docs/en/guide"},
            fr_file: {"url": "https://example.com/docs/fr/guide"},
        }

        processor = LanguageFilter({"include": ["en"]})
        context = ProcessorContext(files=files, metadata=metadata, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 1
        assert result.files[0] == en_file

    def test_filter_by_language_exclude(self, tmp_path):
        """Test filtering by excluded language."""
        # Create test files
        en_file = tmp_path / "docs_en.md"
        fr_file = tmp_path / "docs_fr.md"
        en_file.write_text("# English")
        fr_file.write_text("# French")

        files = [en_file, fr_file]
        metadata = {
            en_file: {"url": "https://example.com/docs/en/guide"},
            fr_file: {"url": "https://example.com/docs/fr/guide"},
        }

        processor = LanguageFilter({"exclude": ["fr"]})
        context = ProcessorContext(files=files, metadata=metadata, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 1
        assert result.files[0] == en_file


class TestDeduplicator:
    """Test Deduplicator processor."""

    def test_deduplicator_init(self):
        """Test deduplicator initialization."""
        config = {"enabled": True}
        processor = Deduplicator(config)

        assert processor.config == config

    def test_compute_hash(self, tmp_path):
        """Test hash computation."""
        processor = Deduplicator({"enabled": True})

        # Create test file
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test Content")

        hash1 = processor._compute_hash(test_file)
        hash2 = processor._compute_hash(test_file)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex digest

    def test_deduplicate_keep_first(self, tmp_path):
        """Test deduplication keeping first file."""
        processor = Deduplicator({"enabled": True, "keep_variant": "first"})

        # Create duplicate files
        file1 = tmp_path / "test1.md"
        file2 = tmp_path / "test2.md"
        file1.write_text("# Same Content")
        file2.write_text("# Same Content")

        files = [file1, file2]
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 1
        assert result.files[0] == file1

    def test_deduplicate_keep_shortest(self, tmp_path):
        """Test deduplication keeping shortest filename."""
        processor = Deduplicator({"enabled": True, "keep_variant": "shortest"})

        # Create files with same content but different name lengths
        short_file = tmp_path / "a.md"
        long_file = tmp_path / "very_long_filename.md"
        short_file.write_text("# Same Content")
        long_file.write_text("# Same Content")

        files = [long_file, short_file]  # Order shouldn't matter
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 1
        assert result.files[0] == short_file

    def test_deduplicate_keep_pattern(self, tmp_path):
        """Test deduplication keeping files matching pattern."""
        processor = Deduplicator({"enabled": True, "keep_variant": "mainnet"})

        # Create files
        mainnet = tmp_path / "mainnet_guide.md"
        testnet = tmp_path / "testnet_guide.md"
        mainnet.write_text("# Same Content")
        testnet.write_text("# Same Content")

        files = [testnet, mainnet]
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 1
        assert result.files[0] == mainnet

    def test_no_duplicates(self, tmp_path):
        """Test deduplication with no duplicates."""
        processor = Deduplicator({"enabled": True})

        # Create unique files
        file1 = tmp_path / "test1.md"
        file2 = tmp_path / "test2.md"
        file1.write_text("# Content 1")
        file2.write_text("# Content 2")

        files = [file1, file2]
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 2


class TestSizeLimiter:
    """Test SizeLimiter processor."""

    def test_size_limiter_init(self):
        """Test size limiter initialization."""
        config = {"max_file_size": "200kb"}
        processor = SizeLimiter(config)

        assert processor.config == config

    def test_parse_size_kb(self):
        """Test parsing size in KB."""
        processor = SizeLimiter({"max_file_size": "200kb"})

        size = processor._parse_size("200kb")

        assert size == 200 * 1024

    def test_parse_size_mb(self):
        """Test parsing size in MB."""
        processor = SizeLimiter({"max_file_size": "1mb"})

        size = processor._parse_size("1mb")

        assert size == 1024 * 1024

    def test_parse_size_gb(self):
        """Test parsing size in GB."""
        processor = SizeLimiter({"max_file_size": "1gb"})

        size = processor._parse_size("1gb")

        assert size == 1024 * 1024 * 1024

    def test_filter_by_file_size(self, tmp_path):
        """Test filtering by file size."""
        processor = SizeLimiter(
            {
                "max_file_size": "100",  # 100 bytes
                "action": "skip",
            }
        )

        # Create files
        small_file = tmp_path / "small.md"
        large_file = tmp_path / "large.md"
        small_file.write_text("Small")  # < 100 bytes
        large_file.write_text("X" * 200)  # > 100 bytes

        files = [small_file, large_file]
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        assert len(result.files) == 1
        assert result.files[0] == small_file

    def test_total_size_limit(self, tmp_path):
        """Test total size limiting."""
        processor = SizeLimiter(
            {
                "max_total_size": "150"  # 150 bytes total
            }
        )

        # Create files (total > 150 bytes)
        file1 = tmp_path / "file1.md"
        file2 = tmp_path / "file2.md"
        file3 = tmp_path / "file3.md"
        file1.write_text("X" * 60)
        file2.write_text("Y" * 60)
        file3.write_text("Z" * 60)

        files = [file1, file2, file3]
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        # Should stop after first 2 files (120 bytes < 150)
        assert len(result.files) <= 2


class TestContentFilter:
    """Test ContentFilter processor."""

    def test_content_filter_init(self):
        """Test content filter initialization."""
        config = {"exclude_sections": ["Examples", "Changelog"]}
        processor = ContentFilter(config)

        assert processor.config == config

    def test_remove_section(self, tmp_path):
        """Test removing a section."""
        processor = ContentFilter({"exclude_sections": ["Examples"]})

        content = """# Main Title

Some intro text.

## Examples

Example 1
Example 2

## Usage

Usage text.
"""

        result = processor._remove_section(content, "Examples")

        assert "Examples" not in result
        assert "Usage" in result

    def test_filter_multiple_sections(self, tmp_path):
        """Test filtering multiple sections."""
        processor = ContentFilter({"exclude_sections": ["Examples", "Changelog"]})

        # Create test file
        test_file = tmp_path / "test.md"
        test_file.write_text(
            """# Guide

## Overview
Content

## Examples
Example content

## Changelog
Version history

## Usage
Usage content
"""
        )

        files = [test_file]
        context = ProcessorContext(files=files, metadata={}, output_dir=tmp_path)

        result = processor.process(context)

        # Read processed file
        processed_content = result.files[0].read_text()

        assert "Examples" not in processed_content
        assert "Changelog" not in processed_content
        assert "Overview" in processed_content
        assert "Usage" in processed_content


class TestProcessorPipeline:
    """Test ProcessorPipeline."""

    def test_pipeline_empty(self, tmp_path):
        """Test empty pipeline."""
        pipeline = ProcessorPipeline([])
        context = ProcessorContext(files=[], metadata={}, output_dir=tmp_path)

        result = pipeline.run(context)

        assert result.files == []

    def test_pipeline_single_processor(self, tmp_path):
        """Test pipeline with single processor."""
        processor = LanguageFilter({"include": ["en"]})
        pipeline = ProcessorPipeline([processor])

        en_file = tmp_path / "en.md"
        en_file.write_text("# English")

        files = [en_file]
        metadata = {en_file: {"url": "https://example.com/en/guide"}}
        context = ProcessorContext(files=files, metadata=metadata, output_dir=tmp_path)

        result = pipeline.run(context)

        assert len(result.files) == 1

    def test_pipeline_multiple_processors(self, tmp_path):
        """Test pipeline with multiple processors."""
        lang_filter = LanguageFilter({"include": ["en"]})
        size_limiter = SizeLimiter({"max_file_size": "1mb"})

        pipeline = ProcessorPipeline([lang_filter, size_limiter])

        en_file = tmp_path / "en.md"
        en_file.write_text("# English Content")

        files = [en_file]
        metadata = {en_file: {"url": "https://example.com/en/guide"}}
        context = ProcessorContext(files=files, metadata=metadata, output_dir=tmp_path)

        result = pipeline.run(context)

        assert len(result.files) == 1

    def test_pipeline_filtering(self, tmp_path):
        """Test pipeline filtering files through multiple stages."""
        lang_filter = LanguageFilter({"include": ["en"]})
        deduplicator = Deduplicator({"enabled": True, "keep_variant": "first"})

        pipeline = ProcessorPipeline([lang_filter, deduplicator])

        # Create files
        en1 = tmp_path / "en1.md"
        en2 = tmp_path / "en2.md"
        fr = tmp_path / "fr.md"

        en1.write_text("# Same Content")
        en2.write_text("# Same Content")  # Duplicate
        fr.write_text("# French")

        files = [en1, en2, fr]
        metadata = {
            en1: {"url": "https://example.com/en/guide1"},
            en2: {"url": "https://example.com/en/guide2"},
            fr: {"url": "https://example.com/fr/guide"},
        }
        context = ProcessorContext(files=files, metadata=metadata, output_dir=tmp_path)

        result = pipeline.run(context)

        # Should filter out French, then deduplicate English files
        assert len(result.files) == 1
        assert result.files[0] == en1
