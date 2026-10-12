"""Local extract endpoints that accept hosted web-context API request shapes.

Agents written against Tavily ``/extract``, Exa ``/contents``, or Firecrawl
``/scrape`` can point their base URL at ``docpull serve --compat`` instead. Every
page is fetched by the local DocPull pipeline, so SSRF guards, HTTPS-only fetching,
and robots.txt handling apply unchanged. Nothing is sent to those services.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .research import LlmConfig, ResearchError, answer_from_search
from .server import ASGIReceive, ASGIScope, ASGISend, _handle_lifespan, _json_response
from .web_search import SearchResponse, WebSearchError

MAX_BODY_BYTES = 1_000_000
MAX_URLS_PER_REQUEST = 20
FETCH_CONCURRENCY = 4


@dataclass(frozen=True)
class Page:
    """One fetched page, or the reason it could not be fetched."""

    url: str
    markdown: str = ""
    title: str = ""
    status_code: int | None = None
    error: str | None = None


PageFetcher = Callable[[str], Awaitable[Page]]
# (query, max_results, include_domains, exclude_domains, include_content) -> response
SearchFn = Callable[[str, int, list[str], list[str], bool], Awaitable["SearchResponse"]]
FLAVORS = ("tavily", "exa", "firecrawl")


class CompatRequestError(ValueError):
    """Invalid request body for a compatible endpoint."""


async def fetch_page(url: str) -> Page:
    """Fetch one URL to Markdown in memory with the default local pipeline."""
    from .conversion.special_cases import _split_markdown_frontmatter
    from .core.fetcher import Fetcher
    from .models.config import DocpullConfig

    try:
        async with Fetcher(DocpullConfig(url=url)) as fetcher:
            ctx = await fetcher.fetch_one(url, save=False)
    except Exception as err:  # noqa: BLE001 - surfaced per URL, like the hosted APIs
        return Page(url=url, error=str(err) or type(err).__name__)
    if ctx.error or ctx.should_skip or not (ctx.markdown or "").strip():
        reason = ctx.error or ctx.skip_reason or "No extractable content"
        return Page(url=url, status_code=ctx.status_code, error=reason)
    # Hosted extract APIs return the page body only, without DocPull's YAML frontmatter.
    _, body = _split_markdown_frontmatter(ctx.markdown or "")
    return Page(url=url, markdown=body.strip() + "\n", title=ctx.title or "", status_code=ctx.status_code)


class CompatASGIApp:
    """JSON-over-POST ASGI app for the compatible extract endpoints."""

    def __init__(
        self,
        *,
        fetch: PageFetcher = fetch_page,
        search: SearchFn | None = None,
        llm: LlmConfig | None = None,
        api_key: str | None = None,
    ) -> None:
        self.fetch = fetch
        self.search = search
        self.llm = llm
        self.api_key = api_key

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") == "lifespan":
            await _handle_lifespan(receive, send)
            return
        path = (str(scope.get("path") or "/").rstrip("/")) or "/"
        # A /tavily, /exa, or /firecrawl prefix lets clients pick a flavor by base URL.
        flavor = next((name for name in FLAVORS if path.startswith(f"/{name}/")), None)
        if flavor:
            path = path.removeprefix(f"/{flavor}")
        if scope.get("type") != "http":
            await _json_response(send, 500, {"error": "Unsupported ASGI scope"})
            return
        if path == "/health" and scope.get("method") == "GET":
            await _json_response(send, 200, {"status": "ok", "endpoints": sorted(_ROUTES)})
            return
        handler = _ROUTES.get(path)
        if handler is None:
            await _json_response(send, 404, {"error": "Not found", "path": path})
            return
        if scope.get("method") != "POST":
            await _json_response(send, 405, {"error": "Use POST"})
            return
        if not self._authorized(scope):
            await _json_response(send, 401, {"error": "Missing or invalid API key"})
            return
        try:
            body = await _read_body(receive)
            if path == "/search":
                flavor = flavor or ("exa" if _EXA_SEARCH_KEYS & body.keys() else "tavily")
                if flavor == "firecrawl":
                    raise CompatRequestError("Firecrawl search is served at /v1/search or /v2/search")
                handler = _tavily_search if flavor == "tavily" else _exa_search
            payload = await handler(self, body)
        except CompatRequestError as err:
            await _json_response(send, 400, {"error": str(err)})
            return
        except SearchUnavailableError as err:
            await _json_response(send, 501, {"error": str(err)})
            return
        except (WebSearchError, ResearchError) as err:
            await _json_response(send, 502, {"error": str(err)})
            return
        await _json_response(send, 200, payload)

    def _authorized(self, scope: ASGIScope) -> bool:
        if self.api_key is None:
            return True
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])
        }
        supplied = (
            headers.get("x-api-key") or headers.get("authorization", "").removeprefix("Bearer ").strip()
        )
        return hmac.compare_digest(supplied.encode(), self.api_key.encode())

    async def run_search(
        self, query: Any, max_results: Any, include: Any, exclude: Any, content: bool
    ) -> SearchResponse:
        if self.search is None:
            raise SearchUnavailableError("Search is not configured; start the server with --search-backend")
        if not isinstance(query, str) or not query.strip():
            raise CompatRequestError("query is required")
        if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 20:
            raise CompatRequestError("the result count must be an integer from 1 to 20")
        return await self.search(query, max_results, _domains(include), _domains(exclude), content)

    async def fetch_all(self, urls: list[str]) -> list[Page]:
        semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def one(url: str) -> Page:
            async with semaphore:
                return await self.fetch(url)

        return list(await asyncio.gather(*(one(url) for url in urls)))


async def _tavily_extract(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    raw = body.get("urls")
    urls = _urls([raw] if isinstance(raw, str) else raw, field="urls")
    pages = await app.fetch_all(urls)
    return {
        "results": [
            {"url": page.url, "raw_content": page.markdown, "images": []} for page in pages if not page.error
        ],
        "failed_results": [{"url": page.url, "error": page.error} for page in pages if page.error],
        "response_time": round(time.perf_counter() - started, 3),
        "request_id": uuid.uuid4().hex,
    }


async def _exa_contents(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    urls = _urls(body.get("urls") or body.get("ids"), field="urls")
    text = body.get("text", True)
    max_characters = text.get("maxCharacters") if isinstance(text, dict) else None
    if max_characters is not None and (
        isinstance(max_characters, bool) or not isinstance(max_characters, int)
    ):
        raise CompatRequestError("text.maxCharacters must be an integer")
    pages = await app.fetch_all(urls)
    results = []
    statuses = []
    for page in pages:
        if page.error:
            statuses.append(
                {"id": page.url, "status": "error", "error": {"tag": "CRAWL_ERROR", "message": page.error}}
            )
            continue
        statuses.append({"id": page.url, "status": "success"})
        result: dict[str, Any] = {"id": page.url, "url": page.url, "title": page.title}
        if text is not False:
            result["text"] = page.markdown[:max_characters] if max_characters else page.markdown
        results.append(result)
    return {"requestId": uuid.uuid4().hex, "results": results, "statuses": statuses}


async def _firecrawl_scrape(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    url = body.get("url")
    if not isinstance(url, str) or not url.strip():
        raise CompatRequestError("url is required")
    formats = body.get("formats", ["markdown"])
    if not isinstance(formats, list) or not all(isinstance(item, str | dict) for item in formats):
        raise CompatRequestError("formats must be a list")
    names = {item if isinstance(item, str) else str(item.get("type")) for item in formats}
    unsupported = names - {"markdown"}
    if unsupported:
        raise CompatRequestError(
            f"Unsupported formats: {', '.join(sorted(unsupported))}; only markdown is served"
        )
    (page,) = await app.fetch_all([url.strip()])
    if page.error:
        return {"success": False, "error": page.error}
    metadata = {"title": page.title, "sourceURL": page.url, "url": page.url, "statusCode": page.status_code}
    return {"success": True, "data": {"markdown": page.markdown, "metadata": metadata}}


class SearchUnavailableError(RuntimeError):
    """The server was started without a search backend."""


async def _tavily_search(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    raw = body.get("include_raw_content", False)
    wants_answer = bool(body.get("include_answer", False))
    response = await app.run_search(
        body.get("query"),
        body.get("max_results", 5),
        body.get("include_domains"),
        body.get("exclude_domains"),
        bool(raw) or wants_answer,
    )
    answer = None
    if wants_answer:
        answer = (await answer_from_search(response.query, response, llm=app.llm)).answer or None
    results = [
        {
            "title": hit.title,
            "url": hit.url,
            "content": hit.snippet,
            "score": hit.score,
            "raw_content": hit.content if raw else None,
        }
        for hit in response.hits
    ]
    return {
        "query": response.query,
        "answer": answer,
        "images": [],
        "follow_up_questions": None,
        "results": results,
        "response_time": round(time.perf_counter() - started, 3),
        "request_id": uuid.uuid4().hex,
    }


async def _exa_search(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    contents = body.get("contents")
    text = contents.get("text") if isinstance(contents, dict) else body.get("text")
    response = await app.run_search(
        body.get("query"),
        body.get("numResults", 10),
        body.get("includeDomains"),
        body.get("excludeDomains"),
        bool(text),
    )
    results = []
    for hit in response.hits:
        result: dict[str, Any] = {
            "id": hit.url,
            "url": hit.url,
            "title": hit.title,
            "score": hit.score,
            "publishedDate": hit.published,
        }
        if text:
            result["text"] = hit.content or ""
        results.append(result)
    return {"requestId": uuid.uuid4().hex, "resolvedSearchType": "keyword", "results": results}


async def _exa_answer(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    response = await app.run_search(body.get("query"), 5, None, None, True)
    result = await answer_from_search(response.query, response, llm=app.llm)
    include_text = bool(body.get("text", False))
    citations = []
    for source in result.sources:
        citation: dict[str, Any] = {"id": source["url"], "url": source["url"], "title": source["title"]}
        if include_text:
            citation["text"] = "\n\n".join(e.text for e in result.evidence if e.source_id == source["id"])
        citations.append(citation)
    return {"requestId": uuid.uuid4().hex, "answer": result.answer, "citations": citations}


async def _firecrawl_search(app: CompatASGIApp, body: dict[str, Any], *, version: int) -> dict[str, Any]:
    options = body.get("scrapeOptions")
    formats = options.get("formats") if isinstance(options, dict) else None
    markdown = isinstance(formats, list) and "markdown" in formats
    response = await app.run_search(body.get("query"), body.get("limit", 5), None, None, markdown)
    data = []
    for hit in response.hits:
        item: dict[str, Any] = {"url": hit.url, "title": hit.title, "description": hit.snippet}
        if markdown:
            item["markdown"] = hit.content or ""
        data.append(item)
    return {"success": True, "data": data if version == 1 else {"web": data}}


async def _firecrawl_search_v1(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    return await _firecrawl_search(app, body, version=1)


async def _firecrawl_search_v2(app: CompatASGIApp, body: dict[str, Any]) -> dict[str, Any]:
    return await _firecrawl_search(app, body, version=2)


_EXA_SEARCH_KEYS = frozenset({"numResults", "contents", "includeDomains", "excludeDomains", "type"})


_ROUTES: dict[str, Callable[[CompatASGIApp, dict[str, Any]], Awaitable[dict[str, Any]]]] = {
    "/extract": _tavily_extract,
    "/contents": _exa_contents,
    "/v1/scrape": _firecrawl_scrape,
    "/v2/scrape": _firecrawl_scrape,
    "/search": _tavily_search,
    "/answer": _exa_answer,
    "/v1/search": _firecrawl_search_v1,
    "/v2/search": _firecrawl_search_v2,
}


def _domains(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CompatRequestError("domain filters must be lists of strings")
    return value


def _urls(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise CompatRequestError(f"{field} must be a non-empty list of URLs")
    if len(value) > MAX_URLS_PER_REQUEST:
        raise CompatRequestError(f"{field} accepts at most {MAX_URLS_PER_REQUEST} URLs per request")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise CompatRequestError(f"{field} must contain only non-empty strings")
    return [item.strip() for item in value]


async def _read_body(receive: ASGIReceive) -> dict[str, Any]:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunk = bytes(message.get("body") or b"")
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise CompatRequestError(f"Request body exceeds {MAX_BODY_BYTES} bytes")
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    try:
        payload = json.loads(b"".join(chunks) or b"{}")
    except json.JSONDecodeError as err:
        raise CompatRequestError(f"Invalid JSON body: {err}") from err
    if not isinstance(payload, dict):
        raise CompatRequestError("Request body must be a JSON object")
    return payload
