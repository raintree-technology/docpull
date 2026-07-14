"""Opt-in hosted adapters with public-URL validation and fail-closed budgets."""

from __future__ import annotations

import ipaddress
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from ..budget import BudgetError, BudgetLedger
from ..models import (
    ArtifactRecord,
    BenchmarkInput,
    ContentPayload,
    CrawlInput,
    ExtractInput,
    Lane,
    RankedResult,
    RunObservation,
    SearchInput,
    SearchPayload,
)
from ..pricing import PricingSnapshot
from ..sanitization import scrub_secrets
from .base import AdapterError

TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
TAVILY_CRAWL_URL = "https://api.tavily.com/crawl"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"
EXA_SEARCH_URL = "https://api.exa.ai/search"
CONTEXT_MARKDOWN_URL = "https://api.context.dev/v1/web/scrape/markdown"
CONTEXT_CRAWL_URL = "https://api.context.dev/v1/web/crawl"
PARALLEL_EXTRACT_URL = "https://api.parallel.ai/v1/extract"
PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1/search"
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class _ProviderResult:
    payload: ContentPayload | SearchPayload
    cost_usd: float
    cost_kind: Literal["actual", "estimated", "upper_bound"]
    cost_basis: str
    usage: dict[str, int | float | str | bool | None] = field(default_factory=dict)


class HostedAdapter(ABC):
    """Base for official hosted APIs; one request and no retries by default."""

    system: str
    version: str
    api_key_env: str
    capabilities: frozenset[Lane]
    operation_by_lane: dict[Lane, str]
    cache_policy = "provider_managed"
    retry_policy = "max_attempts=1"
    pricing_snapshot: str | None

    def __init__(
        self,
        *,
        max_cost_usd: float,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        pricing: PricingSnapshot | None = None,
    ) -> None:
        self.ledger = BudgetLedger(max_cost_usd)
        self._explicit_api_key = api_key
        self.transport = transport
        self.pricing = pricing or PricingSnapshot.load()
        self.pricing_snapshot = self.pricing.snapshot

    def estimate_case_cost(self, inputs: BenchmarkInput) -> float:
        operation = self.operation_by_lane.get(inputs.lane)
        return self.pricing.price(self._provider_name(), operation).usd if operation else 0.0

    def preflight(self, inputs: list[BenchmarkInput], *, repeat: int) -> None:
        for item in inputs:
            _validate_input_urls(item)
        total = sum(self.estimate_case_cost(item) for item in inputs) * repeat
        try:
            self.ledger.plan(total)
        except BudgetError as error:
            raise AdapterError(f"{self.system} {error}") from error

    def run(self, inputs: BenchmarkInput, output_root: Path) -> RunObservation:
        del output_root
        if inputs.lane not in self.capabilities:
            return _unsupported(self, inputs)
        estimate = self.estimate_case_cost(inputs)
        try:
            self.ledger.reserve(estimate)
            api_key = _require_api_key(self.api_key_env, self._explicit_api_key)
        except (BudgetError, AdapterError) as error:
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="budget_blocked" if isinstance(error, BudgetError) else "failed",
                elapsed_seconds=0,
                cost_usd=0,
                cost_kind="actual",
                cost_basis="No request was made.",
                request_count=0,
                attempt_count=0,
                adapter_version=self.version,
                error=str(error),
            )
        started = time.perf_counter()
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=inputs.timeout_seconds,
                follow_redirects=False,
                headers={"User-Agent": "docpull-bench/0.2"},
            ) as client:
                result = self._execute(client, inputs, api_key)
        except (AdapterError, httpx.HTTPError, ValueError, TypeError) as error:
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="failed",
                elapsed_seconds=time.perf_counter() - started,
                cost_usd=estimate,
                cost_kind="upper_bound",
                cost_basis="Conservative reservation for one attempted request.",
                request_count=1,
                adapter_version=self.version,
                error=scrub_secrets(f"{type(error).__name__}: {error}"),
            )
        has_output = bool(
            result.payload.records if isinstance(result.payload, ContentPayload) else result.payload.results
        )
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status="completed" if has_output else "failed",
            payload=result.payload if has_output else None,
            elapsed_seconds=time.perf_counter() - started,
            cost_usd=result.cost_usd,
            cost_kind=result.cost_kind,
            cost_basis=result.cost_basis,
            usage=result.usage,
            request_count=1,
            adapter_version=self.version,
            error=None if has_output else f"{self.system} returned no normalized results.",
        )

    def public_config(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "version": self.version,
            "capabilities": sorted(lane.value for lane in self.capabilities),
            "cache_policy": self.cache_policy,
            "retry_policy": self.retry_policy,
            "pricing_snapshot": self.pricing_snapshot,
            "operations": {lane.value: value for lane, value in self.operation_by_lane.items()},
            "pricing_entries": {
                lane.value: self.pricing.public_entry(self._provider_name(), operation)
                for lane, operation in self.operation_by_lane.items()
            },
            "maximum_cost_usd": self.ledger.maximum_usd,
        }

    def _provider_name(self) -> str:
        return self.system.split("-", 1)[0].replace("context.dev", "contextdev")

    @abstractmethod
    def _execute(
        self,
        client: httpx.Client,
        inputs: BenchmarkInput,
        api_key: str,
    ) -> _ProviderResult: ...


