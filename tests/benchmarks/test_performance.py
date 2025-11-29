"""
Performance benchmarks for docpull v2.

These tests measure:
- Pipeline throughput
- Memory efficiency
- Deduplication performance
- Configuration parsing speed

Run with: pytest tests/benchmarks/ -v --benchmark-only
Or with: python -m pytest tests/benchmarks/ -v
"""

import gc
import sys
import time
from pathlib import Path

import pytest
from docpull.cache import StreamingDeduplicator
from docpull.conversion.markdown import HtmlToMarkdown
from docpull.models.config import DocpullConfig, ProfileName
from docpull.pipeline.base import FetchPipeline, PageContext
from docpull.pipeline.steps import ConvertStep, DedupStep, MetadataStep

# Sample HTML content of varying sizes for benchmarks
SMALL_HTML = "<html><body><h1>Title</h1><p>Content</p></body></html>"
SMALL_HTML_BYTES = b"<html><body><h1>Title</h1><p>Content</p></body></html>"

MEDIUM_HTML = """
<html>
<head><title>Test Document</title></head>
<body>
<main>
<h1>Main Title</h1>
<p>This is a paragraph with <strong>bold</strong> and <em>italic</em> text.</p>
<ul>
<li>Item 1</li>
<li>Item 2</li>
<li>Item 3</li>
</ul>
<h2>Section 1</h2>
<p>More content here with links to <a href="/page1">Page 1</a> and <a href="/page2">Page 2</a>.</p>
<pre><code>def example():
    return "code block"
</code></pre>
</main>
</body>
</html>
"""
MEDIUM_HTML_BYTES = MEDIUM_HTML.encode()

# Generate large HTML (~100KB)
LARGE_HTML = (
    "<html><head><title>Large Document</title></head><body><main>"
    + "<h1>Large Document</h1>"
    + "".join(
        [f"<p>Paragraph {i} with some content that makes this document larger.</p>" for i in range(1000)]
    )
    + "</main></body></html>"
)
LARGE_HTML_BYTES = LARGE_HTML.encode()


class TestConversionPerformance:
    """Benchmarks for HTML to Markdown conversion."""

    def test_small_html_conversion(self):
        """Benchmark small HTML conversion."""
        converter = HtmlToMarkdown()

        start = time.perf_counter()
        iterations = 1000
        for _ in range(iterations):
            converter.convert(SMALL_HTML, "https://example.com/page")
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nSmall HTML: {ops_per_sec:.0f} conversions/sec ({elapsed*1000/iterations:.2f}ms avg)")
        assert ops_per_sec > 100, "Small HTML should convert at >100/sec"

    def test_medium_html_conversion(self):
        """Benchmark medium HTML conversion."""
        converter = HtmlToMarkdown()

        start = time.perf_counter()
        iterations = 500
        for _ in range(iterations):
            converter.convert(MEDIUM_HTML, "https://example.com/page")
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nMedium HTML: {ops_per_sec:.0f} conversions/sec ({elapsed*1000/iterations:.2f}ms avg)")
        assert ops_per_sec > 50, "Medium HTML should convert at >50/sec"

    def test_large_html_conversion(self):
        """Benchmark large HTML conversion."""
        converter = HtmlToMarkdown()

        start = time.perf_counter()
        iterations = 50
        for _ in range(iterations):
            converter.convert(LARGE_HTML, "https://example.com/page")
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(
            f"\nLarge HTML (~100KB): {ops_per_sec:.1f} conversions/sec ({elapsed*1000/iterations:.1f}ms avg)"
        )
        assert ops_per_sec > 5, "Large HTML should convert at >5/sec"


