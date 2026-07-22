from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from docpull_bench import cli
from docpull_bench.adapters import Crawl4AIAdapter, ReadabilityAdapter, TrafilaturaAdapter
from docpull_bench.adapters.base import AdapterError
from docpull_bench.adapters.local_baselines import (
    NOT_INSTALLED_VERSION,
    LocalBaselineAdapter,
    html_fragment_to_markdown,
)
from docpull_bench.models import ContentPayload, CrawlInput, ExtractInput

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_URL = "https://raintree-technology.github.io/docpull/bench-fixtures/v2/extract/01.html"
INLINE_HTML = (
    "<html><head><title>Inline Fixture</title></head><body>"
    "<nav>forbidden navigation boilerplate</nav>"
    "<main><h1>Evidence Heading</h1>"
    "<p>extract-marker-inline deterministic evidence with enough sentences to satisfy "
    "minimum extraction thresholds. This paragraph repeats supporting evidence for the "
    "extractor so the main content is unambiguous. Structural fidelity matters and the "
    "canonical URL stays stable across runs of this controlled benchmark fixture.</p>"
    "<p>A second paragraph keeps the article dense: deterministic evidence, deterministic "
    "evidence, and one <a href='https://example.com/spec'>specification link</a>.</p>"
    "<pre><code>const fixture = true;</code></pre></main></body></html>"
)


class _StubBaselineAdapter(LocalBaselineAdapter):
    system = "stub-baseline"
    import_module = "html"
    distribution = "stub-distribution"

    def _extract(self, source: str, url: str) -> tuple[str, str]:
        del url
        return "Stub", f"stub extracted {len(source)} chars"


def _extract_input(url: str = FIXTURE_URL) -> ExtractInput:
    return ExtractInput(case_id="extract.fixture.01", lane="extract", url=url)


def test_local_baseline_adapters_are_registered_with_cli() -> None:
    expected = {
        "trafilatura": TrafilaturaAdapter,
        "readability": ReadabilityAdapter,
        "crawl4ai": Crawl4AIAdapter,
    }
    for name, adapter_type in expected.items():
        args = argparse.Namespace(adapter=name, system=name, max_cost_usd=None)
        adapter = cli._adapter(args)
        assert isinstance(adapter, adapter_type)
        assert adapter.system == name
    with pytest.raises(ValueError, match="requires --system trafilatura"):
        cli._adapter(argparse.Namespace(adapter="trafilatura", system="docpull", max_cost_usd=None))


def test_missing_dependency_yields_failed_observation_without_crash(tmp_path: Path) -> None:
    adapter = TrafilaturaAdapter()
    adapter.version = NOT_INSTALLED_VERSION
    observation = adapter.run(_extract_input(), tmp_path)
    assert observation.status == "failed"
    assert observation.error is not None
    assert "not installed" in observation.error
    assert observation.cost_usd == 0
    assert observation.attempt_count == 0
    assert observation.request_count == 0


def test_unsupported_lane_is_explicit_for_local_baselines(tmp_path: Path) -> None:
    crawl = CrawlInput(
        case_id="crawl.fixture.graph-01",
        lane="crawl",
        url="https://raintree-technology.github.io/docpull/bench-fixtures/v2/crawl/graph-01/1.html",
    )
    for adapter in (TrafilaturaAdapter(), ReadabilityAdapter(), Crawl4AIAdapter()):
        observation = adapter.run(crawl, tmp_path)
        assert observation.status == "unsupported"
        assert observation.attempt_count == 0


def test_stub_baseline_runs_on_committed_fixture_bytes(tmp_path: Path) -> None:
    adapter = _StubBaselineAdapter()
    adapter.preflight([_extract_input()], repeat=1)
    observation = adapter.run(_extract_input(), tmp_path)
    assert observation.status == "completed"
    assert isinstance(observation.payload, ContentPayload)
    record = observation.payload.records[0]
    assert record.url == FIXTURE_URL
    assert record.content.startswith("stub extracted")
    assert observation.cost_usd == 0
    assert observation.cost_kind == "actual"
    assert observation.request_count == 0


def test_preflight_rejects_inputs_without_committed_fixtures() -> None:
    adapter = _StubBaselineAdapter()
    with pytest.raises(AdapterError, match="no fixture maps"):
        adapter.preflight([_extract_input(url="https://example.com/article")], repeat=1)


def test_unmapped_url_fails_gracefully_at_run_time(tmp_path: Path) -> None:
    observation = _StubBaselineAdapter().run(_extract_input(url="https://example.com/a"), tmp_path)
    assert observation.status == "failed"
    assert observation.error is not None
    assert "no committed fixture" in observation.error


def test_trafilatura_extracts_inline_html() -> None:
    pytest.importorskip("trafilatura")
    title, content = TrafilaturaAdapter()._extract(INLINE_HTML, "https://example.com/inline")
    assert "extract-marker-inline" in content
    assert "deterministic evidence" in content
    assert title == "Inline Fixture"


def test_readability_extracts_inline_html() -> None:
    pytest.importorskip("readability")
    title, content = ReadabilityAdapter()._extract(INLINE_HTML, "https://example.com/inline")
    assert "extract-marker-inline" in content
    assert "deterministic evidence" in content
    assert title == "Inline Fixture"


def test_crawl4ai_extracts_inline_html() -> None:
    pytest.importorskip("crawl4ai")
    title, content = Crawl4AIAdapter()._extract(INLINE_HTML, "https://example.com/inline")
    assert "extract-marker-inline" in content
    assert title == "Inline Fixture"


def test_html_fragment_to_markdown_is_deterministic_and_structural() -> None:
    fragment = (
        "<div><h2>Guide &amp; Notes</h2><p>First paragraph.</p>"
        "<ul><li>alpha</li><li>beta</li></ul>"
        "<p>See <a href='https://example.com/doc'>the doc</a>.</p>"
        "<pre><code>value = 1</code></pre><script>ignored()</script></div>"
    )
    rendered = html_fragment_to_markdown(fragment)
    assert "## Guide & Notes" in rendered
    assert "- alpha" in rendered
    assert "[the doc](https://example.com/doc)" in rendered
    assert "```\nvalue = 1" in rendered
    assert "ignored()" not in rendered
    assert rendered == html_fragment_to_markdown(fragment)
