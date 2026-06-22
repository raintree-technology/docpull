"""First-class adapters for optional live web-intelligence providers."""

from __future__ import annotations

import importlib.util
import json
import random
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .conversion.chunking import TokenCounter
from .discovery.contracts import CandidateSourceRecord, write_discovery_pack
from .discovery.filters import normalize_url
from .models.document import DocumentRecord
from .pack_tools import prepare_pack, score_pack, score_pack_sources
from .pipeline.manifest import CorpusManifest
from .policy import PolicyConfig
from .provider_keys import (
    PROVIDER_CONFIGS,
    PROVIDER_NAMES,
    ProviderKeyError,
    ProviderName,
    lookup_provider_api_key,
    normalize_provider_name,
    validate_provider_api_key,
)
from .source_scoring import score_source
from .time_utils import utc_now_iso

PROVIDER_PACK_SCHEMA_VERSION = 2
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
TAVILY_MAP_URL = "https://api.tavily.com/map"
EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_CONTENTS_URL = "https://api.exa.ai/contents"
HTTP_RETRY_MAX_ATTEMPTS = 3
HTTP_RETRY_TRANSIENT_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})
HTTP_RETRY_CAP_SECONDS = 30.0
HTTP_MAX_RESPONSE_BYTES = 16 * 1024 * 1024
HTTP_MAX_ERROR_BYTES = 64 * 1024
DEFAULT_MAX_FULL_CONTENT_CHARS = 50000
TAVILY_MAX_SEARCH_RESULTS = 20
TAVILY_MAX_EXTRACT_URLS = 20
EXA_MAX_SEARCH_RESULTS = 100
EXA_MAX_CONTENT_URLS = 100
TAVILY_MAX_MAP_DEPTH = 5
TAVILY_MAX_MAP_BREADTH = 500
TAVILY_MIN_MAP_TIMEOUT_SECONDS = 10.0
TAVILY_MAX_MAP_TIMEOUT_SECONDS = 150.0

JsonPost = Callable[..., dict[str, Any]]


class ProviderAdapterError(RuntimeError):
    """User-facing optional-provider adapter error."""


@dataclass(frozen=True)
class ProviderDocument:
    """Normalized provider document ready for DocPull pack writing."""

    url: str
    title: str
    content: str
    metadata: dict[str, Any]
    source_type: str


@dataclass(frozen=True)
class ProviderPackResult:
    """Normalized provider run result independent of provider response shape."""

    provider: ProviderName
    workflow: str
    output_dir: Path
    pack_path: Path
    documents: list[ProviderDocument]
    selected_urls: list[str]
    search_result_count: int
    extract_result_count: int
    extract_error_count: int
    usage: dict[str, Any] = field(default_factory=dict)
    response_metadata: dict[str, Any] = field(default_factory=dict)
    cost_dollars: dict[str, Any] | None = None
    cost_units: dict[str, Any] | None = None
    estimated_cost_usd: float | None = None


class LiveProviderAdapter:
    """Base interface for provider-backed search/extract pack adapters."""

    provider: ProviderName
    label: str

    def __init__(self, *, api_key: str | None = None, http_post: JsonPost | None = None) -> None:
        if api_key is None:
            self.api_key = require_provider_api_key(self.provider)
        else:
            try:
                self.api_key = validate_provider_api_key(api_key, label=f"{self.label} API key")
            except ProviderKeyError as err:
                raise ProviderAdapterError(str(err)) from err
        self.http_post = http_post or http_json_post

    def search_extract_pack(
        self,
        *,
        objective: str,
        queries: list[str],
        output_dir: Path,
        include_domains: list[str],
        max_search_results: int,
        extract_limit: int,
        mode: str = "advanced",
    ) -> ProviderPackResult:
        raise NotImplementedError

    def extract_pack(
        self,
        *,
        urls: list[str],
        objective: str,
        queries: list[str],
        output_dir: Path,
        mode: str = "advanced",
    ) -> ProviderPackResult:
        raise NotImplementedError

    def map_pack(
        self,
        *,
        url: str,
        output_dir: Path,
        objective: str | None,
        query: str | None,
        instructions: str | None,
        include_domains: list[str],
        exclude_domains: list[str],
        select_paths: list[str],
        select_domains: list[str],
        exclude_paths: list[str],
        map_exclude_domains: list[str],
        max_depth: int,
        max_breadth: int,
        limit: int,
        allow_external: bool,
        timeout: float,
    ) -> dict[str, Any]:
        raise NotImplementedError


