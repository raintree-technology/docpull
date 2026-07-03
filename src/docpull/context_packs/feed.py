"""Build local v3 packs from RSS, Atom, or JSON Feed sources."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from defusedxml import ElementTree

from ..conversion.chunking import TokenCounter, chunk_markdown
from ..http.client import AsyncHttpClient
from ..http.protocols import HttpResponse
from ..http.rate_limiter import PerHostRateLimiter
from ..models.document import DocumentRecord
from ..output_contract import default_rights_state, validate_pack_contract
from ..pipeline.manifest import CorpusManifest
from ..security.download_policy import SafeDownloadPolicy, UnsafeDownloadError
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator
from ..time_utils import utc_now, utc_now_iso
from .common import ContextPackError, artifact_ref, write_json

FEED_WORKFLOW = "feed-pack"
DEFAULT_FEED_OUTPUT_DIR = Path("packs/feed")
MAX_FEED_BYTES = 5_000_000


@dataclass(frozen=True)
class FeedItem:
    """One normalized item from a source-provided feed."""

    title: str
    url: str
    item_id: str
    summary: str = ""
    content: str = ""
    published_at: str | None = None
    updated_at: str | None = None
    authors: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FeedPayload:
    """Normalized feed-level metadata and items."""

    title: str
    source_url: str
    source_kind: str
    feed_format: str
    home_url: str | None
    description: str
    updated_at: str | None
    items: list[FeedItem]


@dataclass(frozen=True)
class FeedSource:
    """Fetched or local source text plus acquisition metadata."""

    text: str
    source_url: str
    source_kind: str
    content_type: str
    discovered_from: str | None = None


class _FeedDownloadPolicy(SafeDownloadPolicy):
    """Allow mislabeled text feeds while keeping attachment/body sniffing guards."""

    def validate_response_headers(
        self,
        url: str,
        *,
        status_code: int,
        headers: dict[str, str],
        content_type: str | None,
    ) -> None:
        try:
            super().validate_response_headers(
                url,
                status_code=status_code,
                headers=headers,
                content_type=content_type,
            )
        except UnsafeDownloadError as err:
            if "Disallowed content type 'application/octet-stream'" in str(err):
                return
            raise


def build_feed_pack(
    source: str | Path,
    *,
    output_dir: Path = DEFAULT_FEED_OUTPUT_DIR,
    max_items: int = 50,
    chunk_tokens: int = 4000,
) -> dict[str, Any]:
    """Turn an RSS, Atom, or JSON Feed source into a v3 raw context pack."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source_payload = _read_source(source)
    feed = _parse_feed(
        source_payload.text,
        source_url=source_payload.source_url,
        source_kind=source_payload.source_kind,
    )
    items = feed.items[:max_items]
    if not items:
        raise ContextPackError("Feed contained no readable items.")

    source_hash = hashlib.sha256(source_payload.text.encode("utf-8")).hexdigest()
    documents_path = output_dir / "documents.ndjson"
    feed_items_path = output_dir / "feed.items.ndjson"
    listing_items_path = output_dir / "listing.items.ndjson"
    index_path = output_dir / "feed.index.json"
    freshness_path = output_dir / "freshness.report.json"
    markdown_path = output_dir / "FEED.md"
    sources_dir = output_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    manifest = CorpusManifest(output_dir, output_format="feed")
    counter = TokenCounter()
    records: list[DocumentRecord] = []
    item_entries: list[dict[str, Any]] = []

    with documents_path.open("w", encoding="utf-8") as ndjson:
        for index, item in enumerate(items, start=1):
            item_markdown = _item_markdown(item, feed=feed)
            source_path = sources_dir / f"{index:03d}-{_slugify(item.title)}.md"
            source_path.write_text(item_markdown, encoding="utf-8")
            chunks = chunk_markdown(item_markdown, max_tokens=chunk_tokens, counter=counter)
            if not chunks:
                chunks = chunk_markdown(
                    f"# {item.title}\n\n{item_markdown}",
                    max_tokens=chunk_tokens,
                    counter=counter,
                )
            for chunk in chunks:
                record = DocumentRecord.from_page(
                    url=item.url,
                    title=item.title,
                    content=chunk.text,
                    metadata={
                        "feed_title": feed.title,
                        "feed_url": source_payload.source_url,
                        "feed_home_url": feed.home_url,
                        "feed_description": feed.description,
                        "feed_item_id": item.item_id,
                        "feed_item_index": index,
                        "discovered_from": source_payload.discovered_from,
                        "published_at": item.published_at,
                        "updated_at": item.updated_at,
                        "authors": item.authors,
                        "categories": item.categories,
                        "source_document_hash": source_hash,
                        "source_path": artifact_ref(output_dir, source_path),
                    },
                    extraction={
                        "workflow": FEED_WORKFLOW,
                        "parsed_at": utc_now_iso(),
                        "feed_format": feed.feed_format,
                        "source_content_type": source_payload.content_type,
                    },
                    source_type="feed_item",
                    content_type="text/markdown",
                    mime_type="text/markdown",
                    route={
                        "name": "local-feed-parse",
                        "output_format": "feed",
                        "source_kind": source_payload.source_kind,
                        "source_url": source_payload.source_url,
                        "discovered_from": source_payload.discovered_from,
                        "feed_format": feed.feed_format,
                    },
                    rights=default_rights_state(),
                    chunk_index=chunk.index if len(chunks) > 1 else None,
                    chunk_heading=chunk.heading if len(chunks) > 1 else None,
                    token_count=chunk.token_count,
                )
                records.append(record)
                manifest.add_record(record, source_path)
                ndjson.write(
                    json.dumps(record.model_dump(mode="json", exclude_none=True), ensure_ascii=False)
                )
                ndjson.write("\n")
            item_entries.append(
                _public_item(
                    item,
                    index=index,
                    feed=feed,
                    source_path=source_path,
                    output_dir=output_dir,
                )
            )

    manifest_path = manifest.finalize()
    _write_ndjson(feed_items_path, item_entries)
    _write_ndjson(listing_items_path, [_listing_item(entry) for entry in item_entries])
    index_payload = _index_payload(feed, item_entries=item_entries, record_count=len(records))
    freshness_payload = _freshness_payload(feed, item_entries=item_entries)
    write_json(index_path, index_payload)
    write_json(freshness_path, freshness_payload)
    markdown_path.write_text(_summary_markdown(index_payload, freshness_payload), encoding="utf-8")
    validation = validate_pack_contract(output_dir, level="raw")
    result = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "workflow": FEED_WORKFLOW,
        "status": "completed" if validation["status"] == "pass" else "completed_with_validation_errors",
        "output_dir": str(output_dir),
        "source": source_payload.source_url,
        "source_kind": source_payload.source_kind,
        "discovered_from": source_payload.discovered_from,
        "summary": {
            "feed_format": feed.feed_format,
            "item_count": len(item_entries),
            "record_count": len(records),
            "dated_item_count": freshness_payload["summary"]["dated_item_count"],
            "newest_published_at": freshness_payload["summary"]["newest_published_at"],
            "oldest_published_at": freshness_payload["summary"]["oldest_published_at"],
        },
        "artifacts": {
            "documents_ndjson": artifact_ref(output_dir, documents_path),
            "corpus_manifest": artifact_ref(output_dir, manifest_path),
            "sources": "sources.md",
            "acquisition_routes": "acquisition.routes.json",
            "feed_index": artifact_ref(output_dir, index_path),
            "feed_items": artifact_ref(output_dir, feed_items_path),
            "listing_items": artifact_ref(output_dir, listing_items_path),
            "freshness_report": artifact_ref(output_dir, freshness_path),
            "markdown": artifact_ref(output_dir, markdown_path),
        },
        "validation": validation,
    }
    write_json(output_dir / "feed.pack.json", result)
    return result


