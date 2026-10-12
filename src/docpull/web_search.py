"""Web search through backends the user chooses: self-hosted SearXNG or Marginalia.

DocPull has no search index of its own. ``search_web`` queries a backend the
caller names explicitly, never a paid provider, then optionally fetches the top
results through the local fetch pipeline so content keeps DocPull's SSRF and
robots.txt protections.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from . import __version__

BACKENDS = ("searxng", "marginalia")
MAX_RESULTS = 20
SEARXNG_URL_ENV = "DOCPULL_SEARXNG_URL"
MARGINALIA_KEY_ENV = "DOCPULL_MARGINALIA_KEY"
MARGINALIA_URL = "https://api2.marginalia-search.com/search"
_SEARXNG_MAX_PAGES = 3
_TIMEOUT = aiohttp.ClientTimeout(total=20)


class WebSearchError(RuntimeError):
    """The search backend is unconfigured, unreachable, or returned bad data."""


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    score: float | None = None
    published: str | None = None
    content: str | None = None
    content_error: str | None = None


@dataclass(frozen=True)
class SearchResponse:
    query: str
    backend: str
    hits: list[SearchHit]
    license: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


async def search_web(
    query: str,
    *,
    backend: str,
    max_results: int = 10,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
    include_content: bool = False,
    searxng_url: str | None = None,
    marginalia_key: str | None = None,
) -> SearchResponse:
    """Search the web with an explicitly chosen backend."""
    query = query.strip()
    if not query:
        raise WebSearchError("query must not be empty")
    if backend not in BACKENDS:
        raise WebSearchError(f"backend must be one of: {', '.join(BACKENDS)}")
    if not 1 <= max_results <= MAX_RESULTS:
        raise WebSearchError(f"max_results must be between 1 and {MAX_RESULTS}")
    include = [_domain(item) for item in include_domains or []]
    exclude = [_domain(item) for item in exclude_domains or []]

    key = marginalia_key or os.environ.get(MARGINALIA_KEY_ENV) or "public"
    headers = {"User-Agent": f"docpull/{__version__} (+https://github.com/raintree-technology/docpull)"}
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers) as session:
        if backend == "searxng":
            base = (searxng_url or os.environ.get(SEARXNG_URL_ENV) or "").rstrip("/")
            if not base:
                raise WebSearchError(f"SearXNG needs --searxng-url or {SEARXNG_URL_ENV}")
            hits, license_text = await _searxng(session, base, query, include, exclude, max_results)
        else:
            hits, license_text = await _marginalia(session, key, query, include, exclude, max_results)

    warnings = []
    if backend == "marginalia" and key == "public":
        warnings.append(
            "Marginalia's shared public key is rate limited; set DOCPULL_MARGINALIA_KEY for heavy use."
        )
    if include_content:
        hits = await _attach_content(hits)
    return SearchResponse(query=query, backend=backend, hits=hits, license=license_text, warnings=warnings)


async def _searxng(
    session: aiohttp.ClientSession,
    base: str,
    query: str,
    include: list[str],
    exclude: list[str],
    max_results: int,
) -> tuple[list[SearchHit], str | None]:
    # A single include domain narrows the upstream query; filtering below is authoritative.
    upstream_query = f"{query} site:{include[0]}" if len(include) == 1 else query
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for page in range(1, _SEARXNG_MAX_PAGES + 1):
        params = {"q": upstream_query, "format": "json", "pageno": str(page)}
        payload = await _get_json(session, f"{base}/search", params=params, backend="SearXNG")
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            break
        for item in results:
            hit = _hit(item, snippet_key="content")
            if hit and hit.url not in seen and _allowed(hit.url, include, exclude):
                seen.add(hit.url)
                hits.append(hit)
        if len(hits) >= max_results:
            break
    return hits[:max_results], None


async def _marginalia(
    session: aiohttp.ClientSession,
    key: str,
    query: str,
    include: list[str],
    exclude: list[str],
    max_results: int,
) -> tuple[list[SearchHit], str | None]:
    upstream_query = f"{query} site:{include[0]}" if len(include) == 1 else query
    count = min(100, max_results * 3 if include or exclude else max_results)
    payload = await _get_json(
        session,
        MARGINALIA_URL,
        params={"query": upstream_query, "count": str(count)},
        headers={"API-Key": key},
        backend="Marginalia",
    )
    results = payload.get("results")
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for item in results if isinstance(results, list) else []:
        hit = _hit(item, snippet_key="description")
        if hit and hit.url not in seen and _allowed(hit.url, include, exclude):
            seen.add(hit.url)
            hits.append(hit)
    license_text = payload.get("license")
    return hits[:max_results], license_text if isinstance(license_text, str) else None


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, str],
    backend: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        async with session.get(url, params=params, headers=headers) as response:
            if response.status == 403 and backend == "SearXNG":
                raise WebSearchError(
                    "SearXNG refused JSON output; enable `json` under search.formats in settings.yml"
                )
            if response.status in {429, 503} and backend == "Marginalia":
                raise WebSearchError(
                    f"Marginalia is rate limiting this key (HTTP {response.status}); "
                    f"retry later or set {MARGINALIA_KEY_ENV} to your own key"
                )
            if response.status >= 400:
                raise WebSearchError(f"{backend} returned HTTP {response.status}")
            payload = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise WebSearchError(f"{backend} request failed: {type(err).__name__}: {err}") from err
    except json.JSONDecodeError as err:
        raise WebSearchError(f"{backend} returned invalid JSON") from err
    if not isinstance(payload, dict):
        raise WebSearchError(f"{backend} returned an unexpected response")
    return payload


def _hit(item: Any, *, snippet_key: str) -> SearchHit | None:
    if not isinstance(item, dict):
        return None
    url = item.get("url")
    if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"}:
        return None
    score = item.get("score")
    published = item.get("publishedDate")
    return SearchHit(
        url=url,
        title=str(item.get("title") or ""),
        snippet=str(item.get(snippet_key) or ""),
        score=float(score) if isinstance(score, int | float) and not isinstance(score, bool) else None,
        published=published if isinstance(published, str) else None,
    )


def _domain(value: str) -> str:
    text = value.strip().lower()
    host = urlsplit(text).hostname if "://" in text else text.split("/")[0]
    if not host:
        raise WebSearchError(f"Invalid domain: {value!r}")
    return host.removeprefix("www.")


def _allowed(url: str, include: list[str], exclude: list[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower().removeprefix("www.")

    def matches(domain: str) -> bool:
        return host == domain or host.endswith("." + domain)

    if include and not any(matches(domain) for domain in include):
        return False
    return not any(matches(domain) for domain in exclude)


async def _attach_content(hits: list[SearchHit]) -> list[SearchHit]:
    from .compat_api import fetch_page

    pages = await asyncio.gather(*(fetch_page(hit.url) for hit in hits))
    return [
        replace(hit, content=None if page.error else page.markdown, content_error=page.error)
        for hit, page in zip(hits, pages, strict=True)
    ]


def create_search_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull search",
        description="Search the web through self-hosted SearXNG or Marginalia; no paid provider is used",
    )
    parser.add_argument("query")
    parser.add_argument("--backend", choices=BACKENDS, required=True)
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--include-domain", action="append", default=[], dest="include_domains")
    parser.add_argument("--exclude-domain", action="append", default=[], dest="exclude_domains")
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Fetch each result through the local pipeline and include its Markdown",
    )
    parser.add_argument("--searxng-url", help=f"SearXNG base URL (default: ${SEARXNG_URL_ENV})")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def run_search_cli(argv: list[str] | None = None) -> int:
    args = create_search_parser().parse_args(argv)
    try:
        response = asyncio.run(
            search_web(
                args.query,
                backend=args.backend,
                max_results=args.max_results,
                include_domains=args.include_domains,
                exclude_domains=args.exclude_domains,
                include_content=args.include_content,
                searxng_url=args.searxng_url,
            )
        )
    except WebSearchError as err:
        print(f"docpull search: {err}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))
        return 0
    for warning in response.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if response.license:
        print(f"Results license: {response.license}")
    for index, hit in enumerate(response.hits, start=1):
        print(f"{index:>2}. {hit.title or hit.url}\n    {hit.url}")
        if hit.snippet:
            print(f"    {hit.snippet[:200]}")
        if hit.content_error:
            print(f"    content unavailable: {hit.content_error}")
    if not response.hits:
        print("No results.")
    return 0