class TavilyExtractAdapter(HostedAdapter):
    system = "tavily"
    version = "extract-basic-v2"
    api_key_env = "TAVILY_API_KEY"
    capabilities = frozenset({Lane.EXTRACT})
    operation_by_lane = {Lane.EXTRACT: "extract_basic"}
    extract_depth = "basic"

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, ExtractInput)
        response = client.post(
            TAVILY_EXTRACT_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "urls": [inputs.url],
                "extract_depth": self.extract_depth,
                "include_images": False,
                "include_favicon": False,
                "format": "markdown",
                "timeout": max(1.0, min(inputs.timeout_seconds, 60.0)),
                "include_usage": True,
            },
        )
        payload = _response_object(response, self.system)
        records = [
            ArtifactRecord(
                url=str(result.get("url") or inputs.url),
                title=str(result.get("title") or ""),
                content=str(result.get("raw_content") or ""),
                metadata={"provider": self.system},
            )
            for result in _object_list(payload.get("results"))
            if str(result.get("raw_content") or "").strip()
        ]
        usage = _object(payload.get("usage"))
        credit_count = _number(usage.get("credits"))
        price = self.estimate_case_cost(inputs)
        return _ProviderResult(
            payload=ContentPayload(records=records, selected_urls=[record.url for record in records]),
            cost_usd=max(price, credit_count * self.pricing.price("tavily", "credit").usd),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Tavily {self.extract_depth} extraction ceiling.",
            usage={
                "credits": credit_count,
                "failed_results": len(_object_list(payload.get("failed_results"))),
            },
        )


class TavilyAdvancedExtractAdapter(TavilyExtractAdapter):
    system = "tavily-advanced"
    version = "extract-advanced-v2"
    operation_by_lane = {Lane.EXTRACT: "extract_advanced"}
    extract_depth = "advanced"

    def _provider_name(self) -> str:
        return "tavily"


class TavilyCrawlAdapter(HostedAdapter):
    system = "tavily-crawl-basic"
    version = "crawl-basic-v2"
    api_key_env = "TAVILY_API_KEY"
    capabilities = frozenset({Lane.CRAWL})
    operation_by_lane = {Lane.CRAWL: "crawl_page_basic"}
    extract_depth = "basic"
    instructions: str | None = None

    def _provider_name(self) -> str:
        return "tavily"

    def estimate_case_cost(self, inputs: BenchmarkInput) -> float:
        if not isinstance(inputs, CrawlInput):
            return 0
        map_credits = max(1, (inputs.max_pages + 9) // 10)
        extract_credits = max(1, (inputs.max_pages + 4) // 5)
        if self.instructions:
            map_credits *= 2
        if self.extract_depth == "advanced":
            extract_credits *= 2
        return (map_credits + extract_credits) * self.pricing.price("tavily", "credit").usd

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, CrawlInput)
        response = client.post(
            TAVILY_CRAWL_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "url": inputs.url,
                "max_depth": max(1, min(inputs.max_depth, 5)),
                "max_breadth": max(1, min(inputs.max_pages, 500)),
                "limit": inputs.max_pages,
                "allow_external": False,
                "select_paths": [f"{re.escape(prefix)}.*" for prefix in inputs.include_path_prefixes] or None,
                "exclude_paths": [f"{re.escape(prefix)}.*" for prefix in inputs.exclude_path_prefixes]
                or None,
                "include_images": False,
                "extract_depth": self.extract_depth,
                "instructions": self.instructions,
                "format": "markdown",
                "timeout": max(10.0, min(inputs.timeout_seconds, 150.0)),
                "include_usage": True,
            },
        )
        payload = _response_object(response, self.system)
        records = [
            ArtifactRecord(
                url=str(result.get("url") or ""),
                title=str(result.get("title") or ""),
                content=str(result.get("raw_content") or ""),
                metadata={"provider": self.system},
            )
            for result in _object_list(payload.get("results"))
            if str(result.get("url") or "").strip() and str(result.get("raw_content") or "").strip()
        ]
        usage = _object(payload.get("usage"))
        return _ProviderResult(
            payload=ContentPayload(records=records, selected_urls=[record.url for record in records]),
            cost_usd=self.estimate_case_cost(inputs),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Tavily mapping-plus-extraction ceiling.",
            usage={"credits": _number(usage.get("credits")), "result_count": len(records)},
        )