class TavilyAdapter(LiveProviderAdapter):
    """Tavily Search + Extract adapter using the public REST API."""

    provider = "tavily"
    label = "Tavily"

    def search_extract_pack(
        self,
        *,
        objective: str,
        queries: list[str],
        output_dir: Path,
        include_domains: list[str],
        max_search_results: int,
        extract_limit: int,
        mode: str = "advanced",
    ) -> ProviderPackResult:
        _validate_limit(max_search_results, "max_search_results", TAVILY_MAX_SEARCH_RESULTS)
        _validate_limit(extract_limit, "extract_limit", TAVILY_MAX_EXTRACT_URLS)
        query = _first_query(queries)
        search_depth = _tavily_search_depth(mode)
        search_body: dict[str, Any] = {
            "query": query,
            "search_depth": search_depth,
            "max_results": max_search_results,
            "include_answer": False,
            "include_raw_content": False,
            "include_images": False,
            "include_favicon": True,
        }
        if include_domains:
            search_body["include_domains"] = include_domains
        search_payload = self.http_post(
            label="Tavily Search",
            url=TAVILY_SEARCH_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            body=search_body,
            timeout=60,
        )
        search_results = _json_list(search_payload.get("results"))
        selected_urls = _select_result_urls(search_results, extract_limit)
        if not selected_urls:
            raise ProviderAdapterError("Tavily Search returned no extractable URLs.")

        extract_payload = self._extract_payload(
            urls=selected_urls,
            query=query,
            mode=mode,
        )
        result = self._write_extract_result(
            objective=objective,
            queries=queries,
            output_dir=output_dir,
            workflow="tavily-search-extract",
            search_results=search_results,
            search_payload=search_payload,
            extract_payload=extract_payload,
            selected_urls=selected_urls,
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=extract_limit,
        )
        return result

    def extract_pack(
        self,
        *,
        urls: list[str],
        objective: str,
        queries: list[str],
        output_dir: Path,
        mode: str = "advanced",
    ) -> ProviderPackResult:
        _validate_limit(len(urls), "url_count", TAVILY_MAX_EXTRACT_URLS)
        selected_urls = _unique_non_empty(urls)
        if not selected_urls:
            raise ProviderAdapterError("Tavily extract-pack requires at least one URL.")
        extract_payload = self._extract_payload(
            urls=selected_urls,
            query=queries[0] if queries else None,
            mode=mode,
        )
        return self._write_extract_result(
            objective=objective,
            queries=queries,
            output_dir=output_dir,
            workflow="tavily-extract",
            search_results=[],
            search_payload={},
            extract_payload=extract_payload,
            selected_urls=selected_urls,
            include_domains=[],
            max_search_results=0,
            extract_limit=len(selected_urls),
        )

    def map_pack(
        self,
        *,
        url: str,
        output_dir: Path,
        objective: str | None,
        query: str | None,
        instructions: str | None,
        include_domains: list[str],
        exclude_domains: list[str],
        select_paths: list[str],
        select_domains: list[str],
        exclude_paths: list[str],
        map_exclude_domains: list[str],
        max_depth: int,
        max_breadth: int,
        limit: int,
        allow_external: bool,
        timeout: float,
    ) -> dict[str, Any]:
        _validate_limit(max_depth, "max_depth", TAVILY_MAX_MAP_DEPTH)
        _validate_limit(max_breadth, "max_breadth", TAVILY_MAX_MAP_BREADTH)
        _validate_limit(limit, "limit", TAVILY_MAX_MAP_BREADTH)
        if timeout < TAVILY_MIN_MAP_TIMEOUT_SECONDS or timeout > TAVILY_MAX_MAP_TIMEOUT_SECONDS:
            raise ProviderAdapterError(
                "timeout must be between "
                f"{TAVILY_MIN_MAP_TIMEOUT_SECONDS:g} and {TAVILY_MAX_MAP_TIMEOUT_SECONDS:g} seconds."
            )
        body: dict[str, Any] = {
            "url": url,
            "max_depth": max_depth,
            "max_breadth": max_breadth,
            "limit": limit,
            "allow_external": allow_external,
            "timeout": timeout,
            "include_usage": True,
        }
        if instructions:
            body["instructions"] = instructions
        if select_paths:
            body["select_paths"] = select_paths
        if select_domains:
            body["select_domains"] = select_domains
        if exclude_paths:
            body["exclude_paths"] = exclude_paths
        if map_exclude_domains:
            body["exclude_domains"] = map_exclude_domains

        payload = self.http_post(
            label="Tavily Map",
            url=TAVILY_MAP_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            body=body,
            timeout=min(TAVILY_MAX_MAP_TIMEOUT_SECONDS + 10.0, timeout + 10.0),
        )
        records = _tavily_map_records(
            payload,
            query=query or instructions,
            expected_domains=include_domains,
        )
        if not records:
            raise ProviderAdapterError("Tavily Map returned no candidate URLs.")
        policy = PolicyConfig(
            allowed_domains=include_domains,
            denied_domains=exclude_domains,
            allowed_paths=select_paths,
            denied_paths=exclude_paths,
            max_pages=limit,
            max_depth=max_depth,
        )
        report = write_discovery_pack(
            output_dir=output_dir,
            records=records,
            policy=policy,
            objective=objective,
            query=query or instructions,
            source="provider:tavily-map",
            source_path=None,
            max_results=limit,
        )
        report.update(
            {
                "provider": "tavily",
                "workflow": "tavily-map-pack",
                "url": url,
                "request_options": {
                    "max_depth": max_depth,
                    "max_breadth": max_breadth,
                    "limit": limit,
                    "allow_external": allow_external,
                    "include_domains": include_domains,
                    "exclude_domains": exclude_domains,
                    "select_paths": select_paths,
                    "select_domains": select_domains,
                    "exclude_paths": exclude_paths,
                    "map_exclude_domains": map_exclude_domains,
                    "timeout": timeout,
                },
                "response_metadata": {
                    "request_id": payload.get("request_id"),
                    "response_time": payload.get("response_time"),
                    "base_url": payload.get("base_url"),
                },
                "usage": {"map": payload.get("usage")},
            }
        )
        return report

    def _extract_payload(self, *, urls: list[str], query: str | None, mode: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "urls": urls,
            "extract_depth": _tavily_extract_depth(mode),
            "format": "markdown",
            "include_favicon": True,
            "include_usage": True,
        }
        if query:
            body["query"] = query
        return self.http_post(
            label="Tavily Extract",
            url=TAVILY_EXTRACT_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            body=body,
            timeout=90,
        )

    def _write_extract_result(
        self,
        *,
        objective: str,
        queries: list[str],
        output_dir: Path,
        workflow: str,
        search_results: list[Any],
        search_payload: dict[str, Any],
        extract_payload: dict[str, Any],
        selected_urls: list[str],
        include_domains: list[str],
        max_search_results: int,
        extract_limit: int,
    ) -> ProviderPackResult:
        search_by_url = {str(item.get("url")): item for item in search_results if isinstance(item, dict)}
        documents: list[ProviderDocument] = []
        for item in _json_list(extract_payload.get("results")):
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "")
            if not url:
                continue
            search_item = search_by_url.get(url, {})
            raw_content = str(item.get("raw_content") or "").strip()
            fallback_content = str(search_item.get("content") or "").strip()
            content = raw_content or fallback_content
            if not content:
                continue
            documents.append(
                ProviderDocument(
                    url=url,
                    title=str(search_item.get("title") or item.get("title") or url),
                    content=content,
                    source_type="tavily",
                    metadata={
                        "provider": "tavily",
                        "search_score": search_item.get("score"),
                        "favicon": item.get("favicon") or search_item.get("favicon"),
                        "content_source": "extract.raw_content" if raw_content else "search.content",
                    },
                )
            )
        failed_results = _json_list(extract_payload.get("failed_results"))
        if not documents:
            raise ProviderAdapterError("Tavily Extract returned no non-empty documents.")
        usage = {
            "search": search_payload.get("usage"),
            "extract": extract_payload.get("usage"),
        }
        cost_units = tavily_cost_units(
            search_payload.get("usage"),
            extract_payload.get("usage"),
            tavily_credit_usd=None,
        )
        pack_path = write_provider_pack(
            output_dir=output_dir,
            provider="tavily",
            workflow=workflow,
            objective=objective,
            queries=queries,
            documents=documents,
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=extract_limit,
            selected_urls=selected_urls,
            search_result_count=len(search_results),
            extract_result_count=len(documents),
            extract_error_count=len(failed_results),
            usage=usage,
            response_metadata={
                "search_request_id": search_payload.get("request_id"),
                "extract_request_id": extract_payload.get("request_id"),
                "search_response_time": search_payload.get("response_time"),
                "extract_response_time": extract_payload.get("response_time"),
            },
        )
        return ProviderPackResult(
            provider="tavily",
            workflow=workflow,
            output_dir=output_dir,
            pack_path=pack_path,
            documents=documents,
            selected_urls=selected_urls,
            search_result_count=len(search_results),
            extract_result_count=len(documents),
            extract_error_count=len(failed_results),
            usage=usage,
            response_metadata={
                "search_request_id": search_payload.get("request_id"),
                "extract_request_id": extract_payload.get("request_id"),
                "search_response_time": search_payload.get("response_time"),
                "extract_response_time": extract_payload.get("response_time"),
            },
            cost_units=cost_units,
        )