def _read_source(source: str | Path) -> FeedSource:
    value = str(source)
    if value.startswith(("http://", "https://")):
        return _read_remote_source(value)
    path = Path(source).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ContextPackError(f"Feed source file does not exist: {path}")
    data = path.read_bytes()
    if len(data) > MAX_FEED_BYTES:
        raise ContextPackError(f"Feed source exceeds {MAX_FEED_BYTES} bytes: {path}")
    return FeedSource(
        text=data.decode("utf-8", errors="replace"),
        source_url=path.as_uri(),
        source_kind="file",
        content_type="application/octet-stream",
    )


def _read_remote_source(url: str) -> FeedSource:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read_remote_source_async(url))
    raise ContextPackError("Remote feed-pack sources cannot be fetched while an event loop is running.")


async def _read_remote_source_async(url: str) -> FeedSource:
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(url)
    if not validation.is_valid:
        raise ContextPackError(f"Remote feed source rejected: {validation.rejection_reason}")
    rate_limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    async with AsyncHttpClient(
        rate_limiter=rate_limiter,
        url_validator=validator,
        default_timeout=30.0,
        max_content_size=MAX_FEED_BYTES,
        download_policy=_FeedDownloadPolicy(),
    ) as client:
        robots = RobotsChecker(user_agent=client.user_agent, url_validator=validator)
        response = await _fetch_allowed_feed_candidate(client, robots, url)
        if response is None:
            raise ContextPackError(f"Robots.txt disallows or could not verify remote feed source: {url}")

        body = _decode_response(response.content, response.content_type)
        if response.status_code < 400 and _looks_like_feed_response(
            body,
            response.content_type,
            response.url,
        ):
            return FeedSource(
                text=body,
                source_url=response.url,
                source_kind="remote",
                content_type=response.content_type,
            )

        feed_links = _feed_links_from_html(body, response.url)
        feed_links.extend(_common_feed_urls(response.url))
        blocked_count = 0
        tried_count = 0
        for feed_url in _dedupe_urls(feed_links):
            candidate_validation = validator.validate(feed_url)
            if not candidate_validation.is_valid:
                continue
            tried_count += 1
            candidate = await _fetch_allowed_feed_candidate(client, robots, feed_url)
            if candidate is None:
                blocked_count += 1
                continue
            candidate_body = _decode_response(candidate.content, candidate.content_type)
            if candidate.status_code < 400 and _looks_like_feed_response(
                candidate_body,
                candidate.content_type,
                candidate.url,
            ):
                return FeedSource(
                    text=candidate_body,
                    source_url=candidate.url,
                    source_kind="remote",
                    content_type=candidate.content_type,
                    discovered_from=response.url,
                )
    if response.status_code >= 400:
        raise ContextPackError(f"Could not fetch feed source {url}: HTTP {response.status_code}")
    details = f" Tried {tried_count} candidate feed URLs."
    if blocked_count:
        details += f" {blocked_count} were blocked by robots.txt."
    raise ContextPackError(
        "Remote source was not a readable RSS, Atom, or JSON Feed and no advertised feed was readable."
        + details
    )