class TavilyGuidedAdvancedCrawlAdapter(TavilyCrawlAdapter):
    system = "tavily-crawl-guided-advanced"
    version = "crawl-guided-advanced-v2"
    extract_depth = "advanced"
    instructions = "Crawl documentation within the selected path and exclude unrelated sections."


class TavilySearchAdapter(HostedAdapter):
    system = "tavily-search"
    version = "search-advanced-v2"
    api_key_env = "TAVILY_API_KEY"
    capabilities = frozenset({Lane.SEARCH})
    operation_by_lane = {Lane.SEARCH: "search_advanced"}

    def _provider_name(self) -> str:
        return "tavily"

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, SearchInput)
        response = client.post(
            TAVILY_SEARCH_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": inputs.query,
                "search_depth": "advanced",
                "max_results": inputs.max_results,
                "include_domains": inputs.include_domains or None,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "include_usage": True,
            },
        )
        payload = _response_object(response, self.system)
        results = [
            RankedResult(
                identity=str(item.get("url") or ""),
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                excerpt=str(item.get("content") or ""),
                score=_optional_number(item.get("score")),
            )
            for item in _object_list(payload.get("results"))
            if str(item.get("url") or "").strip()
        ]
        return _ProviderResult(
            payload=SearchPayload(results=results),
            cost_usd=self.estimate_case_cost(inputs),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Tavily advanced search ceiling.",
            usage={"result_count": len(results)},
        )


class ExaContentsAdapter(HostedAdapter):
    system = "exa"
    version = "contents-live-compact-v2"
    api_key_env = "EXA_API_KEY"
    capabilities = frozenset({Lane.EXTRACT})
    operation_by_lane = {Lane.EXTRACT: "contents_text"}
    text_options: bool | dict[str, Any] = True

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, ExtractInput)
        response = client.post(
            EXA_CONTENTS_URL,
            headers={"x-api-key": api_key},
            json={"urls": [inputs.url], "text": self.text_options, "maxAgeHours": 0},
        )
        payload = _response_object(response, self.system)
        records = [
            ArtifactRecord(
                url=str(result.get("url") or result.get("id") or inputs.url),
                title=str(result.get("title") or ""),
                content=str(result.get("text") or ""),
                metadata={"provider": self.system},
            )
            for result in _object_list(payload.get("results"))
            if str(result.get("text") or "").strip()
        ]
        reported = _optional_number(_object(payload.get("costDollars")).get("total"))
        return _ProviderResult(
            payload=ContentPayload(records=records, selected_urls=[record.url for record in records]),
            cost_usd=reported if reported is not None else self.estimate_case_cost(inputs),
            cost_kind="actual" if reported is not None else "upper_bound",
            cost_basis="Provider-reported costDollars.total."
            if reported is not None
            else f"{self.pricing_snapshot}: Exa contents ceiling.",
            usage={"provider_reported_cost_usd": reported},
        )


class ExaFullContentsAdapter(ExaContentsAdapter):
    system = "exa-full"
    version = "contents-live-full-v2"
    text_options = {"verbosity": "full", "excludeSections": ["navigation", "footer", "sidebar"]}

    def _provider_name(self) -> str:
        return "exa"


class ExaSearchAdapter(HostedAdapter):
    system = "exa-search"
    version = "search-auto-v2"
    api_key_env = "EXA_API_KEY"
    capabilities = frozenset({Lane.SEARCH})
    operation_by_lane = {Lane.SEARCH: "search"}

    def _provider_name(self) -> str:
        return "exa"

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, SearchInput)
        body: dict[str, Any] = {
            "query": inputs.query,
            "numResults": inputs.max_results,
            "type": "auto",
            "contents": {"text": {"maxCharacters": 2000}},
        }
        if inputs.include_domains:
            body["includeDomains"] = inputs.include_domains
        response = client.post(EXA_SEARCH_URL, headers={"x-api-key": api_key}, json=body)
        payload = _response_object(response, self.system)
        results = [
            RankedResult(
                identity=str(item.get("url") or item.get("id") or ""),
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                excerpt=str(item.get("text") or ""),
                score=_optional_number(item.get("score")),
            )
            for item in _object_list(payload.get("results"))
            if str(item.get("url") or "").strip()
        ]
        reported = _optional_number(_object(payload.get("costDollars")).get("total"))
        return _ProviderResult(
            payload=SearchPayload(results=results),
            cost_usd=reported if reported is not None else self.estimate_case_cost(inputs),
            cost_kind="actual" if reported is not None else "upper_bound",
            cost_basis="Provider-reported costDollars.total."
            if reported is not None
            else f"{self.pricing_snapshot}: Exa search ceiling.",
            usage={"result_count": len(results), "provider_reported_cost_usd": reported},
        )


