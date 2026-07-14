from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from docpull_bench.adapters import (
    AdapterError,
    ContextCrawlAdapter,
    ContextMarkdownAdapter,
    ExaContentsAdapter,
    ExaSearchAdapter,
    ParallelFullExtractAdapter,
    ParallelSearchAdapter,
    TavilyExtractAdapter,
    TavilySearchAdapter,
)
from docpull_bench.models import CrawlInput, ExtractInput, SearchInput


def _extract(url: str = "https://example.com/article") -> ExtractInput:
    return ExtractInput(case_id="extract.example", lane="extract", url=url, timeout_seconds=15)


def _search() -> SearchInput:
    return SearchInput(case_id="search.example", lane="search", query="official identifier", max_results=5)


def test_tavily_normalizes_payload_usage_and_upper_bound(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["urls"] == ["https://example.com/article"]
        return httpx.Response(
            200,
            json={
                "results": [{"url": body["urls"][0], "raw_content": "# Evidence"}],
                "usage": {"credits": 1},
            },
        )

    adapter = TavilyExtractAdapter(
        max_cost_usd=0.008, api_key="test-key", transport=httpx.MockTransport(handler)
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "completed"
    assert observation.cost_kind == "upper_bound"
    assert observation.cost_usd == 0.008
    assert observation.request_count == 1


def test_exa_uses_provider_reported_actual_cost(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://example.com/article", "text": "Evidence"}],
                "costDollars": {"total": 0.0007},
            },
        )

    adapter = ExaContentsAdapter(
        max_cost_usd=0.001, api_key="test-key", transport=httpx.MockTransport(handler)
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.cost_usd == 0.0007
    assert observation.cost_kind == "actual"


@pytest.mark.parametrize(
    ("factory", "response"),
    [
        (
            ParallelFullExtractAdapter,
            {"results": [{"url": "https://example.com/article", "full_content": "Evidence"}]},
        ),
        (
            ContextMarkdownAdapter,
            {"markdown": "Evidence", "metadata": {"finalUrl": "https://example.com/article"}},
        ),
    ],
)
def test_other_native_extract_adapters_normalize_official_responses(
    factory: type[ParallelFullExtractAdapter] | type[ContextMarkdownAdapter],
    response: dict[str, object],
    tmp_path: Path,
) -> None:
    adapter = factory(
        max_cost_usd=0.002,
        api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "completed"
    assert observation.payload is not None


@pytest.mark.parametrize("factory", [TavilySearchAdapter, ExaSearchAdapter, ParallelSearchAdapter])
def test_search_adapters_return_ranked_results(
    factory: type[TavilySearchAdapter] | type[ExaSearchAdapter] | type[ParallelSearchAdapter],
    tmp_path: Path,
) -> None:
    response = {
        "results": [
            {
                "url": "https://example.com/result",
                "title": "Identifier",
                "content": "Excerpt",
                "text": "Excerpt",
            }
        ],
        "costDollars": {"total": 0.007},
    }
    adapter = factory(
        max_cost_usd=0.02,
        api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    adapter.preflight([_search()], repeat=1)
    observation = adapter.run(_search(), tmp_path)
    assert observation.status == "completed"
    assert observation.payload and observation.payload.kind == "search"


def test_context_crawl_normalizes_bounded_pages(tmp_path: Path) -> None:
    inputs = CrawlInput(
        case_id="crawl.example",
        lane="crawl",
        url="https://example.com/docs",
        include_path_prefixes=["/docs/"],
        max_pages=3,
        max_depth=1,
    )
    response = {
        "pages": [
            {"url": "https://example.com/docs/a", "markdown": "A"},
            {"url": "https://example.com/docs/b", "markdown": "B"},
        ],
        "numSkipped": 1,
    }
    adapter = ContextCrawlAdapter(
        max_cost_usd=0.0045,
        api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    adapter.preflight([inputs], repeat=1)
    observation = adapter.run(inputs, tmp_path)
    assert observation.status == "completed"
    assert observation.cost_usd == pytest.approx(0.0045)
    assert observation.payload and len(observation.payload.records) == 2


def test_budget_exhaustion_happens_before_credentials_or_requests() -> None:
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={})

    adapter = TavilyExtractAdapter(max_cost_usd=0.015, transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError, match="no requests were made"):
        adapter.preflight([_extract()], repeat=2)
    assert requests == 0


def test_credential_is_read_only_after_reservation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, json={})

    adapter = ExaContentsAdapter(max_cost_usd=0.001, transport=httpx.MockTransport(handler))
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "failed"
    assert observation.request_count == 0
    assert "EXA_API_KEY" in (observation.error or "")
    assert requests == 0


@pytest.mark.parametrize(
    "url",
    ["http://example.com", "https://127.0.0.1/private", "https://user:pass@example.com"],
)
def test_hosted_targets_require_credential_free_public_https(url: str) -> None:
    adapter = TavilyExtractAdapter(max_cost_usd=0.008, api_key="test-key")
    with pytest.raises(AdapterError):
        adapter.preflight([_extract(url)], repeat=1)


def test_http_errors_are_bounded_and_secret_scrubbed(tmp_path: Path) -> None:
    adapter = TavilyExtractAdapter(
        max_cost_usd=0.008,
        api_key="secret-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(503, text="secret-key huge body")),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "failed"
    assert "response body omitted" in (observation.error or "")
    assert "secret-key" not in (observation.error or "")
    assert observation.attempt_count == 1


def test_oversized_provider_response_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("docpull_bench.adapters.hosted.MAX_RESPONSE_BYTES", 10)
    adapter = TavilyExtractAdapter(
        max_cost_usd=0.008,
        api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"{" + b"x" * 20 + b"}")),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "failed"
    assert "exceeds 10 bytes" in (observation.error or "")


def test_unsupported_lane_returns_unsupported_without_request(tmp_path: Path) -> None:
    adapter = TavilyExtractAdapter(max_cost_usd=0.008, api_key="test-key")
    observation = adapter.run(_search(), tmp_path)
    assert observation.status == "unsupported"
    assert observation.request_count == 0