class ExaAdapter(LiveProviderAdapter):
    """Exa Search / Contents adapter using the public REST API."""

    provider = "exa"
    label = "Exa"

    def search_extract_pack(
        self,
        *,
        objective: str,
        queries: list[str],
        output_dir: Path,
        include_domains: list[str],
        max_search_results: int,
        extract_limit: int,
        mode: str = "advanced",
    ) -> ProviderPackResult:
        _validate_limit(max_search_results, "max_search_results", EXA_MAX_SEARCH_RESULTS)
        query = _first_query(queries)
        body: dict[str, Any] = {
            "query": query,
            "numResults": max_search_results,
            "contents": _exa_search_contents_options(mode),
        }
        search_type = _exa_search_type(mode)
        if search_type:
            body["type"] = search_type
        if include_domains:
            body["includeDomains"] = include_domains
        payload = self.http_post(
            label="Exa Search",
            url=EXA_SEARCH_URL,
            headers={"x-api-key": self.api_key},
            body=body,
            timeout=90,
        )
        results = _json_list(payload.get("results"))
        documents = _exa_documents(results, source_type="exa")
        if extract_limit:
            documents = documents[:extract_limit]
        if not documents:
            raise ProviderAdapterError("Exa Search returned no non-empty documents.")
        selected_urls = [document.url for document in documents]
        return self._write_result(
            objective=objective,
            queries=queries,
            output_dir=output_dir,
            workflow="exa-search-contents",
            documents=documents,
            selected_urls=selected_urls,
            search_result_count=len(results),
            extract_error_count=max(0, len(results) - len(documents)),
            payload=payload,
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=len(documents),
        )

    def extract_pack(
        self,
        *,
        urls: list[str],
        objective: str,
        queries: list[str],
        output_dir: Path,
        mode: str = "advanced",
    ) -> ProviderPackResult:
        selected_urls = _unique_non_empty(urls)
        _validate_limit(len(selected_urls), "url_count", EXA_MAX_CONTENT_URLS)
        if not selected_urls:
            raise ProviderAdapterError("Exa extract-pack requires at least one URL.")
        body: dict[str, Any] = {
            "urls": selected_urls,
            "text": {"maxCharacters": DEFAULT_MAX_FULL_CONTENT_CHARS},
            "highlights": True,
        }
        payload = self.http_post(
            label="Exa Contents",
            url=EXA_CONTENTS_URL,
            headers={"x-api-key": self.api_key},
            body=body,
            timeout=90,
        )
        results = _json_list(payload.get("results"))
        documents = _exa_documents(results, source_type="exa")
        if not documents:
            raise ProviderAdapterError("Exa Contents returned no non-empty documents.")
        return self._write_result(
            objective=objective,
            queries=queries,
            output_dir=output_dir,
            workflow="exa-contents",
            documents=documents,
            selected_urls=selected_urls,
            search_result_count=0,
            extract_error_count=max(0, len(selected_urls) - len(documents)),
            payload=payload,
            include_domains=[],
            max_search_results=0,
            extract_limit=len(selected_urls),
        )

    def _write_result(
        self,
        *,
        objective: str,
        queries: list[str],
        output_dir: Path,
        workflow: str,
        documents: list[ProviderDocument],
        selected_urls: list[str],
        search_result_count: int,
        extract_error_count: int,
        payload: dict[str, Any],
        include_domains: list[str],
        max_search_results: int,
        extract_limit: int,
    ) -> ProviderPackResult:
        cost_dollars = payload.get("costDollars")
        estimated_cost = cost_dollars_total(cost_dollars)
        pack_path = write_provider_pack(
            output_dir=output_dir,
            provider="exa",
            workflow=workflow,
            objective=objective,
            queries=queries,
            documents=documents,
            include_domains=include_domains,
            max_search_results=max_search_results,
            extract_limit=extract_limit,
            selected_urls=selected_urls,
            search_result_count=search_result_count,
            extract_result_count=len(documents),
            extract_error_count=extract_error_count,
            usage={"cost_dollars": cost_dollars},
            response_metadata={
                "request_id": payload.get("requestId"),
                "resolved_search_type": payload.get("resolvedSearchType"),
            },
            cost_dollars=cost_dollars if isinstance(cost_dollars, dict) else None,
        )
        return ProviderPackResult(
            provider="exa",
            workflow=workflow,
            output_dir=output_dir,
            pack_path=pack_path,
            documents=documents,
            selected_urls=selected_urls,
            search_result_count=search_result_count,
            extract_result_count=len(documents),
            extract_error_count=extract_error_count,
            usage={"cost_dollars": cost_dollars},
            response_metadata={
                "request_id": payload.get("requestId"),
                "resolved_search_type": payload.get("resolvedSearchType"),
            },
            cost_dollars=cost_dollars if isinstance(cost_dollars, dict) else None,
            estimated_cost_usd=estimated_cost,
        )