async def _fetch_allowed_feed_candidate(
    client: AsyncHttpClient,
    robots: RobotsChecker,
    url: str,
) -> HttpResponse | None:
    if not robots.is_allowed(url):
        return None
    return await client.get(
        url,
        headers={
            "Accept": (
                "application/feed+json, application/rss+xml, application/atom+xml, "
                "application/xml;q=0.9, text/xml;q=0.8, application/json;q=0.7, text/html;q=0.6"
            )
        },
    )


def _looks_like_feed_response(text: str, content_type: str, url: str) -> bool:
    base_content_type = content_type.split(";", 1)[0].strip().lower()
    feed_content_types = {
        "application/feed+json",
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
        "application/json",
    }
    path = urlparse(url).path.lower()
    stripped = text.lstrip()[:500].lower()
    if base_content_type in feed_content_types or path.endswith((".rss", ".atom", ".xml", ".json")):
        return stripped.startswith("{") or bool(re.search(r"<(?:rss|feed|rdf:rdf|rdf)\b", stripped))
    return stripped.startswith("{") or bool(re.search(r"<(?:rss|feed|rdf:rdf|rdf)\b", stripped))


def _feed_links_from_html(text: str, base_url: str) -> list[str]:
    if "<" not in text or ">" not in text:
        return []
    try:
        soup = BeautifulSoup(text, "html.parser")
    except Exception:
        return []
    feed_types = {
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
        "application/xml",
        "text/xml",
    }
    urls: list[str] = []
    for link in soup.find_all("link", href=True):
        rel = link.get("rel", [])
        rel_values = {str(item).lower() for item in (rel if isinstance(rel, list) else [rel])}
        type_value = str(link.get("type") or "").lower().split(";", 1)[0].strip()
        if "alternate" not in rel_values and type_value not in feed_types:
            continue
        if type_value and type_value not in feed_types:
            continue
        resolved = urljoin(base_url, str(link["href"]))
        if _is_http_url(resolved):
            urls.append(resolved)
    return urls


