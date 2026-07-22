"""Wayback CDX and Common Crawl archive discovery tests."""

from __future__ import annotations

import asyncio
import json

from docpull.discovery import contracts
from docpull.discovery.contracts import SITE_SCAN_SOURCES, records_from_site_scan
from docpull.discovery_cli import create_discovery_parser
from docpull.http.protocols import HttpResponse

_WAYBACK_ROUTE = "https://web.archive.org/cdx/search/cdx"
_COLLINFO_ROUTE = "https://index.commoncrawl.org/collinfo.json"
_CDX_ROUTE = "https://index.commoncrawl.org/CC-MAIN-2026-26-index"

_WAYBACK_BODY = json.dumps(
    [
        ["original", "timestamp", "mimetype", "statuscode"],
        ["https://docs.example.com/guide", "20240101000000", "text/html", "200"],
        ["https://docs.example.com/api", "20240202000000", "text/html", "200"],
    ]
)
_COLLINFO_BODY = json.dumps(
    [
        {"id": "CC-MAIN-2026-26", "name": "June 2026 Index", "cdx-api": _CDX_ROUTE},
        {
            "id": "CC-MAIN-2026-22",
            "name": "May 2026 Index",
            "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-22-index",
        },
    ]
)
_COMMONCRAWL_NDJSON = "\n".join(
    [
        json.dumps(
            {
                "url": "https://docs.example.com/guide",
                "timestamp": "20260601000000",
                "mime": "text/html",
                "status": "200",
            }
        ),
        "not-json {",
        json.dumps(
            {
                "url": "https://docs.example.com/api",
                "timestamp": "20260602000000",
                "mime": "text/html",
                "status": "200",
            }
        ),
    ]
)


class _PrefixFakeHttpClient:
    """Route by URL-without-query so tests need not reproduce exact query strings."""

    def __init__(self, routes: dict[str, tuple[bytes | str, str]]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    async def get(
        self,
        url: str,
        *,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        self.requested.append(url)
        key = url.split("?", 1)[0]
        if key not in self.routes:
            return HttpResponse(status_code=404, content=b"", content_type="text/plain", headers={}, url=url)
        body, content_type = self.routes[key]
        content = body.encode("utf-8") if isinstance(body, str) else body
        return HttpResponse(status_code=200, content=content, content_type=content_type, headers={}, url=url)

    async def head(
        self,
        url: str,
        *,
        timeout: float = 10.0,
    ) -> HttpResponse:
        return await self.get(url, timeout=timeout)


def _scan(client: _PrefixFakeHttpClient, sources: list[str], **kwargs: int) -> list:
    return asyncio.run(
        records_from_site_scan(
            "https://docs.example.com",
            client=client,
            sources=sources,
            expected_domains=["docs.example.com"],
            **kwargs,
        )
    )


def test_wayback_cdx_scan_emits_snapshot_records() -> None:
    client = _PrefixFakeHttpClient({_WAYBACK_ROUTE: (_WAYBACK_BODY, "application/json")})

    records = _scan(client, ["wayback"])

    assert [record.url for record in records] == [
        "https://docs.example.com/guide",
        "https://docs.example.com/api",
    ]
    first = records[0]
    assert first.source == "local-site-scan:wayback"
    assert first.metadata["discovery_engine"] == "wayback"
    assert first.metadata["index_type"] == "wayback_cdx"
    assert first.metadata["snapshot_timestamp"] == "20240101000000"
    assert (
        first.metadata["snapshot_url"]
        == "https://web.archive.org/web/20240101000000/https://docs.example.com/guide"
    )
    assert "archive_snapshot" in first.metadata["score_reasons"]
    query_url = client.requested[0]
    assert query_url.startswith(f"{_WAYBACK_ROUTE}?")
    assert "url=docs.example.com" in query_url
    assert "output=json" in query_url
    assert "collapse=urlkey" in query_url


def test_wayback_cdx_scan_returns_empty_on_malformed_json() -> None:
    client = _PrefixFakeHttpClient({_WAYBACK_ROUTE: ("not-json [", "application/json")})

    assert _scan(client, ["wayback"]) == []


def test_wayback_cdx_scan_returns_empty_without_header_row() -> None:
    body = json.dumps([["https://docs.example.com/guide", "20240101000000", "text/html", "200"]])
    client = _PrefixFakeHttpClient({_WAYBACK_ROUTE: (body, "application/json")})

    assert _scan(client, ["wayback"]) == []


def test_wayback_cdx_scan_respects_limit() -> None:
    client = _PrefixFakeHttpClient({_WAYBACK_ROUTE: (_WAYBACK_BODY, "application/json")})

    records = _scan(client, ["wayback"], max_results_per_source=1)

    assert [record.url for record in records] == ["https://docs.example.com/guide"]
    assert "limit=1" in client.requested[0]


def test_commoncrawl_scan_uses_newest_collection_and_parses_ndjson() -> None:
    client = _PrefixFakeHttpClient(
        {
            _COLLINFO_ROUTE: (_COLLINFO_BODY, "application/json"),
            _CDX_ROUTE: (_COMMONCRAWL_NDJSON, "application/json"),
        }
    )

    records = _scan(client, ["commoncrawl"])

    assert [record.url for record in records] == [
        "https://docs.example.com/guide",
        "https://docs.example.com/api",
    ]
    first = records[0]
    assert first.source == "local-site-scan:commoncrawl"
    assert first.metadata["discovery_engine"] == "commoncrawl"
    assert first.metadata["index_type"] == "commoncrawl_index"
    assert first.metadata["cdx_api"] == _CDX_ROUTE
    assert first.metadata["snapshot_timestamp"] == "20260601000000"
    assert "archive_snapshot" in first.metadata["score_reasons"]
    assert client.requested[0] == _COLLINFO_ROUTE
    assert client.requested[1].startswith(f"{_CDX_ROUTE}?")
    assert "filter=status%3A200" in client.requested[1]


def test_commoncrawl_scan_returns_empty_when_collinfo_unavailable() -> None:
    client = _PrefixFakeHttpClient({})

    assert _scan(client, ["commoncrawl"]) == []
    assert client.requested == [_COLLINFO_ROUTE]


def test_commoncrawl_scan_returns_empty_when_cdx_endpoint_fails() -> None:
    client = _PrefixFakeHttpClient({_COLLINFO_ROUTE: (_COLLINFO_BODY, "application/json")})

    assert _scan(client, ["commoncrawl"]) == []


def test_archive_sources_are_registered_and_normalized() -> None:
    assert "wayback" in SITE_SCAN_SOURCES
    assert "commoncrawl" in SITE_SCAN_SOURCES
    assert contracts._normalize_site_scan_sources(["wayback", "commoncrawl"]) == {
        "wayback",
        "commoncrawl",
    }
    assert {"wayback", "commoncrawl"} <= contracts._normalize_site_scan_sources(["all"])


def test_discover_scan_cli_accepts_archive_sources() -> None:
    parser = create_discovery_parser()

    args = parser.parse_args(
        ["scan", "https://docs.example.com", "--source", "wayback", "--source", "commoncrawl"]
    )

    assert args.sources == ["wayback", "commoncrawl"]
