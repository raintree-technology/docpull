"""Zero-cost local OSS baseline adapters running on committed controlled fixtures.

Each adapter maps the case URL to the committed fixture bytes under
``bench/fixtures/`` (the same content the fixture host serves to live
adapters) and extracts locally, so baselines score the exact inputs the
controlled corpus uses — no network, no paid requests, cost recorded as 0.

Dependencies are optional (`uv sync --project bench --extra baselines`).
A missing dependency behaves like a missing hosted API key: the adapter
returns a failed observation explaining the missing package and never
crashes the run.
"""

from __future__ import annotations

import html as html_module
import importlib.metadata
import importlib.util
import re
import time
from abc import ABC, abstractmethod
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..fixtures import fixture_path_for_url
from ..models import ArtifactRecord, BenchmarkInput, ContentPayload, ExtractInput, Lane, RunObservation
from ..sanitization import scrub_secrets
from .base import AdapterError

NOT_INSTALLED_VERSION = "not-installed"
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class LocalBaselineAdapter(ABC):
    """Shared contract for deterministic local OSS extraction baselines."""

    system: str
    import_module: str
    distribution: str
    capabilities = frozenset({Lane.EXTRACT})
    cache_policy = "not_applicable"
    retry_policy = "no_retries"
    pricing_snapshot: str | None = None

    def __init__(self) -> None:
        self.version = _installed_version(self.import_module, self.distribution)

    def preflight(self, inputs: list[BenchmarkInput], *, repeat: int) -> None:
        del repeat
        unmapped = [
            item.case_id
            for item in inputs
            if isinstance(item, ExtractInput) and fixture_path_for_url(item.url) is None
        ]
        if unmapped:
            raise AdapterError(
                "local baselines only run on committed controlled fixtures; "
                f"no fixture maps to: {', '.join(unmapped)}"
            )

    def public_config(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "version": self.version,
            "capabilities": sorted(lane.value for lane in self.capabilities),
            "cache_policy": self.cache_policy,
            "retry_policy": self.retry_policy,
            "distribution": self.distribution,
            "input_source": "committed controlled fixtures",
            "paid_routes": False,
            "network": False,
        }

    def run(self, inputs: BenchmarkInput, output_root: Path) -> RunObservation:
        del output_root
        if not isinstance(inputs, ExtractInput):
            return _unsupported(self, inputs)
        if self.version == NOT_INSTALLED_VERSION:
            return self._failed(
                inputs,
                f"{self.distribution} is not installed; no extraction was attempted. "
                "Install the bench optional extra: uv sync --project bench --extra baselines",
                elapsed_seconds=0,
                attempt_count=0,
            )
        fixture = fixture_path_for_url(inputs.url)
        if fixture is None:
            return self._failed(
                inputs,
                "no committed fixture maps to the case URL; "
                "local baselines only run on the controlled corpus",
                elapsed_seconds=0,
                attempt_count=0,
            )
        source = fixture.read_text(encoding="utf-8", errors="replace")
        started = time.perf_counter()
        try:
            title, content = self._extract(source, inputs.url)
        except Exception as error:  # noqa: BLE001 - extraction failures are benchmark outcomes
            return self._failed(
                inputs,
                scrub_secrets(f"{type(error).__name__}: {error}"),
                elapsed_seconds=time.perf_counter() - started,
            )
        elapsed = time.perf_counter() - started
        if not content.strip():
            return self._failed(
                inputs,
                f"{self.system} returned no normalized results.",
                elapsed_seconds=elapsed,
            )
        record = ArtifactRecord(
            url=inputs.url,
            title=title,
            content=content,
            metadata={"provider": self.system},
        )
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status="completed",
            payload=ContentPayload(records=[record], selected_urls=[record.url]),
            elapsed_seconds=elapsed,
            cost_usd=0,
            cost_kind="actual",
            cost_basis="Local OSS extraction on committed fixture bytes; no paid provider or network.",
            request_count=0,
            adapter_version=self.version,
        )

    def _failed(
        self,
        inputs: BenchmarkInput,
        error: str,
        *,
        elapsed_seconds: float,
        attempt_count: int = 1,
    ) -> RunObservation:
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status="failed",
            elapsed_seconds=elapsed_seconds,
            cost_usd=0,
            cost_kind="actual",
            cost_basis="Local execution only; no paid provider or network.",
            request_count=0,
            attempt_count=attempt_count,
            adapter_version=self.version,
            error=error,
        )

    @abstractmethod
    def _extract(self, source: str, url: str) -> tuple[str, str]:
        """Return (title, markdown-or-text content) for one HTML document."""