def _common_feed_urls(url: str) -> list[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return []
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return [
        f"{origin}/feed.xml",
        f"{origin}/rss.xml",
        f"{origin}/atom.xml",
        f"{origin}/feed",
        f"{origin}/blog/feed.xml",
        f"{origin}/blog/rss.xml",
    ]


def _dedupe_urls(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path or '/'}"
        if parsed.query:
            normalized += f"?{parsed.query}"
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _decode_response(body: bytes, content_type: str) -> str:
    encoding = "utf-8"
    for part in content_type.split(";"):
        stripped = part.strip()
        if stripped.lower().startswith("charset="):
            encoding = stripped.split("=", 1)[1].strip().strip("\"'") or encoding
            break
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _parse_feed(text: str, *, source_url: str, source_kind: str) -> FeedPayload:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _parse_json_feed(text, source_url=source_url, source_kind=source_kind)
    try:
        root = ElementTree.fromstring(text.encode("utf-8"))
    except ElementTree.ParseError as err:
        raise ContextPackError(f"Feed source is not valid XML or JSON Feed: {err}") from err
    root_name = _local_name(root.tag)
    if root_name == "rss":
        return _parse_rss(root, source_url=source_url, source_kind=source_kind)
    if root_name == "feed":
        return _parse_atom(root, source_url=source_url, source_kind=source_kind)
    if root_name == "RDF":
        return _parse_rss(root, source_url=source_url, source_kind=source_kind)
    raise ContextPackError(f"Unsupported feed root element: {root_name}")


def _parse_json_feed(text: str, *, source_url: str, source_kind: str) -> FeedPayload:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ContextPackError(f"Invalid JSON Feed: {err}") from err
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ContextPackError("JSON Feed source must include an items array.")
    items: list[FeedItem] = []
    for index, raw_item in enumerate(data["items"], start=1):
        if not isinstance(raw_item, dict):
            continue
        raw_url = _first_string(raw_item.get("url"), raw_item.get("external_url"))
        url = urljoin(source_url, raw_url) if raw_url else ""
        item_id = _first_string(raw_item.get("id"), url) or f"{source_url}#item-{index}"
        title = _first_string(raw_item.get("title")) or url or f"Feed item {index}"
        summary = _html_to_text(_first_string(raw_item.get("summary")) or "")
        content = _html_to_text(
            _first_string(raw_item.get("content_text"), raw_item.get("content_html")) or ""
        )
        items.append(
            FeedItem(
                title=title,
                url=url or _item_fallback_url(source_url, item_id, index),
                item_id=item_id,
                summary=summary,
                content=content,
                published_at=_normalize_datetime(_first_string(raw_item.get("date_published"))),
                updated_at=_normalize_datetime(_first_string(raw_item.get("date_modified"))),
                authors=_json_feed_authors(raw_item.get("authors") or raw_item.get("author")),
                categories=_string_list(raw_item.get("tags")),
            )
        )
    return FeedPayload(
        title=_first_string(data.get("title")) or "Feed",
        source_url=source_url,
        source_kind=source_kind,
        feed_format="json-feed",
        home_url=_resolve_optional_url(source_url, _first_string(data.get("home_page_url"))),
        description=_html_to_text(_first_string(data.get("description")) or ""),
        updated_at=None,
        items=items,
    )


def _parse_rss(root: Any, *, source_url: str, source_kind: str) -> FeedPayload:
    channel = _first_child(root, "channel")
    if channel is None:
        channel = root
    feed_title = _child_text(channel, "title") or "Feed"
    raw_home_url = _child_text(channel, "link")
    home_url = urljoin(source_url, raw_home_url) if raw_home_url else None
    items: list[FeedItem] = []
    rss_items = _children(channel, "item")
    if not rss_items and channel is not root:
        rss_items = _children(root, "item")
    for index, item in enumerate(rss_items, start=1):
        raw_link = _child_text(item, "link")
        link = urljoin(source_url, raw_link) if raw_link else ""
        guid = _child_text(item, "guid")
        title = _child_text(item, "title") or link or f"Feed item {index}"
        summary = _html_to_text(_child_text(item, "description") or "")
        content = _html_to_text(_child_text(item, "encoded") or "")
        item_id = guid or link or f"{source_url}#item-{index}"
        items.append(
            FeedItem(
                title=title,
                url=link or _item_fallback_url(source_url, item_id, index),
                item_id=item_id,
                summary=summary,
                content=content,
                published_at=_normalize_datetime(_child_text(item, "pubDate") or _child_text(item, "date")),
                updated_at=_normalize_datetime(_child_text(item, "updated")),
                authors=_rss_authors(item),
                categories=[
                    _clean_text(child.text or "") for child in _children(item, "category") if child.text
                ],
            )
        )
    return FeedPayload(
        title=feed_title,
        source_url=source_url,
        source_kind=source_kind,
        feed_format="rss",
        home_url=home_url,
        description=_html_to_text(_child_text(channel, "description") or ""),
        updated_at=_normalize_datetime(
            _child_text(channel, "lastBuildDate") or _child_text(channel, "pubDate")
        ),
        items=items,
    )


def _parse_atom(root: Any, *, source_url: str, source_kind: str) -> FeedPayload:
    items: list[FeedItem] = []
    for index, entry in enumerate(_children(root, "entry"), start=1):
        raw_link = _atom_link(entry)
        link = urljoin(source_url, raw_link) if raw_link else ""
        item_id = _child_text(entry, "id") or link or f"{source_url}#item-{index}"
        title = _child_text(entry, "title") or link or f"Feed item {index}"
        summary = _html_to_text(_child_text(entry, "summary") or "")
        content = _html_to_text(_child_text(entry, "content") or "")
        items.append(
            FeedItem(
                title=title,
                url=link or _item_fallback_url(source_url, item_id, index),
                item_id=item_id,
                summary=summary,
                content=content,
                published_at=_normalize_datetime(_child_text(entry, "published")),
                updated_at=_normalize_datetime(_child_text(entry, "updated")),
                authors=_atom_authors(entry),
                categories=[
                    str(child.attrib.get("term") or child.attrib.get("label") or "").strip()
                    for child in _children(entry, "category")
                    if str(child.attrib.get("term") or child.attrib.get("label") or "").strip()
                ],
            )
        )
    return FeedPayload(
        title=_child_text(root, "title") or "Feed",
        source_url=source_url,
        source_kind=source_kind,
        feed_format="atom",
        home_url=_resolve_optional_url(source_url, _atom_link(root)),
        description=_html_to_text(_child_text(root, "subtitle") or ""),
        updated_at=_normalize_datetime(_child_text(root, "updated")),
        items=items,
    )


def _item_markdown(item: FeedItem, *, feed: FeedPayload) -> str:
    lines = [
        f"# {item.title}",
        "",
        f"_source: {item.url}_",
        "",
        f"- Feed: {feed.title}",
        f"- Feed URL: {feed.source_url}",
    ]
    if item.published_at:
        lines.append(f"- Published: {item.published_at}")
    if item.updated_at:
        lines.append(f"- Updated: {item.updated_at}")
    if item.authors:
        lines.append("- Authors: " + ", ".join(item.authors))
    if item.categories:
        lines.append("- Categories: " + ", ".join(item.categories))
    lines.append("")
    if item.summary:
        lines.extend(["## Summary", "", item.summary, ""])
    if item.content and item.content != item.summary:
        lines.extend(["## Content", "", item.content, ""])
    lines.extend(
        [
            "## Provenance",
            "",
            f"- Feed item id: `{item.item_id}`",
            f"- Feed format: `{feed.feed_format}`",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _public_item(
    item: FeedItem,
    *,
    index: int,
    feed: FeedPayload,
    source_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "item_id": f"feed_item_{index:04d}",
        "item_citation_id": f"I{index}",
        "feed_item_id": item.item_id,
        "title": item.title,
        "url": item.url,
        "published_at": item.published_at,
        "updated_at": item.updated_at,
        "authors": item.authors,
        "categories": item.categories,
        "summary": item.summary[:1000] if item.summary else None,
        "feed_title": feed.title,
        "feed_url": feed.source_url,
        "feed_home_url": feed.home_url,
        "source_path": artifact_ref(output_dir, source_path),
    }


def _listing_item(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "item_id": entry["item_id"],
        "item_citation_id": entry["item_citation_id"],
        "title": entry["title"],
        "url": entry["url"],
        "context": entry.get("summary"),
        "published_at": entry.get("published_at"),
        "updated_at": entry.get("updated_at"),
        "parent_url": entry.get("feed_url"),
        "parent_title": entry.get("feed_title"),
    }


def _index_payload(
    feed: FeedPayload,
    *,
    item_entries: list[dict[str, Any]],
    record_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "generated_at": utc_now_iso(),
        "workflow": FEED_WORKFLOW,
        "source": feed.source_url,
        "source_kind": feed.source_kind,
        "feed_format": feed.feed_format,
        "title": feed.title,
        "home_url": feed.home_url,
        "description": feed.description,
        "updated_at": feed.updated_at,
        "item_count": len(item_entries),
        "record_count": record_count,
        "items": item_entries,
    }


def _freshness_payload(feed: FeedPayload, *, item_entries: list[dict[str, Any]]) -> dict[str, Any]:
    dated: list[datetime] = []
    for item in item_entries:
        parsed = _parse_iso_datetime(item.get("published_at") or item.get("updated_at"))
        if parsed is not None:
            dated.append(parsed)
    newest = max(dated) if dated else None
    oldest = min(dated) if dated else None
    now = utc_now()
    age_days = (now - newest).total_seconds() / 86400 if newest is not None else None
    return {
        "schema_version": 3,
        "generated_at": utc_now_iso(),
        "workflow": FEED_WORKFLOW,
        "source": feed.source_url,
        "summary": {
            "item_count": len(item_entries),
            "dated_item_count": len(dated),
            "undated_item_count": len(item_entries) - len(dated),
            "newest_published_at": newest.isoformat() if newest else None,
            "oldest_published_at": oldest.isoformat() if oldest else None,
            "newest_age_days": round(age_days, 3) if age_days is not None else None,
            "freshness_confidence": "dated" if dated else "undated",
        },
    }


def _summary_markdown(index_payload: dict[str, Any], freshness_payload: dict[str, Any]) -> str:
    summary = freshness_payload["summary"]
    lines = [
        "# Feed Pack",
        "",
        f"Source: {index_payload['source']}",
        f"Feed: {index_payload.get('title') or index_payload['source']}",
        f"Format: `{index_payload['feed_format']}`",
        f"Items: {index_payload['item_count']}",
        f"Dated items: {summary['dated_item_count']}",
        f"Newest published/updated: {summary['newest_published_at'] or 'unknown'}",
        "",
        "## Items",
        "",
    ]
    for item in index_payload["items"]:
        date = item.get("published_at") or item.get("updated_at") or "undated"
        lines.append(f"- [{item['item_citation_id']}] [{item['title']}]({item['url']}) - {date}")
    return "\n".join(lines).rstrip() + "\n"


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(_drop_none(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _children(element: Any, name: str) -> list[Any]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _first_child(element: Any, name: str) -> Any | None:
    for child in list(element):
        if _local_name(child.tag) == name:
            return child
    return None


def _child_text(element: Any, name: str) -> str | None:
    child = _first_child(element, name)
    if child is None:
        return None
    return _clean_text("".join(child.itertext()))


def _atom_link(element: Any) -> str | None:
    alternate: str | None = None
    fallback: str | None = None
    for child in _children(element, "link"):
        href = str(child.attrib.get("href") or "").strip()
        if not href:
            continue
        rel = str(child.attrib.get("rel") or "alternate").strip().lower()
        if rel == "alternate":
            alternate = href
            break
        fallback = fallback or href
    return alternate or fallback


def _atom_authors(element: Any) -> list[str]:
    authors: list[str] = []
    for author in _children(element, "author"):
        name = _child_text(author, "name")
        if name:
            authors.append(name)
    return _dedupe(authors)


def _rss_authors(element: Any) -> list[str]:
    values = [
        _child_text(element, "author"),
        _child_text(element, "creator"),
        _child_text(element, "managingEditor"),
    ]
    return _dedupe([value for value in values if value])


def _json_feed_authors(value: Any) -> list[str]:
    if isinstance(value, dict):
        name = _first_string(value.get("name"), value.get("url"))
        return [name] if name else []
    if isinstance(value, list):
        authors: list[str] = []
        for item in value:
            if isinstance(item, dict):
                name = _first_string(item.get("name"), item.get("url"))
                if name:
                    authors.append(name)
            elif isinstance(item, str) and item.strip():
                authors.append(item.strip())
        return _dedupe(authors)
    return []


def _normalize_datetime(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    parsed: datetime | None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        parsed = _parse_iso_datetime(text)
    if parsed is None:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _html_to_text(value: str) -> str:
    text = html.unescape(value or "")
    if "<" in text and ">" in text:
        soup = BeautifulSoup(text, "html.parser")
        text = soup.get_text("\n")
    return _clean_text(text)


def _clean_text(value: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t\r\f\v]+", " ", value)).strip()


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = _clean_text(value)
        key = clean.lower()
        if clean and key not in seen:
            result.append(clean)
            seen.add(key)
    return result


def _item_fallback_url(source_url: str, item_id: str, index: int) -> str:
    return f"{source_url}#item-{quote(item_id or str(index), safe='')}"


def _resolve_optional_url(base_url: str, value: str | None) -> str | None:
    return urljoin(base_url, value) if value else None


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-").lower()
    return slug[:80].strip("-") or "feed-item"


__all__ = ["DEFAULT_FEED_OUTPUT_DIR", "build_feed_pack"]