class ParallelFullExtractAdapter(HostedAdapter):
    system = "parallel"
    version = "v1-live-full-v2"
    api_key_env = "PARALLEL_API_KEY"
    capabilities = frozenset({Lane.EXTRACT})
    operation_by_lane = {Lane.EXTRACT: "extract"}

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, ExtractInput)
        response = client.post(
            PARALLEL_EXTRACT_URL,
            headers={"x-api-key": api_key},
            json={
                "urls": [inputs.url],
                "advanced_settings": {
                    "fetch_policy": {
                        "max_age_seconds": 600,
                        "timeout_seconds": max(15.0, min(inputs.timeout_seconds, 60.0)),
                        "disable_cache_fallback": False,
                    },
                    "full_content": True,
                },
            },
        )
        payload = _response_object(response, self.system)
        records = []
        for item in _object_list(payload.get("results")):
            excerpts = item.get("excerpts")
            content = str(item.get("full_content") or "").strip() or (
                "\n\n".join(str(value) for value in excerpts) if isinstance(excerpts, list) else ""
            )
            if content:
                records.append(
                    ArtifactRecord(
                        url=str(item.get("url") or inputs.url),
                        title=str(item.get("title") or ""),
                        content=content,
                        metadata={"provider": self.system},
                    )
                )
        return _ProviderResult(
            payload=ContentPayload(records=records, selected_urls=[record.url for record in records]),
            cost_usd=self.estimate_case_cost(inputs),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Parallel Extract ceiling.",
            usage={"error_count": len(_object_list(payload.get("errors")))},
        )


class ParallelSearchAdapter(HostedAdapter):
    system = "parallel-search"
    version = "v1-search-v2"
    api_key_env = "PARALLEL_API_KEY"
    capabilities = frozenset({Lane.SEARCH})
    operation_by_lane = {Lane.SEARCH: "search"}

    def _provider_name(self) -> str:
        return "parallel"

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, SearchInput)
        advanced_settings = (
            {"source_policy": {"include_domains": inputs.include_domains}} if inputs.include_domains else None
        )
        response = client.post(
            PARALLEL_SEARCH_URL,
            headers={"x-api-key": api_key},
            json={
                "objective": inputs.query,
                "search_queries": [inputs.query],
                "max_chars_total": max(2000, inputs.max_results * 1000),
                "advanced_settings": advanced_settings,
            },
        )
        payload = _response_object(response, self.system)
        results = [
            RankedResult(
                identity=str(item.get("url") or ""),
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                excerpt="\n\n".join(str(value) for value in item.get("excerpts", [])),
            )
            for item in _object_list(payload.get("results"))[: inputs.max_results]
            if str(item.get("url") or "").strip()
        ]
        return _ProviderResult(
            payload=SearchPayload(results=results),
            cost_usd=self.estimate_case_cost(inputs),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Parallel Search ceiling.",
            usage={"result_count": len(results)},
        )


class ContextMarkdownAdapter(HostedAdapter):
    system = "context.dev"
    version = "v1-markdown-main-v2"
    api_key_env = "CONTEXT_DEV_API_KEY"
    capabilities = frozenset({Lane.EXTRACT})
    operation_by_lane = {Lane.EXTRACT: "markdown"}

    def _provider_name(self) -> str:
        return "contextdev"

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, ExtractInput)
        response = client.get(
            CONTEXT_MARKDOWN_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            params={
                "url": inputs.url,
                "includeLinks": "true",
                "includeImages": "false",
                "useMainContentOnly": "true",
                "timeoutMs": str(max(1, min(round(inputs.timeout_seconds * 1000), 300000))),
            },
        )
        payload = _response_object(response, self.system)
        content = str(payload.get("markdown") or "")
        metadata = _object(payload.get("metadata"))
        records = (
            [
                ArtifactRecord(
                    url=str(metadata.get("finalUrl") or payload.get("url") or inputs.url),
                    title=str(metadata.get("title") or ""),
                    content=content,
                    metadata={"provider": self.system},
                )
            ]
            if content.strip()
            else []
        )
        return _ProviderResult(
            payload=ContentPayload(records=records, selected_urls=[record.url for record in records]),
            cost_usd=self.estimate_case_cost(inputs),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Context.dev Markdown ceiling.",
            usage={
                "credits_consumed": _optional_number(
                    _object(payload.get("key_metadata")).get("credits_consumed")
                )
            },
        )