class TrafilaturaAdapter(LocalBaselineAdapter):
    """Trafilatura main-content extraction on the committed fixture bytes.

    Prefers ``output_format="markdown"`` and falls back to plain text when the
    installed trafilatura release does not support Markdown output.
    """

    system = "trafilatura"
    import_module = "trafilatura"
    distribution = "trafilatura"

    def _extract(self, source: str, url: str) -> tuple[str, str]:
        import trafilatura

        try:
            content = trafilatura.extract(
                source,
                url=url,
                output_format="markdown",
                include_comments=False,
                include_tables=True,
            )
        except (TypeError, ValueError):
            content = trafilatura.extract(
                source,
                url=url,
                output_format="txt",
                include_comments=False,
                include_tables=True,
            )
        return _document_title(source), str(content or "")


class ReadabilityAdapter(LocalBaselineAdapter):
    """readability-lxml main-content extraction on the committed fixture bytes.

    The extracted content HTML is converted to Markdown-flavored text with a
    deterministic stdlib ``html.parser`` conversion (headings, links, lists,
    and fenced code blocks).
    """

    system = "readability"
    import_module = "readability"
    distribution = "readability-lxml"

    def _extract(self, source: str, url: str) -> tuple[str, str]:
        del url
        from readability import Document

        document = Document(source)
        title = str(document.short_title() or "")
        return title, html_fragment_to_markdown(str(document.summary(html_partial=True)))


class Crawl4AIAdapter(LocalBaselineAdapter):
    """Crawl4AI HTML→Markdown generation on the committed fixture bytes.

    Extract lane only. Crawl4AI's crawler (``AsyncWebCrawler``) requires a
    live Playwright browser and network access, which the controlled replay
    policy forbids, so the crawl lane is intentionally not claimed; this
    adapter runs Crawl4AI's Markdown generation pipeline directly on the
    fixture HTML instead.
    """

    system = "crawl4ai"
    import_module = "crawl4ai"
    distribution = "crawl4ai"

    def _extract(self, source: str, url: str) -> tuple[str, str]:
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        result = DefaultMarkdownGenerator().generate_markdown(source, base_url=url)
        return _document_title(source), str(getattr(result, "raw_markdown", "") or "")


class _MarkdownExtractor(HTMLParser):
    """Minimal deterministic HTML→Markdown-flavored text conversion."""

    _SKIP = {"script", "style", "noscript", "template", "head"}
    _BLOCK = {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "footer",
        "nav",
        "aside",
        "figure",
        "figcaption",
        "table",
        "tr",
        "ul",
        "ol",
        "blockquote",
        "br",
    }
    _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self._pre_depth = 0
        self._href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._HEADINGS:
            self._parts.append("\n\n" + "#" * self._HEADINGS[tag] + " ")
        elif tag == "a":
            self._href = next((value for name, value in attrs if name == "href"), None)
            self._link_text = []
        elif tag == "pre":
            self._parts.append("\n\n```\n")
            self._pre_depth += 1
        elif tag == "code" and not self._pre_depth:
            self._parts.append("`")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in self._BLOCK:
            self._parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "a":
            text = "".join(self._link_text).strip()
            if self._href and text:
                self._parts.append(f"[{text}]({self._href})")
            else:
                self._parts.append(text)
            self._href = None
            self._link_text = []
        elif tag == "pre":
            self._pre_depth = max(0, self._pre_depth - 1)
            self._parts.append("\n```\n\n")
        elif tag == "code" and not self._pre_depth:
            self._parts.append("`")
        elif tag in self._HEADINGS or tag in self._BLOCK:
            self._parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._href is not None:
            self._link_text.append(data)
        elif self._pre_depth:
            self._parts.append(data)
        else:
            self._parts.append(re.sub(r"\s+", " ", data))

    def text(self) -> str:
        merged = "".join(self._parts)
        return re.sub(r"\n{3,}", "\n\n", merged).strip()


def html_fragment_to_markdown(fragment: str) -> str:
    parser = _MarkdownExtractor()
    parser.feed(fragment)
    parser.close()
    return parser.text()


def _document_title(source: str) -> str:
    match = _TITLE_RE.search(source)
    return html_module.unescape(match.group(1)).strip() if match else ""


def _installed_version(import_module: str, distribution: str) -> str:
    if importlib.util.find_spec(import_module) is None:
        return NOT_INSTALLED_VERSION
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _unsupported(adapter: LocalBaselineAdapter, inputs: BenchmarkInput) -> RunObservation:
    return RunObservation(
        case_id=inputs.case_id,
        system=adapter.system,
        status="unsupported",
        elapsed_seconds=0,
        cost_usd=0,
        cost_kind="actual",
        cost_basis="No route was executed for an unsupported lane.",
        request_count=0,
        attempt_count=0,
        adapter_version=adapter.version,
        error=f"{adapter.system} baseline does not claim the {inputs.lane.value} lane.",
    )
