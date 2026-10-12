from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from docpull_bench.adapters import (
    AdapterError,
    BraveSearchAdapter,
    BrightDataUnlockerAdapter,
    ContextCrawlAdapter,
    ContextMarkdownAdapter,
    ExaContentsAdapter,
    ExaSearchAdapter,
    FirecrawlCrawlAdapter,
    FirecrawlScrapeAdapter,
    FirecrawlSearchAdapter,
    GrassContentsAdapter,
    JinaReaderAdapter,
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


def test_transport_errors_scrub_explicit_api_key(tmp_path: Path) -> None:
    secret = "explicit-test-key-that-must-not-escape"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(f"transport diagnostic included {secret}", request=request)

    adapter = TavilyExtractAdapter(
        max_cost_usd=0.008,
        api_key=secret,
        transport=httpx.MockTransport(handler),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)

    assert observation.status == "failed"
    assert observation.error is not None
    assert secret not in observation.error
    assert "[REDACTED]" in observation.error


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


def test_firecrawl_scrape_uses_deterministic_basic_mode(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["formats"] == ["markdown"]
        assert body["onlyCleanContent"] is False
        assert body["proxy"] == "basic"
        assert body["storeInCache"] is False
        return httpx.Response(
            200,
            json={
                "success": True,
                "creditsUsed": 1,
                "data": {
                    "markdown": "# Evidence",
                    "metadata": {
                        "url": "https://example.com/article",
                        "title": "Evidence",
                    },
                },
            },
        )

    adapter = FirecrawlScrapeAdapter(
        max_cost_usd=0.006,
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "completed"
    assert observation.cost_usd == pytest.approx(0.006)
    assert observation.usage["credits"] == 1


def test_grass_contents_requests_live_compact_text_and_bills_reported_credits(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.lcr.net/contents"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["urls"] == ["https://example.com/article"]
        assert body["maxAgeHours"] == 0
        assert body["livecrawlTimeout"] == 15000
        assert body["text"]["verbosity"] == "compact"
        return httpx.Response(
            200,
            json={
                "requestId": "request",
                "results": [
                    {
                        "id": "r1",
                        "url": "https://example.com/article",
                        "title": "Evidence",
                        "text": "Evidence",
                    }
                ],
                "statuses": [
                    {
                        "id": "r1",
                        "status": "success",
                        "source": "livecrawled",
                        "httpStatusCode": 200,
                        "strategy": "browser",
                        "credits": 2,
                    }
                ],
            },
        )

    adapter = GrassContentsAdapter(
        max_cost_usd=0.004, api_key="test-key", transport=httpx.MockTransport(handler)
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "completed"
    assert observation.payload is not None
    assert observation.payload.records[0].content == "Evidence"
    assert observation.cost_usd == pytest.approx(2 * 0.0011112)
    assert observation.usage["strategy"] == "browser"
    assert observation.usage["source"] == "livecrawled"


def test_grass_contents_reports_per_url_error_tag(tmp_path: Path) -> None:
    response = {
        "results": [],
        "statuses": [
            {
                "id": "r1",
                "status": "error",
                "source": "https://example.com/article",
                "httpStatusCode": 403,
                "strategy": None,
                "credits": 0,
                "error": {"tag": "SOURCE_NOT_AVAILABLE", "httpStatusCode": 403},
            }
        ],
    }
    adapter = GrassContentsAdapter(
        max_cost_usd=0.004,
        api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "failed"
    assert observation.error is not None
    assert "SOURCE_NOT_AVAILABLE" in observation.error


def test_jina_reader_bypasses_cache_and_bills_reported_tokens(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://r.jina.ai/"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers["Accept"] == "application/json"
        body = json.loads(request.content)
        assert body == {
            "url": "https://example.com/article",
            "noCache": True,
            "tokenBudget": 200_000,
            "timeout": 15,
        }
        return httpx.Response(
            200,
            json={
                "code": 200,
                "status": 20000,
                "data": {
                    "url": "https://example.com/article",
                    "title": "Evidence",
                    "content": "# Evidence",
                    "usage": {"tokens": 1000},
                },
            },
        )

    adapter = JinaReaderAdapter(max_cost_usd=0.01, api_key="test-key", transport=httpx.MockTransport(handler))
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "completed"
    assert observation.payload is not None
    assert observation.payload.records[0].content == "# Evidence"
    assert observation.cost_usd == pytest.approx(1000 * 0.00000005)
    assert observation.usage["tokens"] == 1000


def test_brightdata_unlocker_requests_markdown_from_configured_zone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRIGHTDATA_ZONE", "bench_zone")

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.brightdata.com/request"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body == {
            "zone": "bench_zone",
            "url": "https://example.com/article",
            "format": "raw",
            "data_format": "markdown",
            "country": "us",
        }
        return httpx.Response(200, text="# Evidence")

    adapter = BrightDataUnlockerAdapter(
        max_cost_usd=0.0015, api_key="test-key", transport=httpx.MockTransport(handler)
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "completed"
    assert observation.payload is not None
    assert observation.payload.records[0].content == "# Evidence"
    assert observation.cost_usd == pytest.approx(0.0015)


def test_brightdata_unlocker_reports_bounded_error_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BRIGHTDATA_ZONE", "bench_zone")
    adapter = BrightDataUnlockerAdapter(
        max_cost_usd=0.0015,
        api_key="test-key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(502, headers={"x-brd-error": "target unreachable"}, text="secret page")
        ),
    )
    adapter.preflight([_extract()], repeat=1)
    observation = adapter.run(_extract(), tmp_path)
    assert observation.status == "failed"
    assert observation.error is not None
    assert "target unreachable" in observation.error
    assert "secret page" not in observation.error


def test_brave_search_applies_one_domain_and_strips_markup(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["X-Subscription-Token"] == "test-key"
        assert request.url.params["q"] == "official identifier site:example.com"
        assert request.url.params["count"] == "5"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Evidence",
                            "url": "https://example.com/a",
                            "description": "The <strong>official</strong> identifier",
                        }
                    ]
                }
            },
        )

    search = SearchInput(
        case_id="search.example",
        lane="search",
        query="official identifier",
        max_results=5,
        include_domains=["example.com"],
    )
    adapter = BraveSearchAdapter(
        max_cost_usd=0.005, api_key="test-key", transport=httpx.MockTransport(handler)
    )
    adapter.preflight([search], repeat=1)
    observation = adapter.run(search, tmp_path)
    assert observation.status == "completed"
    assert observation.payload is not None
    assert observation.payload.results[0].excerpt == "The official identifier"
    assert observation.cost_usd == pytest.approx(0.005)


def test_firecrawl_crawl_polls_and_normalizes_bounded_pages(tmp_path: Path) -> None:
    inputs = CrawlInput(
        case_id="crawl.example",
        lane="crawl",
        url="https://example.com/docs",
        include_path_prefixes=["/docs/"],
        max_pages=3,
        max_depth=1,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["limit"] == 3
            assert body["sitemap"] == "skip"
            assert body["ignoreRobotsTxt"] is False
            assert body["includePaths"] == ["docs/.*"]
            assert body["maxConcurrency"] == 1
            return httpx.Response(200, json={"success": True, "id": "crawl-id"})
        assert request.url.path == "/v2/crawl/crawl-id"
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "completed": 2,
                "creditsUsed": 2,
                "data": [
                    {
                        "markdown": "A",
                        "metadata": {"url": "https://example.com/docs/a"},
                    },
                    {
                        "markdown": "B",
                        "metadata": {"url": "https://example.com/docs/b"},
                    },
                ],
            },
        )

    adapter = FirecrawlCrawlAdapter(
        max_cost_usd=0.018,
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    adapter.preflight([inputs], repeat=1)
    observation = adapter.run(inputs, tmp_path)
    assert observation.status == "completed"
    assert observation.request_count == 2
    assert observation.cost_usd == pytest.approx(0.012)
    assert observation.payload and len(observation.payload.records) == 2


def test_firecrawl_search_returns_ranked_web_results(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["sources"] == [{"type": "web"}]
        return httpx.Response(
            200,
            json={
                "success": True,
                "creditsUsed": 2,
                "data": {
                    "web": [
                        {
                            "url": "https://example.com/result",
                            "title": "Identifier",
                            "description": "Excerpt",
                        }
                    ]
                },
            },
        )

    adapter = FirecrawlSearchAdapter(
        max_cost_usd=0.012,
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    adapter.preflight([_search()], repeat=1)
    observation = adapter.run(_search(), tmp_path)
    assert observation.status == "completed"
    assert observation.cost_usd == pytest.approx(0.012)
    assert observation.payload and observation.payload.kind == "search"


def test_public_protocol_config_is_budget_independent_and_request_specific() -> None:
    small = TavilyExtractAdapter(max_cost_usd=0.008, api_key="test-key")
    large = TavilyExtractAdapter(max_cost_usd=8.0, api_key="test-key")
    assert small.public_config() == large.public_config()
    profile = small.public_config()["request_profile"]
    assert profile["endpoint"] == "https://api.tavily.com/extract"
    assert profile["extract_depth"] == "basic"
    assert "maximum_cost_usd" not in small.public_config()


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


def test_structured_http_error_keeps_bounded_schema_diagnostics(tmp_path: Path) -> None:
    adapter = FirecrawlCrawlAdapter(
        max_cost_usd=0.018,
        api_key="secret-key",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                400,
                json={
                    "error": "invalid request for secret-key",
                    "details": {"field": "maxConcurrency"},
                    "ignored": "raw page content must not enter the report",
                },
            )
        ),
    )
    inputs = CrawlInput(
        case_id="crawl.example",
        lane="crawl",
        url="https://example.com/docs",
        max_pages=3,
        max_depth=1,
    )
    adapter.preflight([inputs], repeat=1)
    observation = adapter.run(inputs, tmp_path)
    assert "maxConcurrency" in (observation.error or "")
    assert "secret-key" not in (observation.error or "")
    assert "raw page content" not in (observation.error or "")


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