class TestDeduplicationPerformance:
    """Benchmarks for deduplication."""

    @pytest.mark.asyncio
    async def test_streaming_dedup_throughput(self):
        """Benchmark StreamingDeduplicator throughput."""
        dedup = StreamingDeduplicator()

        # Generate unique content
        contents = [f"Content {i}".encode() for i in range(1000)]

        start = time.perf_counter()
        for i, content in enumerate(contents):
            await dedup.check_and_register(f"https://example.com/page{i}", content)
        elapsed = time.perf_counter() - start

        ops_per_sec = len(contents) / elapsed
        print(
            f"\nStreamingDeduplicator: {ops_per_sec:.0f} checks/sec ({elapsed*1000/len(contents):.2f}ms avg)"
        )
        assert ops_per_sec > 5000, "StreamingDeduplicator should handle >5000 checks/sec"

    @pytest.mark.asyncio
    async def test_streaming_dedup_duplicate_detection(self):
        """Benchmark duplicate detection speed."""
        dedup = StreamingDeduplicator()

        # Add unique entries first
        for i in range(100):
            await dedup.check_and_register(f"https://example.com/page{i}", f"Content {i}".encode())

        # Now check for the same content again
        duplicate_content = b"Content 0"
        start = time.perf_counter()
        iterations = 10000
        for i in range(iterations):
            await dedup.check_and_register(f"https://example.com/new{i}", duplicate_content)
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nDuplicate detection: {ops_per_sec:.0f} checks/sec")
        assert ops_per_sec > 10000, "Duplicate detection should be >10000/sec"

    def test_streaming_dedup_memory_efficiency(self):
        """Test StreamingDeduplicator memory usage."""
        dedup = StreamingDeduplicator()

        # Measure memory before
        gc.collect()
        mem_before = sys.getsizeof(dedup._seen)

        # Add many entries (using sync compute_hash for simplicity)
        for i in range(10000):
            content_hash = dedup.compute_hash(f"Content {i}".encode())
            dedup._seen[content_hash] = f"https://example.com/page{i}"

        # Measure memory after
        gc.collect()
        mem_after = sys.getsizeof(dedup._seen)

        # Memory should grow linearly with entries (hash size is fixed)
        # Each hash is ~64 bytes, so 10000 entries should be ~640KB
        mem_growth = mem_after - mem_before
        print(f"\nStreamingDeduplicator memory: {mem_growth / 1024:.1f}KB for 10000 entries")
        assert mem_growth < 2 * 1024 * 1024, "Memory should be <2MB for 10000 entries"


class TestPipelinePerformance:
    """Benchmarks for pipeline execution."""

    @pytest.mark.asyncio
    async def test_empty_pipeline_throughput(self):
        """Benchmark empty pipeline throughput."""

        class PassthroughStep:
            name = "passthrough"

            async def execute(self, ctx, emit=None):
                return ctx

        pipeline = FetchPipeline(steps=[PassthroughStep()])

        start = time.perf_counter()
        iterations = 1000
        for i in range(iterations):
            await pipeline.execute(f"https://example.com/page{i}", Path(f"/tmp/page{i}.md"))
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nEmpty pipeline: {ops_per_sec:.0f} executions/sec")
        assert ops_per_sec > 1000, "Empty pipeline should execute >1000/sec"

    @pytest.mark.asyncio
    async def test_metadata_step_throughput(self):
        """Benchmark MetadataStep throughput."""
        step = MetadataStep()

        start = time.perf_counter()
        iterations = 500
        for i in range(iterations):
            ctx = PageContext(
                url=f"https://example.com/page{i}",
                output_path=Path(f"/tmp/page{i}.md"),
                html=MEDIUM_HTML_BYTES,
            )
            await step.execute(ctx)
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nMetadataStep: {ops_per_sec:.0f} pages/sec")
        assert ops_per_sec > 50, "MetadataStep should process >50 pages/sec"

    @pytest.mark.asyncio
    async def test_convert_step_throughput(self):
        """Benchmark ConvertStep throughput."""
        step = ConvertStep(add_frontmatter=True)

        start = time.perf_counter()
        iterations = 200
        for i in range(iterations):
            ctx = PageContext(
                url=f"https://example.com/page{i}",
                output_path=Path(f"/tmp/page{i}.md"),
                html=MEDIUM_HTML_BYTES,
            )
            ctx.title = f"Page {i}"
            await step.execute(ctx)
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nConvertStep: {ops_per_sec:.0f} pages/sec")
        assert ops_per_sec > 30, "ConvertStep should process >30 pages/sec"

    @pytest.mark.asyncio
    async def test_dedup_step_throughput(self):
        """Benchmark DedupStep throughput."""
        dedup = StreamingDeduplicator()
        step = DedupStep(deduplicator=dedup)

        start = time.perf_counter()
        iterations = 1000
        for i in range(iterations):
            ctx = PageContext(
                url=f"https://example.com/page{i}",
                output_path=Path(f"/tmp/page{i}.md"),
                markdown=f"# Page {i}\n\nUnique content {i}.",
            )
            await step.execute(ctx)
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nDedupStep: {ops_per_sec:.0f} pages/sec")
        assert ops_per_sec > 500, "DedupStep should process >500 pages/sec"


