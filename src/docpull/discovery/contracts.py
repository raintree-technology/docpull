"""Provider-neutral discovery records, pack artifacts, and selection policies."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pydantic import BaseModel, Field, field_validator

from ..http.protocols import HttpClient, HttpResponse
from ..policy import PolicyConfig, reject_secret_like_mapping
from ..source_scoring import score_source
from ..time_utils import utc_now_iso
from .filters import normalize_url

DISCOVERY_SCHEMA_VERSION = 1
CANDIDATE_SOURCES_FILENAME = "candidate_sources.ndjson"
SOURCE_POLICY_FILENAME = "source_policy.json"
DISCOVERY_GUIDE_FILENAME = "DISCOVERY.md"
SELECTED_SOURCES_FILENAME = "selected_sources.ndjson"
SELECTED_URLS_FILENAME = "selected_urls.txt"
SITE_SCAN_SOURCES = ("llms", "feeds", "openapi", "sitemaps", "github")
_MAX_SCAN_RESOURCE_BYTES = 5 * 1024 * 1024
_URL_RE = re.compile(r"https?://[^\s<>)\"']+")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


class DiscoveryError(RuntimeError):
    """User-facing discovery contract error."""


class CandidateSourceRecord(BaseModel):
    """One provider-neutral candidate URL before final fetch."""

    schema_version: int = DISCOVERY_SCHEMA_VERSION
    generated_at: str = Field(default_factory=utc_now_iso)
    url: str
    source: str
    title: str | None = None
    snippet: str | None = None
    provider: str = "local"
    score: float | None = None
    rank: int | None = None
    query: str | None = None
    discovered_at: str = Field(default_factory=utc_now_iso)
    raw_ref: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("candidate URL must be absolute http(s)")
        return value

    @field_validator("metadata")
    @classmethod
    def _reject_secret_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        reject_secret_like_mapping(value, "candidate.metadata")
        return value


def normalize_provider_response(
    response_path: Path,
    *,
    provider: str,
    query: str | None = None,
    expected_domains: list[str] | None = None,
) -> list[CandidateSourceRecord]:
    """Normalize a local provider response JSON file into candidate records."""
    data = _read_json_file(response_path)
    effective_query = query or _find_query(data)
    generated_at = utc_now_iso()
    records: list[CandidateSourceRecord] = []
    seen: set[str] = set()

    for index, (item, ref) in enumerate(_extract_candidate_items(data), start=1):
        url = _first_text(item, ("url", "link", "href"))
        if not url:
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)

        title = _first_text(item, ("title", "name", "headline"))
        snippet = _snippet_from_item(item)
        raw_score = _first_number(item, ("score", "confidence", "probability"))
        local_score = score_source(url=url, title=title or "", expected_domains=expected_domains or [])
        normalized_provider_score = _normalize_score(raw_score)
        score = (
            max(normalized_provider_score, float(local_score["score"]))
            if normalized_provider_score is not None
            else float(local_score["score"])
        )
        records.append(
            CandidateSourceRecord(
                generated_at=generated_at,
                url=url,
                source=f"provider-import:{provider}",
                title=title,
                snippet=snippet,
                provider=provider,
                score=score,
                rank=_first_int(item, ("rank", "position")) or index,
                query=effective_query,
                discovered_at=generated_at,
                raw_ref=f"{response_path.name}{ref}",
                metadata={
                    "provider_score": raw_score,
                    "local_score": local_score["score"],
                    "score_grade": local_score["grade"],
                    "score_reasons": local_score["reasons"],
                    "imported_from": response_path.name,
                },
            )
        )
    return records


def records_from_url_file(
    url_file: Path,
    *,
    query: str | None = None,
    expected_domains: list[str] | None = None,
    source: str = "local-url-file",
) -> list[CandidateSourceRecord]:
    """Read a JSON, NDJSON, or newline-delimited URL file as local candidates."""
    items = _read_url_items(url_file)
    generated_at = utc_now_iso()
    records: list[CandidateSourceRecord] = []
    seen: set[str] = set()

    for index, item in enumerate(items, start=1):
        if isinstance(item, str):
            url = item
            title = None
            snippet = None
            raw_score = None
            metadata: dict[str, Any] = {}
        elif isinstance(item, dict):
            url = _first_text(item, ("url", "link", "href")) or ""
            title = _first_text(item, ("title", "name", "headline"))
            snippet = _snippet_from_item(item)
            raw_score = _first_number(item, ("score", "confidence"))
            metadata = {"input": "object"}
        else:
            continue
        if not url:
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)

        local_score = score_source(url=url, title=title or "", expected_domains=expected_domains or [])
        normalized_score = _normalize_score(raw_score)
        score = normalized_score if normalized_score is not None else float(local_score["score"])
        records.append(
            CandidateSourceRecord(
                generated_at=generated_at,
                url=url,
                source=source,
                title=title,
                snippet=snippet,
                provider="local",
                score=score,
                rank=index,
                query=query,
                discovered_at=generated_at,
                raw_ref=f"{url_file.name}#{index}",
                metadata={
                    **metadata,
                    "local_score": local_score["score"],
                    "score_grade": local_score["grade"],
                    "score_reasons": local_score["reasons"],
                    "imported_from": url_file.name,
                },
            )
        )
    return records


def records_from_sitemap_file(
    sitemap_file: Path,
    *,
    base_url: str | None = None,
    query: str | None = None,
    expected_domains: list[str] | None = None,
) -> list[CandidateSourceRecord]:
    """Read a local sitemap XML file as URL-only candidate records."""
    content = sitemap_file.read_bytes()
    page_urls, nested_sitemaps = _parse_sitemap_xml(content)
    generated_at = utc_now_iso()
    records: list[CandidateSourceRecord] = []
    seen: set[str] = set()
    parsed_base = urlparse(base_url) if base_url else None
    base_host = parsed_base.hostname.lower().rstrip(".") if parsed_base and parsed_base.hostname else None

    for index, url in enumerate(page_urls, start=1):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if base_host and (parsed.hostname or "").lower().rstrip(".") != base_host:
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        local_score = score_source(url=url, expected_domains=expected_domains or [])
        records.append(
            CandidateSourceRecord(
                generated_at=generated_at,
                url=url,
                source="local-sitemap",
                provider="local",
                score=float(local_score["score"]),
                rank=index,
                query=query,
                discovered_at=generated_at,
                raw_ref=f"{sitemap_file.name}#/urlset/url[{index}]",
                metadata={
                    "local_score": local_score["score"],
                    "score_grade": local_score["grade"],
                    "score_reasons": local_score["reasons"],
                    "nested_sitemap_count": len(nested_sitemaps),
                    "imported_from": sitemap_file.name,
                },
            )
        )
    return records


async def records_from_site_scan(
    start_url: str,
    *,
    client: HttpClient,
    sources: list[str] | None = None,
    query: str | None = None,
    expected_domains: list[str] | None = None,
    max_results_per_source: int = 50,
    timeout_seconds: float = 20.0,
) -> list[CandidateSourceRecord]:
    """Discover local/open candidate URLs from a site without provider calls.

    This is the Phase 3 free-first producer: it reads machine-published hints
    such as ``llms.txt``, RSS/Atom feeds, OpenAPI specs, sitemaps, and GitHub
    contents trees, then emits normal discovery-pack records.
    """
    parsed = urlparse(start_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise DiscoveryError("scan URL must be an absolute http(s) URL")
    if max_results_per_source < 1:
        raise DiscoveryError("max_results_per_source must be >= 1")

    enabled = _normalize_site_scan_sources(sources)
    generated_at = utc_now_iso()
    records: list[CandidateSourceRecord] = []
    seen: set[str] = set()

    async def add_many(candidates: list[dict[str, Any]], engine: str) -> None:
        for candidate in candidates[:max_results_per_source]:
            _append_site_scan_record(
                records,
                seen,
                candidate,
                engine=engine,
                generated_at=generated_at,
                query=query,
                expected_domains=expected_domains or [],
            )

    if "llms" in enabled:
        await add_many(
            await _scan_llms_txt(start_url, client=client, timeout_seconds=timeout_seconds),
            "llms",
        )
    if "feeds" in enabled:
        await add_many(
            await _scan_feeds(start_url, client=client, timeout_seconds=timeout_seconds),
            "feeds",
        )
    if "openapi" in enabled:
        await add_many(
            await _scan_openapi_refs(start_url, client=client, timeout_seconds=timeout_seconds),
            "openapi",
        )
    if "sitemaps" in enabled:
        await add_many(
            await _scan_live_sitemaps(start_url, client=client, timeout_seconds=timeout_seconds),
            "sitemaps",
        )
    if "github" in enabled:
        await add_many(
            await _scan_github_docs_tree(start_url, client=client, timeout_seconds=timeout_seconds),
            "github",
        )

    return records


def write_discovery_pack(
    output_dir: Path,
    records: list[CandidateSourceRecord],
    *,
    policy: PolicyConfig,
    objective: str | None = None,
    query: str | None = None,
    source: str,
    source_path: Path | None = None,
    max_results: int | None = None,
) -> dict[str, Any]:
    """Write candidate_sources.ndjson, source_policy.json, and DISCOVERY.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = utc_now_iso()
    filtered, skipped = _apply_policy(records, policy)
    if policy.max_pages is not None:
        filtered = filtered[: policy.max_pages]
    if max_results is not None:
        filtered = filtered[:max_results]

    candidate_path = output_dir / CANDIDATE_SOURCES_FILENAME
    _write_ndjson(candidate_path, [record.model_dump(mode="json", exclude_none=True) for record in filtered])

    policy_path = output_dir / SOURCE_POLICY_FILENAME
    policy_payload = policy.to_source_policy_payload(
        generated_at=generated_at,
        source=source,
        url=filtered[0].url if filtered else None,
        metadata={
            "objective": objective,
            "query": query,
            "candidate_count": len(filtered),
            "skipped_count": len(skipped),
            "source_path": source_path.name if source_path else None,
        },
    )
    policy_path.write_text(json.dumps(policy_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    guide_path = output_dir / DISCOVERY_GUIDE_FILENAME
    guide_path.write_text(
        _render_discovery_md(
            records=filtered,
            skipped=skipped,
            objective=objective,
            query=query,
            source=source,
        ),
        encoding="utf-8",
    )

    pack_path = output_dir / "discovery.pack.json"
    pack_payload = {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "generated_at": generated_at,
        "url": filtered[0].url if filtered else None,
        "source": source,
        "workflow": "discovery-pack",
        "objective": objective,
        "query": query,
        "candidate_count": len(filtered),
        "skipped_count": len(skipped),
        "artifacts": {
            "candidate_sources": CANDIDATE_SOURCES_FILENAME,
            "source_policy": SOURCE_POLICY_FILENAME,
            "discovery": DISCOVERY_GUIDE_FILENAME,
        },
    }
    pack_path.write_text(json.dumps(pack_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "generated_at": generated_at,
        "output_dir": str(output_dir),
        "candidate_count": len(filtered),
        "skipped_count": len(skipped),
        "artifacts": {
            "candidate_sources": str(candidate_path),
            "source_policy": str(policy_path),
            "discovery": str(guide_path),
            "pack": str(pack_path),
        },
        "skipped": skipped,
    }


def read_candidate_records(path: Path) -> list[CandidateSourceRecord]:
    """Read candidate records from a discovery pack directory or NDJSON file."""
    candidate_path = path / CANDIDATE_SOURCES_FILENAME if path.is_dir() else path
    if not candidate_path.exists():
        raise DiscoveryError(f"Candidate sources file does not exist: {candidate_path}")
    records: list[CandidateSourceRecord] = []
    for line_number, line in enumerate(candidate_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(CandidateSourceRecord.model_validate(json.loads(line)))
        except Exception as err:  # noqa: BLE001
            raise DiscoveryError(f"Invalid candidate record at line {line_number}: {err}") from err
    return records


def select_candidate_records(
    records: list[CandidateSourceRecord],
    selectors: list[str],
    *,
    manual_file: Path | None = None,
) -> list[CandidateSourceRecord]:
    """Apply top:N, domain:N, score>=X, and manual-file selection policies."""
    selected = sorted(
        records,
        key=lambda item: (
            -(item.score if item.score is not None else -1),
            item.rank if item.rank is not None else 1_000_000,
            item.url,
        ),
    )
    for selector in selectors:
        selected = _apply_selector(selected, selector, manual_file=manual_file)
    if manual_file is not None and (not selectors or "manual-file" not in selectors):
        selected = _select_manual(selected, manual_file)
    return selected


def write_selected_sources(
    output_dir: Path,
    records: list[CandidateSourceRecord],
    *,
    source_pack: Path,
    policy: PolicyConfig | None = None,
) -> dict[str, Any]:
    """Write selected_sources.ndjson, selected_urls.txt, and optional source policy."""
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_path = output_dir / SELECTED_SOURCES_FILENAME
    urls_path = output_dir / SELECTED_URLS_FILENAME
    _write_ndjson(selected_path, [record.model_dump(mode="json", exclude_none=True) for record in records])
    urls_path.write_text("".join(f"{record.url}\n" for record in records), encoding="utf-8")
    artifacts = {
        "selected_sources": str(selected_path),
        "selected_urls": str(urls_path),
    }
    if policy is not None:
        policy_path = output_dir / SOURCE_POLICY_FILENAME
        policy_path.write_text(
            json.dumps(
                policy.to_source_policy_payload(
                    source="discovery-selection",
                    url=records[0].url if records else None,
                    metadata={"source_pack": str(source_pack), "selected_count": len(records)},
                ),
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        artifacts["source_policy"] = str(policy_path)
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_pack": str(source_pack),
        "selected_count": len(records),
        "artifacts": artifacts,
    }


def _normalize_site_scan_sources(sources: list[str] | None) -> set[str]:
    if not sources:
        return set(SITE_SCAN_SOURCES)
    normalized: set[str] = set()
    for source in sources:
        clean = source.strip().lower()
        if clean == "all":
            normalized.update(SITE_SCAN_SOURCES)
            continue
        if clean not in SITE_SCAN_SOURCES:
            allowed = ", ".join((*SITE_SCAN_SOURCES, "all"))
            raise DiscoveryError(f"Unsupported scan source '{source}'. Supported sources: {allowed}")
        normalized.add(clean)
    return normalized


def _append_site_scan_record(
    records: list[CandidateSourceRecord],
    seen: set[str],
    candidate: dict[str, Any],
    *,
    engine: str,
    generated_at: str,
    query: str | None,
    expected_domains: list[str],
) -> None:
    url = str(candidate.get("url") or "").strip()
    if not url:
        return
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return
    key = normalize_url(url)
    if key in seen:
        return
    seen.add(key)

    title = str(candidate["title"]).strip() if candidate.get("title") else None
    snippet = str(candidate["snippet"]).strip()[:1000] if candidate.get("snippet") else None
    local_score = score_source(url=url, title=title or "", expected_domains=expected_domains)
    candidate_metadata = candidate.get("metadata")
    extra_metadata = candidate_metadata if isinstance(candidate_metadata, dict) else {}
    metadata = {
        "discovery_engine": engine,
        "source_url": candidate.get("source_url"),
        "local_score": local_score["score"],
        "score_grade": local_score["grade"],
        "score_reasons": local_score["reasons"],
        **extra_metadata,
    }
    records.append(
        CandidateSourceRecord(
            generated_at=generated_at,
            url=url,
            source=f"local-site-scan:{engine}",
            title=title,
            snippet=snippet,
            provider="local",
            score=float(local_score["score"]),
            rank=int(candidate.get("rank") or len(records) + 1),
            query=query,
            discovered_at=generated_at,
            raw_ref=str(candidate.get("raw_ref") or candidate.get("source_url") or engine),
            metadata=metadata,
        )
    )


async def _fetch_scan_response(
    client: HttpClient,
    url: str,
    *,
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> HttpResponse | None:
    try:
        response = await client.get(url, timeout=timeout_seconds, headers=headers)
    except Exception:
        return None
    if response.status_code != 200:
        return None
    if len(response.content) > _MAX_SCAN_RESOURCE_BYTES:
        return None
    return response


def _decode_scan_text(response: HttpResponse) -> str:
    content_type = response.content_type or ""
    encoding = "utf-8"
    for part in content_type.split(";"):
        clean = part.strip()
        if clean.lower().startswith("charset="):
            encoding = clean.split("=", 1)[1].strip().strip("\"'") or "utf-8"
            break
    return response.content.decode(encoding, errors="replace")


def _site_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _same_origin(left: str, right: str) -> bool:
    left_parsed = urlparse(left)
    right_parsed = urlparse(right)
    return (
        left_parsed.scheme in {"http", "https"}
        and left_parsed.scheme == right_parsed.scheme
        and left_parsed.netloc.lower() == right_parsed.netloc.lower()
    )


def _dedupe_urls(urls: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for url in urls:
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        output.append(url)
    return output


async def _scan_llms_txt(
    start_url: str,
    *,
    client: HttpClient,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    base = _site_origin(start_url)
    candidates: list[dict[str, Any]] = []
    for index_url in (f"{base}/llms.txt", f"{base}/llms-full.txt"):
        response = await _fetch_scan_response(
            client,
            index_url,
            timeout_seconds=timeout_seconds,
            headers={"Accept": "text/plain, text/markdown, */*"},
        )
        if response is None:
            continue
        final_url = response.url or index_url
        candidates.append(
            {
                "url": final_url,
                "title": Path(urlparse(final_url).path).name or "llms.txt",
                "snippet": "Machine-readable LLM source index",
                "source_url": final_url,
                "raw_ref": final_url,
                "metadata": {"index_type": "llms_txt"},
            }
        )
        text = _decode_scan_text(response)
        for rank, (url, title) in enumerate(_extract_text_links(text, final_url), start=1):
            candidates.append(
                {
                    "url": url,
                    "title": title,
                    "source_url": final_url,
                    "rank": rank,
                    "raw_ref": f"{final_url}#{rank}",
                    "metadata": {"index_type": "llms_txt"},
                }
            )
    return candidates


def _extract_text_links(text: str, base_url: str) -> list[tuple[str, str | None]]:
    links: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    for match in _MARKDOWN_LINK_RE.finditer(text):
        title = match.group(1).strip() or None
        href = match.group(2).strip()
        resolved = _resolve_candidate_url(href, base_url)
        if resolved and normalize_url(resolved) not in seen:
            seen.add(normalize_url(resolved))
            links.append((resolved, title))

    for match in _URL_RE.finditer(text):
        href = match.group(0).rstrip(".,;)]}")
        resolved = _resolve_candidate_url(href, base_url)
        if resolved and normalize_url(resolved) not in seen:
            seen.add(normalize_url(resolved))
            links.append((resolved, None))

    return links


def _resolve_candidate_url(href: str, base_url: str) -> str | None:
    if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    resolved = urljoin(base_url, href)
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    if parsed.query:
        clean += f"?{parsed.query}"
    return clean


async def _scan_feeds(
    start_url: str,
    *,
    client: HttpClient,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    base = _site_origin(start_url)
    feed_urls = [
        start_url if start_url.endswith((".xml", ".rss", ".atom")) else "",
        f"{base}/feed.xml",
        f"{base}/rss.xml",
        f"{base}/atom.xml",
        f"{base}/feed",
        f"{base}/blog/feed.xml",
        f"{base}/blog/rss.xml",
    ]
    homepage = await _fetch_scan_response(
        client,
        start_url,
        timeout_seconds=timeout_seconds,
        headers={"Accept": "text/html, application/xhtml+xml, */*"},
    )
    if homepage is not None:
        feed_urls.extend(_extract_feed_links(homepage.content, homepage.url or start_url))

    candidates: list[dict[str, Any]] = []
    for feed_url in _dedupe_urls(url for url in feed_urls if url):
        if not _same_origin(start_url, feed_url):
            continue
        response = await _fetch_scan_response(
            client,
            feed_url,
            timeout_seconds=timeout_seconds,
            headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"},
        )
        if response is None:
            continue
        candidates.extend(_parse_feed_candidates(response))
    return candidates


def _extract_feed_links(content: bytes, base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(content, "html.parser")
    except Exception:
        return []
    urls: list[str] = []
    feed_types = {
        "application/rss+xml",
        "application/atom+xml",
        "application/feed+json",
        "application/xml",
        "text/xml",
    }
    for link in soup.find_all("link", href=True):
        rel = link.get("rel", [])
        rel_values = {str(item).lower() for item in (rel if isinstance(rel, list) else [rel])}
        type_value = str(link.get("type") or "").lower()
        if "alternate" not in rel_values and type_value not in feed_types:
            continue
        if type_value and type_value not in feed_types:
            continue
        resolved = _resolve_candidate_url(str(link["href"]), base_url)
        if resolved:
            urls.append(resolved)
    return urls


def _parse_feed_candidates(response: HttpResponse) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(response.content)
    except (ElementTree.ParseError, DefusedXmlException):
        return []

    source_url = response.url
    candidates: list[dict[str, Any]] = []
    entries = [elem for elem in root.iter() if _xml_local_name(elem.tag) in {"item", "entry"}]
    for rank, entry in enumerate(entries, start=1):
        url = _feed_entry_url(entry, source_url)
        if not url:
            continue
        candidates.append(
            {
                "url": url,
                "title": _child_text(entry, {"title"}),
                "snippet": _child_text(entry, {"description", "summary", "content", "subtitle"}),
                "source_url": source_url,
                "rank": rank,
                "raw_ref": f"{source_url}#{rank}",
                "metadata": {"feed_url": source_url},
            }
        )
    return candidates


def _feed_entry_url(entry: Any, base_url: str) -> str | None:
    link_text = _child_text(entry, {"link"})
    if link_text and not link_text.isspace():
        resolved = _resolve_candidate_url(link_text.strip(), base_url)
        if resolved:
            return resolved
    for child in list(entry):
        if _xml_local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return _resolve_candidate_url(href, base_url)
    return None


async def _scan_openapi_refs(
    start_url: str,
    *,
    client: HttpClient,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    base = _site_origin(start_url)
    spec_urls = [
        start_url if Path(urlparse(start_url).path).name.lower() in {"openapi.json", "swagger.json"} else "",
        f"{base}/openapi.json",
        f"{base}/openapi.yaml",
        f"{base}/openapi.yml",
        f"{base}/swagger.json",
        f"{base}/swagger.yaml",
        f"{base}/api/openapi.json",
        f"{base}/api/docs/openapi.json",
        f"{base}/docs/openapi.json",
        f"{base}/.well-known/openapi.json",
    ]
    homepage = await _fetch_scan_response(
        client,
        start_url,
        timeout_seconds=timeout_seconds,
        headers={"Accept": "text/html, application/xhtml+xml, */*"},
    )
    if homepage is not None:
        spec_urls.extend(_extract_openapi_links(homepage.content, homepage.url or start_url))

    candidates: list[dict[str, Any]] = []
    for spec_url in _dedupe_urls(url for url in spec_urls if url):
        if not _same_origin(start_url, spec_url):
            continue
        response = await _fetch_scan_response(
            client,
            spec_url,
            timeout_seconds=timeout_seconds,
            headers={"Accept": "application/json, application/yaml, text/yaml, application/x-yaml, */*"},
        )
        if response is None:
            continue
        candidates.extend(_parse_openapi_candidates(response))
    return candidates


def _extract_openapi_links(content: bytes, base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(content, "html.parser")
    except Exception:
        return []
    urls: list[str] = []
    for tag in soup.find_all(["a", "link", "script"], href=True):
        href = str(tag.get("href") or "")
        if _looks_like_openapi_ref(href):
            resolved = _resolve_candidate_url(href, base_url)
            if resolved:
                urls.append(resolved)
    for tag in soup.find_all("script", src=True):
        src = str(tag.get("src") or "")
        if _looks_like_openapi_ref(src):
            resolved = _resolve_candidate_url(src, base_url)
            if resolved:
                urls.append(resolved)
    return urls


def _looks_like_openapi_ref(value: str) -> bool:
    lower = value.lower()
    return any(token in lower for token in ("openapi", "swagger")) and lower.endswith(
        (".json", ".yaml", ".yml")
    )


def _parse_openapi_candidates(response: HttpResponse) -> list[dict[str, Any]]:
    data = _parse_structured_spec(_decode_scan_text(response), response.url)
    if not isinstance(data, dict) or not ("openapi" in data or "swagger" in data):
        return []
    raw_info = data.get("info")
    info = raw_info if isinstance(raw_info, dict) else {}
    title = str(info.get("title") or "OpenAPI specification")
    description = str(info.get("description") or "")[:1000] or None
    version = data.get("openapi") or data.get("swagger")
    candidates = [
        {
            "url": response.url,
            "title": title,
            "snippet": description,
            "source_url": response.url,
            "raw_ref": response.url,
            "metadata": {"openapi_version": version, "kind": "openapi_spec"},
        }
    ]
    external_docs = data.get("externalDocs")
    if isinstance(external_docs, dict):
        docs_url = external_docs.get("url")
        if isinstance(docs_url, str):
            resolved = _resolve_candidate_url(docs_url, response.url)
            if resolved:
                candidates.append(
                    {
                        "url": resolved,
                        "title": str(external_docs.get("description") or "External API docs"),
                        "source_url": response.url,
                        "raw_ref": f"{response.url}#/externalDocs",
                        "metadata": {"openapi_version": version, "kind": "openapi_external_docs"},
                    }
                )
    return candidates


def _parse_structured_spec(text: str, url: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if urlparse(url).path.lower().endswith((".yaml", ".yml")):
        try:
            import yaml

            return yaml.safe_load(text)
        except Exception:
            return None
    return None


async def _scan_live_sitemaps(
    start_url: str,
    *,
    client: HttpClient,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    sitemap_urls = await _discover_sitemap_urls(start_url, client=client, timeout_seconds=timeout_seconds)
    candidates: list[dict[str, Any]] = []
    visited: set[str] = set()

    async def visit(sitemap_url: str, depth: int) -> None:
        if depth > 3 or len(visited) >= 25 or len(candidates) >= 250:
            return
        if not _same_origin(start_url, sitemap_url):
            return
        key = normalize_url(sitemap_url)
        if key in visited:
            return
        visited.add(key)
        response = await _fetch_scan_response(
            client,
            sitemap_url,
            timeout_seconds=timeout_seconds,
            headers={"Accept": "application/xml, text/xml, */*"},
        )
        if response is None:
            return
        try:
            page_urls, nested_urls = _parse_sitemap_xml(response.content)
        except DiscoveryError:
            return
        for index, page_url in enumerate(page_urls, start=1):
            if not _same_origin(start_url, page_url):
                continue
            candidates.append(
                {
                    "url": page_url,
                    "source_url": response.url,
                    "rank": len(candidates) + 1,
                    "raw_ref": f"{response.url}#/url[{index}]",
                    "metadata": {"sitemap_url": response.url},
                }
            )
            if len(candidates) >= 250:
                return
        for nested_url in nested_urls:
            await visit(nested_url, depth + 1)

    for sitemap_url in sitemap_urls:
        await visit(sitemap_url, 0)
    return candidates


async def _discover_sitemap_urls(
    start_url: str,
    *,
    client: HttpClient,
    timeout_seconds: float,
) -> list[str]:
    base = _site_origin(start_url)
    urls = _sitemap_url_guesses(start_url)
    robots = await _fetch_scan_response(
        client,
        f"{base}/robots.txt",
        timeout_seconds=timeout_seconds,
        headers={"Accept": "text/plain, */*"},
    )
    if robots is not None:
        for line in _decode_scan_text(robots).splitlines():
            if line.lower().startswith("sitemap:"):
                value = line.split(":", 1)[1].strip()
                resolved = _resolve_candidate_url(value, robots.url or f"{base}/robots.txt")
                if resolved:
                    urls.insert(0, resolved)
    return _dedupe_urls(urls)


def _sitemap_url_guesses(start_url: str) -> list[str]:
    base = _site_origin(start_url)
    parsed = urlparse(start_url)
    path_prefixes = [""]
    first_segment = parsed.path.strip("/").split("/", 1)[0] if parsed.path.strip("/") else ""
    if first_segment:
        path_prefixes.append(f"/{first_segment}")

    names = (
        "sitemap.xml",
        "sitemap_index.xml",
        "sitemap-index.xml",
        "sitemap/sitemap.xml",
        "sitemaps/sitemap.xml",
        "sitemap1.xml",
        "wp-sitemap.xml",
        "page-sitemap.xml",
        "post-sitemap.xml",
        "blog-sitemap.xml",
        "docs-sitemap.xml",
        "sitemap-pages.xml",
    )
    return [f"{base}{prefix}/{name}" for prefix in path_prefixes for name in names]


async def _scan_github_docs_tree(
    start_url: str,
    *,
    client: HttpClient,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    repo = _github_repo(start_url)
    if repo is None:
        return []
    owner, name = repo
    queue: list[tuple[str, int]] = [("", 0), ("docs", 0), ("doc", 0), ("website/docs", 0)]
    visited: set[str] = set()
    candidates: list[dict[str, Any]] = []

    while queue and len(visited) < 30 and len(candidates) < 200:
        path, depth = queue.pop(0)
        if path in visited or depth > 3:
            continue
        visited.add(path)
        api_url = _github_contents_api_url(owner, name, path)
        response = await _fetch_scan_response(
            client,
            api_url,
            timeout_seconds=timeout_seconds,
            headers={"Accept": "application/vnd.github+json"},
        )
        if response is None:
            continue
        try:
            data = json.loads(_decode_scan_text(response))
        except json.JSONDecodeError:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            item_path = str(item.get("path") or "")
            if item_type == "dir" and _github_should_descend(item_path, path) and depth < 3:
                queue.append((item_path, depth + 1))
                continue
            if item_type != "file" or not _github_doc_file(item_path, root=path == ""):
                continue
            url = item.get("download_url") or item.get("html_url")
            if not isinstance(url, str) or not url:
                continue
            candidates.append(
                {
                    "url": url,
                    "title": item_path,
                    "source_url": item.get("html_url") or api_url,
                    "rank": len(candidates) + 1,
                    "raw_ref": f"{api_url}#{item_path}",
                    "metadata": {
                        "github_owner": owner,
                        "github_repo": name,
                        "github_path": item_path,
                        "html_url": item.get("html_url"),
                    },
                }
            )
    return candidates


def _github_repo(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if parsed.hostname not in {"github.com", "www.github.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2:
        return None
    return parts[0], parts[1]


def _github_contents_api_url(owner: str, repo: str, path: str) -> str:
    encoded_path = quote(path.strip("/"))
    suffix = f"/{encoded_path}" if encoded_path else ""
    return f"https://api.github.com/repos/{owner}/{repo}/contents{suffix}?ref=HEAD"


def _github_should_descend(item_path: str, current_path: str) -> bool:
    clean = item_path.strip("/").lower()
    if current_path:
        return True
    return clean in {"docs", "doc", "documentation", "website", "site", ".github"}


def _github_doc_file(path: str, *, root: bool) -> bool:
    clean = path.strip("/").lower()
    name = clean.rsplit("/", 1)[-1]
    if not name.endswith((".md", ".mdx", ".rst", ".txt")):
        return False
    if root:
        return name.startswith(("readme", "contributing", "security", "changelog"))
    return True


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(element: Any, names: set[str]) -> str | None:
    for child in list(element):
        text = getattr(child, "text", None)
        if _xml_local_name(child.tag) in names and isinstance(text, str) and text.strip():
            return text.strip()[:1000]
    return None


def _read_json_file(path: Path) -> Any:
    if not path.exists():
        raise DiscoveryError(f"Input file does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise DiscoveryError(f"Input file is not valid JSON: {path}: {err}") from err


def _find_query(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("query", "queries", "objective"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list) and value and isinstance(value[0], str):
                return value[0]
        request = data.get("request") or data.get("request_options")
        if isinstance(request, dict):
            return _find_query(request)
    return None


def _extract_candidate_items(data: Any) -> Iterator[tuple[dict[str, Any], str]]:
    if isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, dict):
                yield item, f"#[{index}]"
        return
    if not isinstance(data, dict):
        return

    candidates = _first_list_with_urls(
        data,
        (
            ("search", "results"),
            ("results",),
            ("search_results",),
            ("sources",),
            ("data", "results"),
            ("items",),
            ("candidates",),
        ),
    )
    if candidates is not None:
        path, items = candidates
        for index, item in enumerate(items):
            if isinstance(item, dict):
                yield item, f"#{path}[{index}]"
        return

    for path, item in _walk_url_dicts(data):
        yield item, f"#{path}"


def _first_list_with_urls(
    data: dict[str, Any],
    paths: Iterable[tuple[str, ...]],
) -> tuple[str, list[Any]] | None:
    for path in paths:
        current: Any = data
        ok = True
        for key in path:
            if not isinstance(current, dict) or key not in current:
                ok = False
                break
            current = current[key]
        if (
            ok
            and isinstance(current, list)
            and any(isinstance(item, dict) and _first_text(item, ("url", "link", "href")) for item in current)
        ):
            return "/" + "/".join(path), current
    return None


def _walk_url_dicts(data: Any, path: str = "") -> Iterator[tuple[str, dict[str, Any]]]:
    skip_keys = {"errors", "warnings", "usage", "request_options", "metadata", "task", "auth"}
    if isinstance(data, dict):
        if _first_text(data, ("url", "link", "href")):
            yield path or "/", data
            return
        for key, value in data.items():
            if key in skip_keys:
                continue
            yield from _walk_url_dicts(value, f"{path}/{key}")
    elif isinstance(data, list):
        for index, item in enumerate(data):
            yield from _walk_url_dicts(item, f"{path}[{index}]")


def _first_text(item: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _first_number(item: dict[str, Any], keys: Iterable[str]) -> float | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _first_int(item: dict[str, Any], keys: Iterable[str]) -> int | None:
    number = _first_number(item, keys)
    return int(number) if number is not None else None


def _snippet_from_item(item: dict[str, Any]) -> str | None:
    direct = _first_text(item, ("snippet", "content", "text", "description", "summary", "excerpt"))
    if direct:
        return direct[:1000]
    for key in ("excerpts", "highlights"):
        value = item.get(key)
        if isinstance(value, list):
            parts = [str(part).strip() for part in value if str(part).strip()]
            if parts:
                return " ".join(parts)[:1000]
    return None


def _normalize_score(value: float | None) -> float | None:
    if value is None:
        return None
    score = value * 100 if 0 <= value <= 1 else value
    return max(0.0, min(100.0, score))


def _read_url_items(path: Path) -> list[str | dict[str, Any]]:
    if not path.exists():
        raise DiscoveryError(f"URL file does not exist: {path}")
    text = path.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if path.suffix.lower() == ".json":
        data = json.loads(stripped)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, (str, dict))]
        if isinstance(data, dict):
            found = _first_list_with_urls(data, (("urls",), ("sources",), ("results",), ("items",)))
            if found is not None:
                return [item for item in found[1] if isinstance(item, (str, dict))]
        raise DiscoveryError("JSON URL file must be a list or contain urls/sources/results/items")

    parsed_items: list[str | dict[str, Any]] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#"):
            continue
        if clean.startswith("{"):
            parsed_items.append(json.loads(clean))
        else:
            parsed_items.append(clean)
    return parsed_items


def _parse_sitemap_xml(content: bytes) -> tuple[list[str], list[str]]:
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, DefusedXmlException) as err:
        raise DiscoveryError(f"Could not parse sitemap XML: {err}") from err
    page_urls: list[str] = []
    sitemap_urls: list[str] = []
    for loc_elem in root.findall(
        ".//{http://www.sitemaps.org/schemas/sitemap/0.9}url/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
    ):
        if loc_elem.text:
            page_urls.append(loc_elem.text.strip())
    for loc_elem in root.findall(".//url/loc"):
        if loc_elem.text:
            page_urls.append(loc_elem.text.strip())
    for loc_elem in root.findall(
        ".//{http://www.sitemaps.org/schemas/sitemap/0.9}sitemap/{http://www.sitemaps.org/schemas/sitemap/0.9}loc"
    ):
        if loc_elem.text:
            sitemap_urls.append(loc_elem.text.strip())
    for loc_elem in root.findall(".//sitemap/loc"):
        if loc_elem.text:
            sitemap_urls.append(loc_elem.text.strip())
    return page_urls, sitemap_urls


def _apply_policy(
    records: list[CandidateSourceRecord],
    policy: PolicyConfig,
) -> tuple[list[CandidateSourceRecord], list[dict[str, Any]]]:
    filtered: list[CandidateSourceRecord] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = normalize_url(record.url)
        if key in seen:
            skipped.append({"url": record.url, "reason": "duplicate"})
            continue
        seen.add(key)
        allowed, reason = policy.allows_url(record.url)
        if not allowed:
            skipped.append({"url": record.url, "reason": reason})
            continue
        filtered.append(record)
    return filtered, skipped


def _apply_selector(
    records: list[CandidateSourceRecord],
    selector: str,
    *,
    manual_file: Path | None,
) -> list[CandidateSourceRecord]:
    if selector.startswith("top:"):
        return records[: _selector_int(selector, "top")]
    if selector.startswith("domain:"):
        parts = selector.split(":")
        if len(parts) == 2:
            return _limit_per_domain(records, int(parts[1]))
        if len(parts) == 3:
            return _limit_one_domain(records, parts[1], int(parts[2]))
    if selector.startswith("score>="):
        threshold = float(selector.split(">=", 1)[1])
        return [record for record in records if record.score is not None and record.score >= threshold]
    if selector == "manual-file":
        if manual_file is None:
            raise DiscoveryError("manual-file selector requires --manual-file")
        return _select_manual(records, manual_file)
    raise DiscoveryError(f"Unsupported selection policy: {selector}")


def _selector_int(selector: str, name: str) -> int:
    try:
        value = int(selector.split(":", 1)[1])
    except ValueError as err:
        raise DiscoveryError(f"{name} selector must use {name}:N") from err
    if value < 1:
        raise DiscoveryError(f"{name} selector N must be >= 1")
    return value


def _limit_per_domain(records: list[CandidateSourceRecord], limit: int) -> list[CandidateSourceRecord]:
    if limit < 1:
        raise DiscoveryError("domain selector N must be >= 1")
    counts: dict[str, int] = {}
    output: list[CandidateSourceRecord] = []
    for record in records:
        domain = urlparse(record.url).netloc.lower()
        if counts.get(domain, 0) >= limit:
            continue
        counts[domain] = counts.get(domain, 0) + 1
        output.append(record)
    return output


def _limit_one_domain(
    records: list[CandidateSourceRecord],
    domain: str,
    limit: int,
) -> list[CandidateSourceRecord]:
    if limit < 1:
        raise DiscoveryError("domain selector N must be >= 1")
    output: list[CandidateSourceRecord] = []
    for record in records:
        host = urlparse(record.url).netloc.lower()
        if host == domain.lower() or host.endswith(f".{domain.lower()}"):
            output.append(record)
            if len(output) >= limit:
                break
    return output


def _select_manual(records: list[CandidateSourceRecord], manual_file: Path) -> list[CandidateSourceRecord]:
    manual_items = _read_url_items(manual_file)
    generated_at = utc_now_iso()
    by_url = {normalize_url(record.url): record for record in records}
    selected: list[CandidateSourceRecord] = []
    seen: set[str] = set()
    for index, item in enumerate(manual_items, start=1):
        url = item if isinstance(item, str) else _first_text(item, ("url", "link", "href"))
        if not url:
            continue
        key = normalize_url(url)
        if key in seen:
            continue
        seen.add(key)
        existing = by_url.get(key)
        if existing is not None:
            selected.append(existing)
            continue
        local_score = score_source(url=url)
        selected.append(
            CandidateSourceRecord(
                generated_at=generated_at,
                url=url,
                source="manual-file",
                provider="local",
                score=float(local_score["score"]),
                rank=index,
                discovered_at=generated_at,
                raw_ref=f"{manual_file.name}#{index}",
                metadata={
                    "local_score": local_score["score"],
                    "score_grade": local_score["grade"],
                    "score_reasons": local_score["reasons"],
                    "imported_from": manual_file.name,
                },
            )
        )
    return selected


def _write_ndjson(path: Path, payloads: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for payload in payloads:
            reject_secret_like_mapping(payload, str(path))
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")


def _render_discovery_md(
    *,
    records: list[CandidateSourceRecord],
    skipped: list[dict[str, Any]],
    objective: str | None,
    query: str | None,
    source: str,
) -> str:
    lines = [
        "# Discovery",
        "",
        f"Source: `{source}`",
        f"Objective: {objective or 'not set'}",
        f"Query: {query or 'not set'}",
        f"Candidates: {len(records)}",
        f"Skipped: {len(skipped)}",
        "",
        "## Artifacts",
        "",
        f"- `{CANDIDATE_SOURCES_FILENAME}` - normalized candidate URL records before fetch.",
        f"- `{SOURCE_POLICY_FILENAME}` - non-secret effective policy for this discovery pack.",
        f"- `{DISCOVERY_GUIDE_FILENAME}` - this guide.",
        "",
        "## Suggested Commands",
        "",
        "```bash",
        "docpull discover fetch . --select top:10 -o ../selected-pack",
        "docpull discover fetch . --select 'score>=70' --select domain:3 -o ../selected-pack",
        "```",
        "",
        "## Top Candidates",
        "",
    ]
    for index, record in enumerate(records[:20], start=1):
        title = f" - {record.title}" if record.title else ""
        score = f"{record.score:.1f}" if record.score is not None else "n/a"
        lines.append(f"{index}. {record.url}{title} (score: {score})")
    if not records:
        lines.append("No candidates survived policy filtering.")
    if skipped:
        lines.extend(["", "## Skipped", ""])
        for item in skipped[:20]:
            lines.append(f"- {item.get('url')} ({item.get('reason')})")
    return "\n".join(lines).rstrip() + "\n"