class ContextCrawlAdapter(HostedAdapter):
    system = "context.dev-crawl"
    version = "v1-crawl-v2"
    api_key_env = "CONTEXT_DEV_API_KEY"
    capabilities = frozenset({Lane.CRAWL})
    operation_by_lane = {Lane.CRAWL: "crawl_page"}

    def _provider_name(self) -> str:
        return "contextdev"

    def estimate_case_cost(self, inputs: BenchmarkInput) -> float:
        if not isinstance(inputs, CrawlInput):
            return 0.0
        return inputs.max_pages * self.pricing.price("contextdev", "crawl_page").usd

    def _execute(self, client: httpx.Client, inputs: BenchmarkInput, api_key: str) -> _ProviderResult:
        assert isinstance(inputs, CrawlInput)
        prefixes = [re.escape(prefix) for prefix in inputs.include_path_prefixes]
        response = client.post(
            CONTEXT_CRAWL_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "url": inputs.url,
                "maxPages": inputs.max_pages,
                "maxDepth": inputs.max_depth,
                "urlRegex": f"^({'|'.join(prefixes)})" if prefixes else None,
                "useMainContentOnly": True,
                "followSubdomains": False,
                "maxAgeMs": 0,
                "stopAfterMs": max(10000, min(round(inputs.timeout_seconds * 1000), 240000)),
                "includeFrames": False,
            },
        )
        payload = _response_object(response, self.system)
        pages = _object_list(payload.get("pages") or payload.get("results"))
        records = [
            ArtifactRecord(
                url=str(item.get("url") or ""),
                title=str(item.get("title") or ""),
                content=str(item.get("markdown") or item.get("content") or ""),
                metadata={"provider": self.system},
            )
            for item in pages
            if str(item.get("url") or "").strip()
            and str(item.get("markdown") or item.get("content") or "").strip()
        ]
        return _ProviderResult(
            payload=ContentPayload(records=records, selected_urls=[record.url for record in records]),
            cost_usd=self.estimate_case_cost(inputs),
            cost_kind="upper_bound",
            cost_basis=f"{self.pricing_snapshot}: Context.dev maximum-page crawl ceiling.",
            usage={"result_count": len(records), "skipped": _number(payload.get("numSkipped"))},
        )


def _unsupported(adapter: HostedAdapter, inputs: BenchmarkInput) -> RunObservation:
    return RunObservation(
        case_id=inputs.case_id,
        system=adapter.system,
        status="unsupported",
        elapsed_seconds=0,
        cost_usd=0,
        cost_kind="actual",
        cost_basis="No request was made for an unsupported lane.",
        request_count=0,
        attempt_count=0,
        adapter_version=adapter.version,
        error=f"{adapter.system} adapter does not claim the {inputs.lane.value} lane.",
    )


def _validate_input_urls(inputs: BenchmarkInput) -> None:
    values: list[str] = []
    if isinstance(inputs, (ExtractInput, CrawlInput)):
        values.append(inputs.url)
    for value in values:
        _validate_public_https(value)


def _validate_public_https(value: str) -> None:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise AdapterError("hosted benchmark targets must be credential-free public HTTPS URLs")
    host = parts.hostname.casefold().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise AdapterError("hosted benchmark targets must not use local or internal hostnames")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise AdapterError("hosted benchmark targets must not use private or non-global addresses")


def _require_api_key(name: str, explicit: str | None) -> str:
    value = explicit if explicit is not None else os.environ.get(name)
    if value is None or not value.strip():
        raise AdapterError(f"missing {name}; no requests were made")
    value = value.strip()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise AdapterError(f"invalid control character in {name}; no requests were made")
    return value


def _response_object(response: httpx.Response, system: str) -> dict[str, Any]:
    if response.is_error:
        raise AdapterError(f"{system} returned HTTP {response.status_code}; response body omitted")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise AdapterError(f"{system} response exceeds {MAX_RESPONSE_BYTES} bytes")
    payload = response.json()
    if not isinstance(payload, dict):
        raise AdapterError("provider returned a non-object JSON response")
    return payload


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _object_list(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _number(value: Any) -> float:
    number = _optional_number(value)
    return number if number is not None else 0.0


def _optional_number(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, float(value))
    return None