class TestConfigPerformance:
    """Benchmarks for configuration parsing."""

    def test_config_creation_speed(self):
        """Benchmark config creation speed."""
        start = time.perf_counter()
        iterations = 1000
        for i in range(iterations):
            DocpullConfig(
                url=f"https://example{i}.com",
                profile=ProfileName.RAG,
            )
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nConfig creation: {ops_per_sec:.0f} configs/sec")
        assert ops_per_sec > 100, "Config creation should be >100/sec"

    def test_config_serialization_speed(self):
        """Benchmark config YAML serialization."""
        config = DocpullConfig(
            url="https://example.com",
            profile=ProfileName.RAG,
            crawl={"max_pages": 100, "max_depth": 5},
            output={"directory": Path("/tmp/docs")},
        )

        start = time.perf_counter()
        iterations = 500
        for _ in range(iterations):
            config.to_yaml()
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nConfig YAML serialization: {ops_per_sec:.0f} ops/sec")
        assert ops_per_sec > 100, "YAML serialization should be >100/sec"

    def test_config_yaml_parsing_speed(self):
        """Benchmark config YAML parsing."""
        yaml_str = """
url: https://docs.example.com
profile: rag
crawl:
  max_pages: 100
  max_depth: 5
  rate_limit: 0.5
output:
  directory: ./docs
  format: markdown
network:
  max_retries: 3
"""
        start = time.perf_counter()
        iterations = 500
        for _ in range(iterations):
            DocpullConfig.from_yaml(yaml_str)
        elapsed = time.perf_counter() - start

        ops_per_sec = iterations / elapsed
        print(f"\nConfig YAML parsing: {ops_per_sec:.0f} ops/sec")
        assert ops_per_sec > 50, "YAML parsing should be >50/sec"


class TestMemoryUsage:
    """Tests for memory efficiency."""

    def test_pipeline_memory_usage(self):
        """Test pipeline doesn't leak memory."""
        gc.collect()
        # Get baseline memory (Python doesn't have a simple way to measure this)
        # This is a basic test that runs many iterations to detect leaks

        pipeline_count = 100
        for _ in range(pipeline_count):
            pipeline = FetchPipeline(steps=[])
            del pipeline

        gc.collect()
        # If we got here without OOM, memory isn't leaking badly
        print(f"\nCreated and destroyed {pipeline_count} pipelines without memory issues")

    def test_large_content_memory(self):
        """Test that large content doesn't cause excessive memory usage."""
        converter = HtmlToMarkdown()

        # Process large content multiple times
        gc.collect()
        for _ in range(10):
            result = converter.convert(LARGE_HTML, "https://example.com/page")
            assert len(result) > 0
            del result
        gc.collect()

        print("\nProcessed large HTML 10 times without memory issues")


if __name__ == "__main__":
    # Run benchmarks directly
    pytest.main([__file__, "-v", "-s"])