def provider_adapter(
    provider: ProviderName | str,
    *,
    api_key: str | None = None,
    http_post: JsonPost | None = None,
) -> LiveProviderAdapter:
    name = normalize_provider_name(provider)
    if name == "tavily":
        return TavilyAdapter(api_key=api_key, http_post=http_post)
    if name == "exa":
        return ExaAdapter(api_key=api_key, http_post=http_post)
    raise ProviderAdapterError("Parallel uses the dedicated parallel_workflows adapter for now.")


def require_provider_api_key(provider: ProviderName | str) -> str:
    name = normalize_provider_name(provider)
    config = PROVIDER_CONFIGS[name]
    lookup = lookup_provider_api_key(name)
    if lookup.invalid_reason:
        raise ProviderAdapterError(
            f"{config.label} workflows found an invalid API key source "
            f"({lookup.source}): {lookup.invalid_reason}."
        )
    if not lookup.value:
        raise ProviderAdapterError(
            f"{config.label} workflows require {config.api_key_env_var}. Store it in "
            "~/.config/docpull/secrets.env, write .env.local with `docpull providers init`, "
            "or export it in the environment."
        )
    return lookup.value


def normalize_live_providers(
    *,
    parallel: bool,
    tavily: bool,
    exa: bool,
    live_providers: list[str] | None,
) -> list[ProviderName]:
    selected: list[str] = []
    if parallel:
        selected.append("parallel")
    if tavily:
        selected.append("tavily")
    if exa:
        selected.append("exa")
    selected.extend(live_providers or [])
    normalized: list[ProviderName] = []
    for raw_provider in selected:
        provider = raw_provider.strip().lower()
        if provider in {"auto", "all"}:
            for name in PROVIDER_NAMES:
                if name not in normalized:
                    normalized.append(name)
            continue
        if provider not in PROVIDER_CONFIGS:
            raise ProviderAdapterError(f"Unsupported live provider: {raw_provider}")
        name = provider  # type: ignore[assignment]
        if name not in normalized:
            normalized.append(name)
    return normalized


