"""Build v3 packs from standards documents."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup, Tag
from defusedxml import ElementTree

from .common import ContextPackError, write_json
from .typed import (
    PrepareLevel,
    TypedPackItem,
    read_https_text,
    simple_summary_markdown,
    typed_http_cache,
    write_typed_pack,
)
from .typed_models import StandardsMetadataArtifact

STANDARDS_WORKFLOW = "standards-pack"
DEFAULT_STANDARDS_OUTPUT_DIR = Path("packs/standards")


def build_standards_pack(
    sources: list[str],
    *,
    output_dir: Path = DEFAULT_STANDARDS_OUTPUT_DIR,
    max_items: int = 20,
    chunk_tokens: int = 4000,
    prepare_level: PrepareLevel = "raw",
    cache_dir: Path | None = None,
    cache_ttl_days: int | None = 7,
) -> dict[str, Any]:
    """Build a standards context pack from RFC/IETF/W3C/WHATWG sources."""
    if not sources:
        raise ContextPackError("standards-pack requires at least one source.")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    standards: list[dict[str, Any]] = []
    items: list[TypedPackItem] = []
    with typed_http_cache(cache_dir, ttl_days=cache_ttl_days):
        for source in sources:
            if len(items) >= max_items:
                break
            standard = _standard_from_source(source)
            standard["sections"] = _standard_sections(str(standard.get("content") or ""))
            standards.append(_standard_metadata(standard))
            remaining = max_items - len(items)
            items.extend(_items_for_standard(standard)[:remaining])
    metadata_path = output_dir / "standards.metadata.json"
    write_json(
        metadata_path,
        StandardsMetadataArtifact(workflow=STANDARDS_WORKFLOW, standards=standards).model_dump(mode="json"),
    )
    return write_typed_pack(
        workflow=STANDARDS_WORKFLOW,
        output_format="standards",
        output_dir=output_dir,
        items=items,
        pack_filename="standards.pack.json",
        index_filename="standards.index.json",
        items_filename="standards.items.ndjson",
        summary_filename="STANDARDS.md",
        index_payload={"standards": standards},
        summary_markdown=simple_summary_markdown(
            title="Standards Pack",
            source=", ".join(sources),
            items=items,
        ),
        result_summary={"standard_count": len(standards)},
        chunk_tokens=chunk_tokens,
        extra_artifacts={"metadata": metadata_path},
        prepare_level=prepare_level,
    )


async def async_build_standards_pack(
    sources: list[str],
    **kwargs: Any,
) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_standards_pack, sources, **kwargs)


def _standard_from_source(source: str) -> dict[str, Any]:
    value = source.strip()
    if value.startswith("rfc:"):
        return _rfc_standard(value.split(":", 1)[1])
    if value.lower().startswith("rfc") and value[3:].isdigit():
        return _rfc_standard(value[3:])
    if value.startswith("ietf:"):
        return _ietf_standard(value.split(":", 1)[1])
    if value.startswith("w3c:"):
        return _w3c_standard(value.split(":", 1)[1])
    if value.startswith("whatwg:"):
        return _whatwg_standard(value.split(":", 1)[1])
    if value.startswith("https://"):
        if "rfc-editor.org/rfc/rfc" in value:
            match = re.search(r"rfc(\d+)", value, re.IGNORECASE)
            if match:
                return _rfc_standard(match.group(1))
        if "datatracker.ietf.org/doc/" in value:
            return _ietf_standard(value.rstrip("/").rsplit("/", 1)[-1])
        if "w3.org/TR/" in value:
            return _w3c_standard(value.rstrip("/").rsplit("/", 1)[-1])
        return _whatwg_standard(value)
    raise ContextPackError(
        "standards-pack sources must be rfc:<n>, ietf:<draft>, w3c:<shortname>, or whatwg:<url>."
    )


def _rfc_standard(number: str) -> dict[str, Any]:
    digits = re.sub(r"\D", "", number)
    if not digits:
        raise ContextPackError(f"Invalid RFC identifier: {number}")
    canonical_url = f"https://www.rfc-editor.org/rfc/rfc{digits}.txt"
    text = read_https_text(canonical_url, accept="text/plain")
    metadata = _rfc_index_metadata(digits)
    title = metadata.get("title") or _title_from_text(text.text) or f"RFC {digits}"
    return {
        "source": "rfc",
        "identifier": f"RFC{digits}",
        "canonical_url": canonical_url,
        "title": title,
        "status": metadata.get("status"),
        "published_at": metadata.get("published_at"),
        "authors": metadata.get("authors", []),
        "references": _rfc_references(text.text),
        "content": text.text,
    }


def _rfc_index_metadata(number: str) -> dict[str, Any]:
    try:
        index = read_https_text(
            "https://www.rfc-editor.org/rfc-index.xml", accept="application/xml, text/xml"
        )
        root = ElementTree.fromstring(index.text.encode("utf-8"))
    except Exception:
        return {}
    target = f"RFC{number}"
    for entry in root.iter():
        if _local_name(entry.tag) != "rfc-entry":
            continue
        doc_id = _child_text(entry, "doc-id")
        if doc_id != target:
            continue
        authors = []
        for author in [child for child in entry.iter() if _local_name(child.tag) == "author"]:
            name = _child_text(author, "name")
            if name:
                authors.append(name)
        return {
            "title": _child_text(entry, "title"),
            "status": _child_text(entry, "current-status"),
            "published_at": "-".join(
                part for part in (_child_text(entry, "date/year"), _child_text(entry, "date/month")) if part
            )
            or None,
            "authors": authors,
        }
    return {}


def _ietf_standard(name: str) -> dict[str, Any]:
    name = name.strip()
    api_url = f"https://datatracker.ietf.org/api/v1/doc/document/{quote(name, safe='')}/"
    metadata: dict[str, Any] = {}
    try:
        parsed = json.loads(read_https_text(api_url, accept="application/json").text)
        if isinstance(parsed, dict):
            metadata = parsed
    except ValueError:
        metadata = {}
    html_url = f"https://datatracker.ietf.org/doc/html/{quote(name, safe='')}"
    html = read_https_text(html_url, accept="text/html")
    text = _html_to_text(html.text)
    return {
        "source": "ietf",
        "identifier": name,
        "canonical_url": html_url,
        "title": metadata.get("title") or _title_from_html(html.text) or name,
        "status": metadata.get("states") or metadata.get("intended_std_level"),
        "published_at": metadata.get("time"),
        "authors": [],
        "references": _rfc_references(text),
        "content": text,
    }


def _w3c_standard(shortname: str) -> dict[str, Any]:
    shortname = shortname.strip().strip("/")
    url = f"https://www.w3.org/TR/{quote(shortname, safe='')}/"
    html = read_https_text(url, accept="text/html")
    text = _html_to_text(html.text)
    return {
        "source": "w3c",
        "identifier": shortname,
        "canonical_url": html.url,
        "title": _title_from_html(html.text) or shortname,
        "status": _meta_content(html.text, "w3c-status"),
        "published_at": _meta_content(html.text, "dcterms.issued"),
        "authors": [],
        "references": _rfc_references(text),
        "content": text,
    }


def _whatwg_standard(url: str) -> dict[str, Any]:
    html = read_https_text(url, accept="text/html")
    text = _html_to_text(html.text)
    return {
        "source": "whatwg",
        "identifier": html.url,
        "canonical_url": html.url,
        "title": _title_from_html(html.text) or html.url,
        "status": "living-standard",
        "published_at": None,
        "authors": [],
        "references": _rfc_references(text),
        "content": text,
    }


def _standard_metadata(standard: dict[str, Any]) -> dict[str, Any]:
    metadata = {key: value for key, value in standard.items() if key != "content"}
    sections = metadata.get("sections")
    if isinstance(sections, list):
        metadata["sections"] = [
            {key: value for key, value in section.items() if key != "content"}
            for section in sections
            if isinstance(section, dict)
        ]
    return metadata


def _items_for_standard(standard: dict[str, Any]) -> list[TypedPackItem]:
    items = [_item_for_standard(standard)]
    for section in standard.get("sections") or []:
        if isinstance(section, dict):
            items.append(_item_for_standard_section(standard, section))
    return items


def _item_for_standard(standard: dict[str, Any]) -> TypedPackItem:
    title = str(standard["title"])
    markdown = "\n".join(
        [
            "# " + title,
            "",
            f"- Source: {standard.get('source')}",
            f"- Identifier: `{standard.get('identifier')}`",
            f"- URL: {standard.get('canonical_url')}",
            f"- Status: {standard.get('status') or 'unknown'}",
            f"- Published: {standard.get('published_at') or 'unknown'}",
            f"- Authors: {', '.join(standard.get('authors') or []) or 'unknown'}",
            f"- Sections: {len(standard.get('sections') or [])}",
            "",
            "This record describes the standard as a whole. "
            "Use section records for precise clause citations.",
        ]
    )
    return TypedPackItem(
        title=title,
        url=str(standard["canonical_url"]),
        markdown=markdown,
        source_type="standard",
        item_kind=str(standard["source"]),
        metadata={key: value for key, value in standard.items() if key != "content"},
        route={"source_kind": standard["source"], "source_url": standard["canonical_url"]},
        public={"identifier": standard.get("identifier"), "status": standard.get("status")},
    )


def _item_for_standard_section(standard: dict[str, Any], section: dict[str, Any]) -> TypedPackItem:
    title = f"{standard['identifier']} section {section['label']}: {section['title']}"
    canonical_url = str(standard["canonical_url"])
    section_url = canonical_url + "#" + str(section["anchor"])
    markdown = "\n".join(
        [
            "# " + title,
            "",
            f"- Standard: `{standard.get('identifier')}`",
            f"- URL: {canonical_url}",
            f"- Section: `{section['label']}`",
            "",
            str(section["content"]),
        ]
    )
    return TypedPackItem(
        title=title,
        url=section_url,
        markdown=markdown,
        source_type="standard_section",
        item_kind=f"{standard['source']}_section",
        metadata={
            **_standard_metadata(standard),
            "section_label": section["label"],
            "section_title": section["title"],
            "section_anchor": section["anchor"],
        },
        route={
            "source_kind": standard["source"],
            "source_url": standard["canonical_url"],
            "section_label": section["label"],
        },
        public={
            "identifier": standard.get("identifier"),
            "section_label": section["label"],
            "section_title": section["title"],
        },
    )


_NUMBERED_SECTION_RE = re.compile(
    r"^\s*(?P<label>[1-9]\d*(?:\.[1-9]\d*)*)\.?\s+(?P<title>[A-Z][^\n]{2,140})\s*$"
)
_NAMED_SECTION_NAMES = (
    "Abstract",
    "Status of This Document",
    "Introduction",
    "Security Considerations",
    "IANA Considerations",
    "References",
    "Acknowledgments?",
)
_NAMED_SECTION_RE = re.compile(r"^\s*(?P<title>" + "|".join(_NAMED_SECTION_NAMES) + r")\s*$", re.IGNORECASE)


def _standard_sections(text: str) -> list[dict[str, Any]]:
    lines = [line.rstrip() for line in text.replace("\f", "\n").splitlines()]
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal current, buffer
        if current is None:
            return
        content = "\n".join(buffer).strip()
        if content:
            current["content"] = content
            sections.append(current)
        current = None
        buffer = []

    for line in lines:
        heading = _section_heading(line)
        if heading is not None:
            flush()
            label, title = heading
            current = {
                "schema_version": 3,
                "label": label,
                "title": title,
                "anchor": "section-" + re.sub(r"[^a-zA-Z0-9.-]+", "-", label).strip("-").lower(),
            }
            buffer = [line.strip()]
            continue
        if current is not None:
            buffer.append(line)
    flush()

    if sections:
        return sections[:200]
    stripped = text.strip()
    if not stripped:
        return []
    return [
        {
            "schema_version": 3,
            "label": "full",
            "title": "Full Text",
            "anchor": "section-full",
            "content": stripped,
        }
    ]


def _section_heading(line: str) -> tuple[str, str] | None:
    clean = line.strip()
    if not clean or len(clean) > 180:
        return None
    numbered = _NUMBERED_SECTION_RE.match(clean)
    if numbered:
        title = numbered.group("title").strip().rstrip(".")
        if title.lower().startswith(("page ", "table of contents")):
            return None
        return numbered.group("label"), title
    named = _NAMED_SECTION_RE.match(clean)
    if named:
        title = re.sub(r"\s+", " ", named.group("title")).strip()
        return title.lower().replace(" ", "-"), title
    return None


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav"]):
        tag.decompose()
    return re.sub(r"\n{3,}", "\n\n", soup.get_text("\n")).strip()


def _title_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    return h1.get_text(" ", strip=True) if h1 else None


def _title_from_text(text: str) -> str | None:
    for line in text.splitlines()[:80]:
        clean = line.strip()
        if clean and not clean.startswith("RFC "):
            return clean
    return None


def _rfc_references(text: str) -> list[str]:
    return sorted({f"RFC{match}" for match in re.findall(r"\bRFC\s*(\d{3,5})\b", text)})[:200]


def _meta_content(html: str, name: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
    if not isinstance(tag, Tag):
        return None
    content = tag.get("content")
    return str(content).strip() if content else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(element: Any, name: str) -> str | None:
    parts = name.split("/")
    current = element
    for part in parts:
        found = None
        for child in list(current):
            if _local_name(child.tag) == part:
                found = child
                break
        if found is None:
            return None
        current = found
    text = "".join(current.itertext()).strip()
    return text or None


__all__ = ["DEFAULT_STANDARDS_OUTPUT_DIR", "async_build_standards_pack", "build_standards_pack"]
