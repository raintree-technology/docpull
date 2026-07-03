"""Build v3 packs from Wikimedia/MediaWiki REST page content."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from bs4 import BeautifulSoup, Tag

from ..conversion.markdown import HtmlToMarkdown
from .common import ContextPackError, write_json
from .typed import (
    PrepareLevel,
    TypedPackItem,
    read_https_text,
    simple_summary_markdown,
    typed_http_cache,
    write_typed_pack,
)
from .typed_models import WikiMetadataArtifact

WIKI_WORKFLOW = "wiki-pack"
DEFAULT_WIKI_OUTPUT_DIR = Path("packs/wiki")
DEFAULT_WIKI_SITE = "en.wikipedia.org"
MAX_WIKI_HTML_BYTES = 8_000_000
_WIKI_PATH_RE = re.compile(r"^/wiki/(.+)$")
_REST_PAGE_RE = re.compile(r"^/w/rest\.php/v1/page/([^/]+)(?:/.*)?$")
_SECTION_HEADING_TAGS = {"h2", "h3"}
_REMOVE_SELECTORS = (
    "style",
    "script",
    "noscript",
    "sup.reference",
    "ol.references",
    ".mw-editsection",
    ".navbox",
    ".metadata",
    ".ambox",
    ".hatnote",
    ".reference",
    ".reflist",
    "table.infobox",
    "table.vertical-navbox",
)


def build_wiki_pack(
    sources: list[str],
    *,
    output_dir: Path = DEFAULT_WIKI_OUTPUT_DIR,
    max_items: int = 30,
    chunk_tokens: int = 4000,
    prepare_level: PrepareLevel = "raw",
    cache_dir: Path | None = None,
    cache_ttl_days: int | None = 7,
) -> dict[str, Any]:
    """Build a v3 pack from Wikimedia/MediaWiki REST page records."""
    if not sources:
        raise ContextPackError("wiki-pack requires at least one page source.")
    with typed_http_cache(cache_dir, ttl_days=cache_ttl_days):
        pages = [_fetch_wiki_page(source) for source in sources[:max_items]]

    items: list[TypedPackItem] = []
    page_metadata: list[dict[str, Any]] = []
    for page in pages:
        page_items, metadata = _page_items(page, max_items=max(1, max_items - len(items)))
        page_metadata.append(metadata)
        items.extend(page_items)
        if len(items) >= max_items:
            break

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_payload = {
        "schema_version": 3,
        "workflow": WIKI_WORKFLOW,
        "page_count": len(page_metadata),
        "section_count": sum(len(page.get("sections", [])) for page in page_metadata),
        "pages": page_metadata,
    }
    metadata_path = output_dir / "wiki.metadata.json"
    sections_path = output_dir / "wiki.sections.ndjson"
    write_json(metadata_path, WikiMetadataArtifact.model_validate(metadata_payload).model_dump(mode="json"))
    sections_path.write_text(
        "".join(
            json.dumps(section, ensure_ascii=False, sort_keys=True) + "\n"
            for page in page_metadata
            for section in page.get("sections", [])
        ),
        encoding="utf-8",
    )
    return write_typed_pack(
        workflow=WIKI_WORKFLOW,
        output_format="wiki",
        output_dir=output_dir,
        items=items,
        pack_filename="wiki.pack.json",
        index_filename="wiki.index.json",
        items_filename="wiki.items.ndjson",
        summary_filename="WIKI.md",
        index_payload=metadata_payload,
        summary_markdown=simple_summary_markdown(
            title="Wiki Pack",
            source=", ".join(sources),
            items=items,
        ),
        result_summary={
            "page_count": len(page_metadata),
            "section_count": metadata_payload["section_count"],
        },
        objective="Review Wikimedia/MediaWiki page context for "
        + ", ".join(str(page.get("title") or "wiki page") for page in page_metadata[:3]),
        chunk_tokens=chunk_tokens,
        extra_artifacts={"metadata": metadata_path, "sections": sections_path},
        prepare_level=prepare_level,
    )


async def async_build_wiki_pack(source: list[str], **kwargs: Any) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_wiki_pack, source, **kwargs)


def _fetch_wiki_page(source: str) -> dict[str, Any]:
    site, title = _parse_wiki_source(source)
    api_url = f"https://{site}/w/rest.php/v1/page/{quote(title.replace(' ', '_'), safe='')}/with_html"
    response = read_https_text(
        api_url,
        accept="application/json",
        max_bytes=MAX_WIKI_HTML_BYTES,
        source_contract="mediawiki_rest",
    )
    payload = json.loads(response.text)
    if not isinstance(payload, dict):
        raise ContextPackError(f"MediaWiki REST response was not an object: {api_url}")
    payload["_docpull_source"] = source
    payload["_docpull_site"] = site
    payload["_docpull_api_url"] = response.url
    return payload


def _parse_wiki_source(source: str) -> tuple[str, str]:
    value = source.strip()
    if not value:
        raise ContextPackError("wiki-pack source cannot be empty.")
    if value.startswith(("wiki:", "wikipedia:")):
        _, title = value.split(":", 1)
        if not title.strip():
            raise ContextPackError("wiki-pack wiki:<title> source is missing a title.")
        return DEFAULT_WIKI_SITE, title.strip().replace(" ", "_")
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        site = parsed.netloc.lower()
        if not _is_allowed_wiki_site(site):
            raise ContextPackError("wiki-pack supports Wikimedia/MediaWiki page URLs only.")
        rest_match = _REST_PAGE_RE.match(parsed.path)
        if rest_match:
            return site, unquote(rest_match.group(1))
        wiki_match = _WIKI_PATH_RE.match(parsed.path)
        if wiki_match:
            return site, unquote(wiki_match.group(1))
        raise ContextPackError("wiki-pack URL must be a /wiki/<title> or /w/rest.php/v1/page/<title> URL.")
    raise ContextPackError("wiki-pack source must be wiki:<title> or a Wikimedia/MediaWiki HTTPS URL.")


def _is_allowed_wiki_site(site: str) -> bool:
    return (
        site == "www.mediawiki.org"
        or site == "mediawiki.org"
        or site.endswith(".wikipedia.org")
        or site.endswith(".wikimedia.org")
        or site.endswith(".wiktionary.org")
        or site.endswith(".wikibooks.org")
        or site.endswith(".wikiquote.org")
        or site.endswith(".wikivoyage.org")
        or site.endswith(".wikiversity.org")
        or site.endswith(".wikisource.org")
        or site.endswith(".wikinews.org")
    )


def _page_items(page: dict[str, Any], *, max_items: int) -> tuple[list[TypedPackItem], dict[str, Any]]:
    title = str(page.get("title") or page.get("key") or "Untitled wiki page")
    html_url = str(page.get("html_url") or f"https://{page['_docpull_site']}/wiki/{quote(title)}")
    license_payload = _license_payload(page.get("license"))
    latest_raw = page.get("latest")
    latest: dict[str, Any] = latest_raw if isinstance(latest_raw, dict) else {}
    sections = _extract_sections(str(page.get("html") or ""), title=title, html_url=html_url)
    metadata = {
        "schema_version": 3,
        "source": "mediawiki_rest",
        "site": page["_docpull_site"],
        "title": title,
        "key": page.get("key"),
        "html_url": html_url,
        "api_url": page["_docpull_api_url"],
        "latest_revision_id": latest.get("id"),
        "latest_revision_timestamp": latest.get("timestamp"),
        "license": license_payload,
        "section_count": len(sections),
        "sections": [
            {
                "section_id": section["section_id"],
                "title": section["title"],
                "level": section["level"],
                "url": section["url"],
            }
            for section in sections
        ],
    }
    rights = _wiki_rights(license_payload)
    items = [_metadata_item(metadata, rights=rights)]
    for section in sections[: max(0, max_items - 1)]:
        items.append(_section_item(metadata, section, rights=rights))
    return items, metadata


def _extract_sections(html: str, *, title: str, html_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    for selector in _REMOVE_SELECTORS:
        for node in soup.select(selector):
            node.decompose()
    parsoid_sections = [node for node in soup.select("section[data-mw-section-id]") if isinstance(node, Tag)]
    if parsoid_sections:
        return _extract_parsoid_sections(parsoid_sections, title=title, html_url=html_url)
    body = soup.find("body")
    root = body if isinstance(body, Tag) else soup
    sections: list[dict[str, Any]] = []
    lead_nodes: list[Any] = []
    current_heading: Tag | None = None
    current_nodes: list[Any] = []

    for child in list(root.children):
        if isinstance(child, Tag) and child.name in _SECTION_HEADING_TAGS:
            if current_heading is None:
                lead_nodes = current_nodes
            else:
                sections.append(_section_from_nodes(current_heading, current_nodes, html_url=html_url))
            current_heading = child
            current_nodes = []
            continue
        current_nodes.append(child)

    if current_heading is None:
        lead_nodes = current_nodes
    else:
        sections.append(_section_from_nodes(current_heading, current_nodes, html_url=html_url))

    lead_markdown = _nodes_to_markdown(lead_nodes, html_url)
    output: list[dict[str, Any]] = []
    if lead_markdown:
        output.append(
            {
                "section_id": "lead",
                "title": "Lead",
                "level": 1,
                "url": html_url,
                "markdown": f"# {title}\n\n{lead_markdown}",
            }
        )
    output.extend(section for section in sections if section.get("markdown"))
    return output


def _extract_parsoid_sections(sections: list[Tag], *, title: str, html_url: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for index, section in enumerate(sections):
        heading = section.find(_SECTION_HEADING_TAGS)
        section_id = str(section.get("data-mw-section-id") or index)
        if heading is None:
            section_title = "Lead" if index == 0 else f"Section {section_id}"
            level = 1 if index == 0 else 2
            body_html = str(section)
        else:
            section_title = heading.get_text(" ", strip=True) or f"Section {section_id}"
            level = int(str(heading.name or "h2").removeprefix("h") or "2")
            heading.extract()
            body_html = str(section)
        markdown_body = _nodes_to_markdown([BeautifulSoup(body_html, "html.parser")], html_url)
        if not markdown_body:
            continue
        url = html_url if index == 0 else f"{html_url}#{quote(_slug(section_title), safe='')}"
        hashes = "#" * min(max(level, 1), 6)
        markdown = (
            f"# {title}\n\n{markdown_body}" if index == 0 else f"{hashes} {section_title}\n\n{markdown_body}"
        )
        output.append(
            {
                "section_id": "lead" if index == 0 else section_id,
                "title": section_title,
                "level": level,
                "url": url,
                "markdown": markdown.strip(),
            }
        )
    return output


def _section_from_nodes(heading: Tag, nodes: list[Any], *, html_url: str) -> dict[str, Any]:
    section_title = heading.get_text(" ", strip=True) or "Section"
    section_id = str(heading.get("id") or _slug(section_title))
    level = int(str(heading.name or "h2").removeprefix("h") or "2")
    body = _nodes_to_markdown(nodes, html_url)
    hashes = "#" * min(max(level, 1), 6)
    return {
        "section_id": section_id,
        "title": section_title,
        "level": level,
        "url": f"{html_url}#{quote(section_id, safe='')}",
        "markdown": f"{hashes} {section_title}\n\n{body}".strip(),
    }


def _nodes_to_markdown(nodes: list[Any], base_url: str) -> str:
    html = "".join(str(node) for node in nodes).strip()
    if not html:
        return ""
    markdown = HtmlToMarkdown().convert(f"<main>{html}</main>", base_url)
    return _clean_wiki_markdown(markdown)


def _clean_wiki_markdown(markdown: str) -> str:
    lines: list[str] = []
    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1]:
                lines.append("")
            continue
        if stripped in {"[edit]", "edit"}:
            continue
        if stripped.startswith("^ "):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _metadata_item(metadata: dict[str, Any], *, rights: dict[str, Any]) -> TypedPackItem:
    license_name = (metadata.get("license") or {}).get("title") or "unknown"
    markdown = "\n".join(
        [
            f"# Wiki page: {metadata['title']}",
            "",
            f"- URL: {metadata['html_url']}",
            f"- Site: `{metadata['site']}`",
            f"- Latest revision: `{metadata.get('latest_revision_id') or 'unknown'}`",
            f"- License: {license_name}",
            f"- Sections: {metadata['section_count']}",
        ]
    )
    return TypedPackItem(
        title=f"Wiki metadata: {metadata['title']}",
        url=metadata["html_url"],
        markdown=markdown,
        source_type="wiki_page",
        item_kind="metadata",
        metadata=metadata,
        route={
            "source_kind": "mediawiki_rest",
            "source_url": metadata["api_url"],
            "html_url": metadata["html_url"],
        },
        rights=rights,
        public={"site": metadata["site"], "section_count": metadata["section_count"]},
    )


def _section_item(
    metadata: dict[str, Any],
    section: dict[str, Any],
    *,
    rights: dict[str, Any],
) -> TypedPackItem:
    return TypedPackItem(
        title=f"{metadata['title']} - {section['title']}",
        url=section["url"],
        markdown=str(section["markdown"]),
        source_type="wiki_section",
        item_kind="section",
        metadata={
            "site": metadata["site"],
            "page_title": metadata["title"],
            "section_id": section["section_id"],
            "section_title": section["title"],
            "level": section["level"],
            "latest_revision_id": metadata.get("latest_revision_id"),
            "license": metadata.get("license"),
        },
        route={
            "source_kind": "mediawiki_rest",
            "source_url": metadata["api_url"],
            "html_url": metadata["html_url"],
            "section_id": section["section_id"],
        },
        rights=rights,
        public={
            "site": metadata["site"],
            "page_title": metadata["title"],
            "section_id": section["section_id"],
        },
    )


def _license_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items() if item}
    if isinstance(value, str) and value.strip():
        return {"title": value.strip()}
    return {}


def _wiki_rights(license_payload: dict[str, Any]) -> dict[str, Any]:
    license_name = str(license_payload.get("title") or license_payload.get("code") or "").lower()
    allowed = (
        "allowed_with_conditions"
        if "creative commons" in license_name or "cc by" in license_name
        else "unknown"
    )
    return {
        "status": "permissioned" if allowed == "allowed_with_conditions" else "unknown",
        "license": license_payload,
        "allowed_use": {
            "internal_indexing": "allowed",
            "redistribution": allowed,
            "eval_generation": allowed,
            "model_training": "unknown",
        },
        "obligations": ["provide attribution and preserve license terms"]
        if allowed == "allowed_with_conditions"
        else [],
        "basis": "mediawiki_rest_license_metadata"
        if allowed == "allowed_with_conditions"
        else "license_unknown",
    }


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return slug or "section"


__all__ = ["DEFAULT_WIKI_OUTPUT_DIR", "async_build_wiki_pack", "build_wiki_pack"]