def live_provider_statuses(
    providers: list[ProviderName],
    *,
    parallel_sdk_installed: Callable[[], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    statuses: dict[str, dict[str, Any]] = {}
    for provider in providers:
        config = PROVIDER_CONFIGS[provider]
        lookup = lookup_provider_api_key(provider)
        api_key_present = bool(lookup.value)
        sdk_installed = True
        reason = "ready"
        ready = api_key_present
        if lookup.invalid_reason:
            reason = "invalid_api_key"
            ready = False
        if provider == "parallel":
            sdk_installed = (
                parallel_sdk_installed() if parallel_sdk_installed else _default_parallel_sdk_installed()
            )
            ready = api_key_present and sdk_installed
            if lookup.invalid_reason:
                reason = "invalid_api_key"
                ready = False
            elif api_key_present and not sdk_installed:
                reason = "missing_optional_sdk"
        if not api_key_present and not lookup.invalid_reason:
            reason = "missing_api_key"
        statuses[provider] = {
            "provider": provider,
            "label": config.label,
            "ready": ready,
            "reason": reason,
            "api_key_env_var": config.api_key_env_var,
            "api_key_present": api_key_present,
            "api_key_source": lookup.source,
            "api_key_source_path": str(lookup.path) if lookup.path else None,
            "api_key_invalid_reason": lookup.invalid_reason,
            "sdk_installed": sdk_installed,
        }
    return statuses


def write_provider_pack(
    *,
    output_dir: Path,
    provider: ProviderName,
    workflow: str,
    objective: str,
    queries: list[str],
    documents: list[ProviderDocument],
    include_domains: list[str],
    max_search_results: int,
    extract_limit: int,
    selected_urls: list[str],
    search_result_count: int,
    extract_result_count: int,
    extract_error_count: int,
    usage: dict[str, Any],
    response_metadata: dict[str, Any],
    cost_dollars: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ndjson_path = output_dir / "documents.ndjson"
    manifest = CorpusManifest(output_dir, output_format="ndjson")
    counter = TokenCounter()
    with ndjson_path.open("w", encoding="utf-8") as handle:
        for document in documents:
            record = DocumentRecord.from_page(
                url=document.url,
                title=document.title,
                content=document.content,
                metadata=document.metadata,
                extraction={
                    "provider": provider,
                    "workflow": workflow,
                },
                source_type=document.source_type,
                token_count=counter.count(document.content),
            )
            manifest.add_record(record, ndjson_path)
            handle.write(json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False))
            handle.write("\n")
    manifest_path = manifest.finalize()
    sources = [
        {
            "index": index,
            "url": document.url,
            "title": document.title,
            "source_type": document.source_type,
        }
        for index, document in enumerate(documents, start=1)
    ]
    sources_path = _write_provider_sources_md(output_dir, workflow=workflow, sources=sources)
    pack_path = output_dir / f"{provider}.pack.json"
    pack: dict[str, Any] = {
        "schema_version": PROVIDER_PACK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "provider": provider,
        "workflow": workflow,
        "objective": objective,
        "queries": queries,
        "record_count": len(documents),
        "item_count": len(documents),
        "search_result_count": search_result_count,
        "extract_result_count": extract_result_count,
        "extract_error_count": extract_error_count,
        "selected_urls": selected_urls,
        "request_options": {
            "source_policy": {"include_domains": include_domains},
            "max_search_results": max_search_results,
            "extract_limit": extract_limit,
            "content_policy": "provider_returned_text",
        },
        "usage": usage,
        "response_metadata": response_metadata,
        "artifacts": {
            "documents_ndjson": _relative_path(ndjson_path, output_dir),
            "manifest": _relative_path(manifest_path, output_dir),
            "sources_md": _relative_path(sources_path, output_dir),
            "pack_metadata": _relative_path(pack_path, output_dir),
        },
        "sources": sources,
    }
    if cost_dollars is not None:
        pack["cost_dollars"] = cost_dollars
    pack_path.write_text(json.dumps(pack, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return pack_path


def provider_case_payload(
    result: ProviderPackResult,
    *,
    name: str,
    workflow: str,
    wall_seconds: float,
    rss_before: int | None = None,
    include_domains: list[str],
    objective: str,
    queries: list[str],
    tavily_credit_usd: float | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "workflow": workflow,
        "output_dir": str(result.output_dir),
        "wall_seconds": round(wall_seconds, 3),
    }
    if rss_before is not None:
        rss_after = _peak_rss_bytes()
        payload.update(
            {
                "rss_baseline_mb": round(rss_before / (1024 * 1024), 1),
                "rss_peak_mb": round(rss_after / (1024 * 1024), 1),
                "rss_delta_mb": round(max(0, rss_after - rss_before) / (1024 * 1024), 1),
            }
        )
    if result.cost_units:
        cost_units = dict(result.cost_units)
        if result.provider == "tavily":
            cost_units = tavily_cost_units(
                result.usage.get("search"),
                result.usage.get("extract"),
                tavily_credit_usd=tavily_credit_usd,
            )
        payload["cost_units"] = cost_units
        estimated = cost_units.get("estimated_cost_usd")
        if isinstance(estimated, int | float):
            payload["estimated_cost_usd"] = float(estimated)
    if result.estimated_cost_usd is not None:
        payload["estimated_cost_usd"] = result.estimated_cost_usd
    payload["artifact_size_bytes"] = dir_size(result.output_dir)
    attach_pack_metadata(payload, result.pack_path)
    attach_pack_intelligence(
        payload,
        result.output_dir,
        include_domains,
        objective=objective,
        queries=queries,
    )
    return payload


def attach_pack_intelligence(
    payload: dict[str, Any],
    output_dir: Path,
    include_domains: list[str],
    *,
    objective: str,
    queries: list[str],
) -> None:
    documents_path = output_dir / "documents.ndjson"
    if not documents_path.exists():
        payload["pack_score"] = None
        payload["source_score_count"] = 0
        payload["pack_intelligence"] = None
        return
    try:
        prepared = prepare_pack(
            output_dir,
            objective=objective,
            search_queries=queries,
            required_domains=include_domains,
        )
    except Exception as err:  # noqa: BLE001
        payload["pack_intelligence"] = None
        payload["pack_intelligence_error"] = {
            "type": type(err).__name__,
            "message": _short_error_detail(str(err)),
        }
        try:
            attach_pack_scores(payload, output_dir, include_domains)
        except Exception as score_err:  # noqa: BLE001
            payload["pack_score"] = None
            payload["source_score_count"] = 0
            payload["pack_score_error"] = {
                "type": type(score_err).__name__,
                "message": _short_error_detail(str(score_err)),
            }
        payload["artifact_size_bytes"] = dir_size(output_dir)
        return
    score = json.loads((output_dir / "pack.score.json").read_text(encoding="utf-8"))
    sources = json.loads((output_dir / "source.scores.json").read_text(encoding="utf-8"))
    _attach_pack_score_payload(payload, score, sources)
    payload["pack_intelligence"] = {
        "summary": prepared["summary"],
        "artifacts": prepared["artifacts"],
        "search_queries": prepared["search_queries"],
    }
    payload["artifact_size_bytes"] = dir_size(output_dir)


def attach_pack_scores(payload: dict[str, Any], output_dir: Path, include_domains: list[str]) -> None:
    documents_path = output_dir / "documents.ndjson"
    if not documents_path.exists():
        payload["pack_score"] = None
        payload["source_score_count"] = 0
        return
    score = score_pack(output_dir, required_domains=include_domains)
    sources = score_pack_sources(output_dir, required_domains=include_domains)
    (output_dir / "pack.score.json").write_text(
        json.dumps(score, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "source.scores.json").write_text(
        json.dumps(sources, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _attach_pack_score_payload(payload, score, sources)


def attach_pack_metadata(payload: dict[str, Any], path: Path) -> None:
    if not path.exists():
        return
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return
    raw_metadata = raw.get("metadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    payload["pack_metadata"] = {
        "workflow": raw.get("workflow"),
        "item_count": raw.get("item_count"),
        "record_count": raw.get("record_count"),
        "search_id": raw.get("search_id") or metadata.get("search_id"),
        "session_id": raw.get("session_id") or metadata.get("session_id"),
        "selected_urls": raw.get("selected_urls"),
        "search_result_count": raw.get("search_result_count"),
        "extract_result_count": raw.get("extract_result_count"),
        "extract_error_count": raw.get("extract_error_count"),
        "usage": raw.get("usage") or metadata.get("usage"),
        "provider": raw.get("provider"),
        "cost_dollars": raw.get("cost_dollars"),
    }


def http_json_post(
    *,
    label: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    max_attempts: int = HTTP_RETRY_MAX_ATTEMPTS,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    """POST JSON with bounded retry on transient provider errors."""

    last_error: ProviderAdapterError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return http_json_post_once(label=label, url=url, headers=headers, body=body, timeout=timeout)
        except _TransientHTTPError as err:
            last_error = ProviderAdapterError(str(err))
            last_error.__cause__ = err.__cause__
            if attempt >= max_attempts:
                break
            delay = _retry_delay_seconds(attempt=attempt, retry_after=err.retry_after)
            sleep(delay)
    if last_error is None:
        raise ProviderAdapterError(f"{label} request failed without a captured response error.")
    raise last_error


class _NoRedirectHandler(HTTPRedirectHandler):
    """Refuse provider POST redirects so auth headers cannot be forwarded."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise HTTPError(req.full_url, code, f"Refused redirect to {newurl!r}", headers, fp)


def http_json_post_once(
    *,
    label: str,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https":
        raise ProviderAdapterError(f"{label} URL must use HTTPS.")
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            **headers,
        },
        method="POST",
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            raw_bytes = response.read(HTTP_MAX_RESPONSE_BYTES + 1)
    except HTTPError as err:
        detail = _redact_secret_like(err.read(HTTP_MAX_ERROR_BYTES).decode("utf-8", errors="replace"))
        message = f"{label} returned HTTP {err.code}: {_short_error_detail(detail)}"
        if err.code in HTTP_RETRY_TRANSIENT_STATUSES:
            transient = _TransientHTTPError(message, retry_after=_parse_retry_after(err))
            transient.__cause__ = err
            raise transient from err
        raise ProviderAdapterError(message) from err
    except URLError as err:
        message = f"{label} request failed: {err.reason}"
        transient = _TransientHTTPError(message, retry_after=None)
        transient.__cause__ = err
        raise transient from err
    if len(raw_bytes) > HTTP_MAX_RESPONSE_BYTES:
        raise ProviderAdapterError(f"{label} response exceeds {HTTP_MAX_RESPONSE_BYTES}-byte limit.")
    try:
        parsed = json.loads(raw_bytes.decode("utf-8"))
    except json.JSONDecodeError as err:
        raise ProviderAdapterError(f"{label} returned invalid JSON: {err}") from err
    if not isinstance(parsed, dict):
        raise ProviderAdapterError(f"{label} returned JSON {type(parsed).__name__}, expected object.")
    return parsed


class _TransientHTTPError(Exception):
    """Internal marker for retryable HTTP failures."""

    def __init__(self, message: str, *, retry_after: float | None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def tavily_cost_units(
    search_usage: Any,
    extract_usage: Any,
    tavily_credit_usd: float | None,
) -> dict[str, Any]:
    search_credits = usage_credits(search_usage)
    extract_credits = usage_credits(extract_usage)
    total_credits = round(search_credits + extract_credits, 6)
    payload: dict[str, Any] = {
        "provider": "tavily",
        "unit": "credit",
        "search_credits": search_credits,
        "extract_credits": extract_credits,
        "total_credits": total_credits,
        "credit_usd": tavily_credit_usd,
    }
    if tavily_credit_usd is not None:
        payload["estimated_cost_usd"] = round(total_credits * tavily_credit_usd, 6)
    return payload


def usage_credits(value: Any) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        for key in ("credits", "credit", "total_credits", "totalCredits"):
            raw = value.get(key)
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                return float(raw)
    return 0.0


def cost_dollars_total(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    total = value.get("total")
    if isinstance(total, int | float) and not isinstance(total, bool):
        return round(float(total), 6)
    return None


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _write_provider_sources_md(
    output_dir: Path,
    *,
    workflow: str,
    sources: list[dict[str, Any]],
) -> Path:
    lines = [
        "# Context Pack Sources",
        "",
        f"Workflow: `{workflow}`",
        "",
        "## Sources",
        "",
    ]
    for source in sources:
        index = source.get("index")
        title = str(source.get("title") or source.get("url") or "Untitled")
        url = str(source.get("url") or "")
        lines.append(f"{index}. {_md_link(title, url)}")
        if source.get("source_type"):
            lines.append(f"   - Source type: `{source['source_type']}`")
        lines.append("   - Records file: `documents.ndjson`")
    path = output_dir / "sources.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _exa_documents(results: list[Any], *, source_type: str) -> list[ProviderDocument]:
    documents: list[ProviderDocument] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        content = str(item.get("text") or "").strip()
        content_source = "search.text"
        if not content:
            highlights = [str(value) for value in _json_list(item.get("highlights")) if value]
            content = "\n\n".join(highlights).strip()
            content_source = "search.highlights"
        if not content:
            content = str(item.get("summary") or "").strip()
            content_source = "search.summary"
        if not content:
            continue
        documents.append(
            ProviderDocument(
                url=url,
                title=str(item.get("title") or url),
                content=content,
                source_type=source_type,
                metadata={
                    "provider": "exa",
                    "id": item.get("id"),
                    "published_date": item.get("publishedDate"),
                    "author": item.get("author"),
                    "image": item.get("image"),
                    "favicon": item.get("favicon"),
                    "highlight_scores": item.get("highlightScores"),
                    "content_source": content_source,
                },
            )
        )
    return documents


def _tavily_map_records(
    payload: dict[str, Any],
    *,
    query: str | None,
    expected_domains: list[str],
) -> list[CandidateSourceRecord]:
    generated_at = utc_now_iso()
    records: list[CandidateSourceRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(_json_list(payload.get("results")), start=1):
        url = _tavily_map_url(item)
        if not url:
            continue
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)

        title = _first_text_value(item, ("title", "name", "headline"))
        snippet = _first_text_value(item, ("snippet", "content", "description"))
        local_score = score_source(
            url=url,
            title=title or "",
            expected_domains=expected_domains,
        )
        records.append(
            CandidateSourceRecord(
                generated_at=generated_at,
                url=url,
                source="provider:tavily-map",
                title=title,
                snippet=snippet,
                provider="tavily",
                score=float(local_score["score"]),
                rank=index,
                query=query,
                discovered_at=generated_at,
                raw_ref=f"tavily-map.results[{index}]",
                metadata={
                    "local_score": local_score["score"],
                    "score_grade": local_score["grade"],
                    "score_reasons": local_score["reasons"],
                    "base_url": payload.get("base_url"),
                },
            )
        )
    return records


def _tavily_map_url(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return _first_text_value(item, ("url", "link", "href")) or ""
    return ""


def _first_text_value(item: Any, keys: tuple[str, ...]) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None


def _first_query(queries: list[str]) -> str:
    for query in queries:
        cleaned = query.strip()
        if cleaned:
            return cleaned
    raise ProviderAdapterError("At least one non-empty query is required.")


def _json_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _select_result_urls(results: list[Any], limit: int) -> list[str]:
    selected: list[str] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url and url not in selected:
            selected.append(url)
        if len(selected) >= limit:
            break
    return selected


def _unique_non_empty(values: list[str]) -> list[str]:
    selected: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in selected:
            selected.append(cleaned)
    return selected


def _validate_limit(value: int, field: str, maximum: int) -> None:
    if value < 1:
        raise ProviderAdapterError(f"{field} must be at least 1.")
    if value > maximum:
        raise ProviderAdapterError(f"{field} must be at most {maximum}.")


def _tavily_search_depth(mode: str) -> str:
    return {
        "turbo": "fast",
        "basic": "basic",
        "advanced": "advanced",
        "fast": "fast",
        "ultra-fast": "ultra-fast",
    }.get(mode, "advanced")


def _tavily_extract_depth(mode: str) -> str:
    return "advanced" if mode == "advanced" else "basic"


def _exa_search_type(mode: str) -> str:
    return {
        "turbo": "fast",
        "basic": "auto",
        "advanced": "auto",
        "fast": "fast",
        "instant": "instant",
    }.get(mode, "auto")


def _exa_search_contents_options(_mode: str) -> dict[str, Any]:
    return {
        "text": {"maxCharacters": DEFAULT_MAX_FULL_CONTENT_CHARS},
        "highlights": True,
    }


def _attach_pack_score_payload(
    payload: dict[str, Any],
    score: dict[str, Any],
    sources: dict[str, Any],
) -> None:
    payload["pack_score"] = {
        "score": score["score"],
        "grade": score["grade"],
        "summary": score["summary"],
        "issues": score["issues"],
        "warnings": score["warnings"],
    }
    payload["source_score_count"] = sources["source_count"]


def _parse_retry_after(err: HTTPError) -> float | None:
    raw = err.headers.get("Retry-After") if err.headers else None
    if not raw:
        return None
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, HTTP_RETRY_CAP_SECONDS)


def _retry_delay_seconds(*, attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    base = min(HTTP_RETRY_CAP_SECONDS, 2.0 ** (attempt - 1))
    return base + random.uniform(0.0, 0.5)  # nosec B311


def _redact_secret_like(value: str) -> str:
    return re.sub(
        r"(?i)(?:bearer\s+|x-api-key\s*[:=]\s*|api[-_]?key\s*[\"':=]\s*|tvly-|exa_|sk-)"
        r"[A-Za-z0-9._\-]{6,}",
        "[REDACTED]",
        value,
    )


def _short_error_detail(value: str, limit: int = 500) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _md_inline_text(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]").replace("\n", " ").strip()


def _md_safe_url(value: str) -> str:
    return value.replace(")", "%29").replace("\n", "").strip()


def _md_link(title: str, url: str) -> str:
    safe_url = _md_safe_url(url)
    if not safe_url:
        return _md_inline_text(title)
    return f"[{_md_inline_text(title) or safe_url}]({safe_url})"


def _default_parallel_sdk_installed() -> bool:
    return importlib.util.find_spec("parallel") is not None


def _relative_path(path: Path, base_dir: Path) -> str:
    try:
        return str(path.resolve().relative_to(base_dir.resolve()))
    except ValueError:
        return str(path)


def _peak_rss_bytes() -> int:
    try:
        import resource
    except ImportError:
        return 0
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(rss if sys.platform == "darwin" else rss * 1024)
