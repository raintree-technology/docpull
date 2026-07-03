"""Build v3 packs from research paper metadata and local papers."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup, Tag
from defusedxml import ElementTree

from ..document_parse import DocumentParseError, ParsedDocument, parse_one_document
from ..http.client import AsyncHttpClient
from ..http.rate_limiter import PerHostRateLimiter
from ..security.download_policy import SafeDownloadPolicy, UnsafeDownloadError, content_type_base
from ..security.url_validator import UrlValidator
from .common import ContextPackError, write_json
from .typed import (
    PrepareLevel,
    TypedPackItem,
    read_https_text,
    simple_summary_markdown,
    typed_http_cache,
    write_typed_pack,
)
from .typed_models import PaperMetadataArtifact

PAPER_WORKFLOW = "paper-pack"
DEFAULT_PAPER_OUTPUT_DIR = Path("packs/papers")
MAX_PAPER_TEXT_BYTES = 5_000_000
MAX_ARXIV_PDF_BYTES = 25_000_000
ARXIV_API_DELAY_SECONDS = 3.0
_ARXIV_HOSTS = frozenset({"arxiv.org", "www.arxiv.org"})
_DOI_HOSTS = frozenset({"doi.org", "dx.doi.org", "www.doi.org"})
_PUBMED_HOSTS = frozenset({"pubmed.ncbi.nlm.nih.gov"})


def build_paper_pack(
    sources: list[str | Path],
    *,
    output_dir: Path = DEFAULT_PAPER_OUTPUT_DIR,
    max_items: int = 50,
    chunk_tokens: int = 4000,
    include_full_text: bool = False,
    prepare_level: PrepareLevel = "raw",
    cache_dir: Path | None = None,
    cache_ttl_days: int | None = 7,
) -> dict[str, Any]:
    """Build a v3 pack from local papers, arXiv IDs, DOIs, PubMed IDs, or paper URLs."""
    if not sources:
        raise ContextPackError("paper-pack requires at least one source.")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    items: list[TypedPackItem] = []
    metadata_rows: list[dict[str, Any]] = []
    reference_rows: list[dict[str, Any]] = []
    with typed_http_cache(cache_dir, ttl_days=cache_ttl_days):
        for source in sources:
            paper = _paper_from_source(source, include_full_text=include_full_text)
            metadata_rows.append(paper["metadata"])
            reference_rows.extend(paper.get("references", []))
            items.append(_item_for_paper(paper))
            if len(items) >= max_items:
                break
    metadata_path = output_dir / "paper.metadata.json"
    references_path = output_dir / "paper.references.ndjson"
    write_json(
        metadata_path,
        PaperMetadataArtifact(
            workflow=PAPER_WORKFLOW,
            paper_count=len(metadata_rows),
            papers=metadata_rows,
        ).model_dump(mode="json"),
    )

    references_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in reference_rows),
        encoding="utf-8",
    )
    return write_typed_pack(
        workflow=PAPER_WORKFLOW,
        output_format="paper",
        output_dir=output_dir,
        items=items,
        pack_filename="paper.pack.json",
        index_filename="paper.index.json",
        items_filename="paper.items.ndjson",
        summary_filename="PAPER.md",
        index_payload={"papers": metadata_rows, "reference_count": len(reference_rows)},
        summary_markdown=simple_summary_markdown(
            title="Paper Pack",
            source=", ".join(str(source) for source in sources),
            items=items,
        ),
        result_summary={"paper_count": len(metadata_rows), "reference_count": len(reference_rows)},
        chunk_tokens=chunk_tokens,
        extra_artifacts={"metadata": metadata_path, "references": references_path},
        prepare_level=prepare_level,
    )


async def async_build_paper_pack(
    sources: list[str | Path],
    **kwargs: Any,
) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_paper_pack, sources, **kwargs)


def _paper_from_source(source: str | Path, *, include_full_text: bool) -> dict[str, Any]:
    value = str(source)
    if value.startswith("arxiv:"):
        return _paper_from_arxiv(value.split(":", 1)[1], include_full_text=include_full_text)
    if value.startswith("doi:"):
        return _paper_from_doi(value.split(":", 1)[1])
    if value.startswith("pmid:"):
        return _paper_from_pmid(value.split(":", 1)[1])
    if value.startswith(("http://", "https://")):
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if hostname in _ARXIV_HOSTS and parsed.path.startswith("/abs/"):
            return _paper_from_arxiv(parsed.path.rsplit("/", 1)[-1], include_full_text=include_full_text)
        if hostname in _DOI_HOSTS:
            return _paper_from_doi(parsed.path.strip("/"))
        if hostname in _PUBMED_HOSTS:
            pmid = parsed.path.strip("/").split("/", 1)[0]
            return _paper_from_pmid(pmid)
        return _paper_from_metadata_url(value)
    return _paper_from_local(Path(source))


def _paper_from_arxiv(arxiv_id: str, *, include_full_text: bool) -> dict[str, Any]:
    arxiv_id = arxiv_id.strip()
    url = "https://export.arxiv.org/api/query?id_list=" + quote(arxiv_id, safe="")
    response = read_https_text(
        url,
        accept="application/atom+xml, application/xml, text/xml",
        delay_seconds=ARXIV_API_DELAY_SECONDS,
        source_contract="arxiv_api",
    )
    root = ElementTree.fromstring(response.text.encode("utf-8"))
    entry = next((child for child in list(root) if _local_name(child.tag) == "entry"), None)
    if entry is None:
        raise ContextPackError(f"arXiv returned no entry for {arxiv_id}")
    metadata: dict[str, Any] = {
        "source": "arxiv",
        "identifier": arxiv_id,
        "canonical_url": _child_text(entry, "id") or f"https://arxiv.org/abs/{arxiv_id}",
        "title": _clean(_child_text(entry, "title") or arxiv_id),
        "abstract": _clean(_child_text(entry, "summary") or ""),
        "authors": [
            _child_text(author, "name")
            for author in _children(entry, "author")
            if _child_text(author, "name")
        ],
        "published_at": _child_text(entry, "published"),
        "updated_at": _child_text(entry, "updated"),
        "categories": [
            str(child.attrib.get("term") or "")
            for child in _children(entry, "category")
            if child.attrib.get("term")
        ],
        "doi": _child_text(entry, "doi"),
        "pdf_url": _arxiv_link(entry, title="pdf"),
        "license": _arxiv_link(entry, rel="license"),
    }
    full_text = ""
    full_text_status = "not_requested"
    if include_full_text:
        try:
            pdf_url = str(metadata.get("pdf_url") or f"https://arxiv.org/pdf/{arxiv_id}")
            parsed_pdf = _parse_arxiv_pdf(
                pdf_url=pdf_url,
                title=str(metadata["title"]),
            )
            full_text = parsed_pdf.content
            metadata["parse_backend"] = parsed_pdf.backend
            metadata["source_mime_type"] = parsed_pdf.source_mime_type
            full_text_status = "included_arxiv_pdf"
        except (DocumentParseError, OSError, ValueError) as err:
            full_text_status = f"unavailable: {err}"
    metadata["full_text_status"] = full_text_status
    references = _references_from_text(full_text) if full_text else []
    return {"metadata": metadata, "content": full_text, "references": references}


def _parse_arxiv_pdf(*, pdf_url: str, title: str) -> ParsedDocument:
    pdf_bytes = _fetch_arxiv_pdf_bytes(pdf_url)
    with tempfile.NamedTemporaryFile(prefix="docpull-arxiv-", suffix=".pdf", delete=False) as handle:
        handle.write(pdf_bytes)
        temp_path = Path(handle.name)
    try:
        return parse_one_document(temp_path, backend="auto", source_url=pdf_url, title=title)
    finally:
        temp_path.unlink(missing_ok=True)


def _fetch_arxiv_pdf_bytes(pdf_url: str) -> bytes:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch_arxiv_pdf_bytes_async(pdf_url))
    raise ContextPackError("Remote paper-pack sources cannot be fetched while an event loop is running.")


async def _fetch_arxiv_pdf_bytes_async(pdf_url: str) -> bytes:
    parsed = urlparse(pdf_url)
    if parsed.scheme != "https" or parsed.netloc.lower() not in {"arxiv.org", "www.arxiv.org"}:
        raise ValueError(f"Refusing non-arXiv PDF URL: {pdf_url}")
    if not parsed.path.startswith("/pdf/"):
        raise ValueError(f"Refusing unexpected arXiv PDF path: {pdf_url}")

    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(pdf_url)
    if not validation.is_valid:
        raise ValueError(f"arXiv PDF URL rejected: {validation.rejection_reason}")
    rate_limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    async with AsyncHttpClient(
        rate_limiter=rate_limiter,
        url_validator=validator,
        default_timeout=45.0,
        max_content_size=MAX_ARXIV_PDF_BYTES,
        download_policy=_ArxivPdfDownloadPolicy(),
    ) as client:
        response = await client.get(pdf_url, headers={"Accept": "application/pdf"})
    if response.status_code >= 400:
        raise ValueError(f"Could not fetch arXiv PDF {pdf_url}: HTTP {response.status_code}")
    return response.content


class _ArxivPdfDownloadPolicy(SafeDownloadPolicy):
    """Allow only arXiv PDFs for explicit paper full-text parsing."""

    def validate_request_url(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.netloc.lower() in {"arxiv.org", "www.arxiv.org"}:
            return
        super().validate_request_url(url)

    def validate_response_headers(
        self,
        url: str,
        *,
        status_code: int,
        headers: dict[str, str],
        content_type: str | None,
    ) -> None:
        parsed = urlparse(url)
        base_type = content_type_base(content_type or headers.get("Content-Type"))
        if (
            status_code < 400
            and parsed.scheme == "https"
            and parsed.netloc.lower() in {"arxiv.org", "www.arxiv.org"}
            and parsed.path.startswith("/pdf/")
            and base_type == "application/pdf"
        ):
            return
        raise UnsafeDownloadError(f"Refusing non-arXiv PDF response from {url}.")

    def validate_body_prefix(self, url: str, body_prefix: bytes) -> None:
        if body_prefix.startswith(b"%PDF"):
            return
        raise UnsafeDownloadError(f"Refusing non-PDF body while fetching {url}.")


def _paper_from_doi(doi: str) -> dict[str, Any]:
    doi = doi.strip()
    mailto = os.environ.get("DOCPULL_CONTACT_EMAIL")
    url = "https://api.crossref.org/works/" + quote(doi, safe="")
    if mailto:
        url += "?mailto=" + quote(mailto, safe="@.")
    response = read_https_text(url, accept="application/json", source_contract="crossref_api")
    payload = json.loads(response.text)
    message = payload.get("message") if isinstance(payload, dict) else {}
    if not isinstance(message, dict):
        raise ContextPackError(f"Crossref returned no metadata for DOI {doi}")
    metadata: dict[str, Any] = {
        "source": "crossref",
        "identifier": doi,
        "canonical_url": message.get("URL") or f"https://doi.org/{doi}",
        "title": _first_list_string(message.get("title")) or doi,
        "abstract": _html_to_text(str(message.get("abstract") or "")),
        "authors": [
            _crossref_author(author) for author in message.get("author") or [] if isinstance(author, dict)
        ],
        "published_at": _crossref_date(
            message.get("published-print") or message.get("published-online") or message.get("created")
        ),
        "container_title": _first_list_string(message.get("container-title")),
        "publisher": message.get("publisher"),
        "doi": message.get("DOI") or doi,
        "license": message.get("license"),
    }
    references = []
    for index, reference in enumerate(message.get("reference") or [], start=1):
        if isinstance(reference, dict):
            references.append({"schema_version": 3, "paper_id": doi, "index": index, **reference})
    return {"metadata": metadata, "content": "", "references": references}


def _paper_from_pmid(pmid: str) -> dict[str, Any]:
    pmid = pmid.strip()
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        f"?db=pubmed&id={quote(pmid, safe='')}&retmode=json"
    )
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        url += "&api_key=" + quote(api_key, safe="")
    response = read_https_text(url, accept="application/json", source_contract="ncbi_eutils")
    payload = json.loads(response.text)
    result = payload.get("result") if isinstance(payload, dict) else {}
    record = result.get(pmid) if isinstance(result, dict) else {}
    if not isinstance(record, dict):
        raise ContextPackError(f"PubMed returned no metadata for PMID {pmid}")
    metadata: dict[str, Any] = {
        "source": "pubmed",
        "identifier": pmid,
        "canonical_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        "title": record.get("title") or pmid,
        "abstract": "",
        "authors": [
            author.get("name")
            for author in record.get("authors") or []
            if isinstance(author, dict) and author.get("name")
        ],
        "published_at": record.get("pubdate"),
        "container_title": record.get("fulljournalname"),
        "doi": _article_id(record, "doi"),
        "pmid": pmid,
    }
    return {"metadata": metadata, "content": "", "references": _pubmed_references(pmid)}


def _pubmed_references(pmid: str) -> list[dict[str, Any]]:
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
        f"?dbfrom=pubmed&id={quote(pmid, safe='')}&linkname=pubmed_pubmed_refs&retmode=json"
    )
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        url += "&api_key=" + quote(api_key, safe="")
    try:
        response = read_https_text(url, accept="application/json", source_contract="ncbi_eutils")
    except ValueError:
        return []
    payload = json.loads(response.text)
    linksets = payload.get("linksets") if isinstance(payload, dict) else []
    references: list[dict[str, Any]] = []
    for linkset in linksets if isinstance(linksets, list) else []:
        if not isinstance(linkset, dict):
            continue
        linksetdbs = linkset.get("linksetdbs")
        for linksetdb in linksetdbs if isinstance(linksetdbs, list) else []:
            if not isinstance(linksetdb, dict):
                continue
            links = linksetdb.get("links")
            for ref_pmid in links if isinstance(links, list) else []:
                references.append(
                    {
                        "schema_version": 3,
                        "source": "pubmed",
                        "paper_id": pmid,
                        "index": len(references) + 1,
                        "pmid": str(ref_pmid),
                    }
                )
    return references


def _paper_from_metadata_url(url: str) -> dict[str, Any]:
    response = read_https_text(url, accept="text/html, text/plain, application/json")
    title = url
    abstract = ""
    if "html" in response.content_type:
        soup = BeautifulSoup(response.text, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        abstract = _meta_content(soup, "citation_abstract") or _meta_content(soup, "description") or ""
    else:
        abstract = response.text[:2000]
    metadata = {
        "source": "url",
        "identifier": url,
        "canonical_url": response.url,
        "title": title,
        "abstract": abstract,
    }
    return {"metadata": metadata, "content": "", "references": []}


def _paper_from_local(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ContextPackError(f"Paper source file does not exist: {path}")
    try:
        parsed = parse_one_document(path, backend="auto", source_url=path.as_uri(), title=None)
        content = parsed.content
        backend = parsed.backend
        source_mime_type = parsed.source_mime_type
    except DocumentParseError as err:
        raise ContextPackError(str(err)) from err
    metadata: dict[str, Any] = {
        "source": "local",
        "identifier": str(path),
        "canonical_url": path.as_uri(),
        "title": parsed.title,
        "abstract": _first_paragraph(content),
        "authors": [],
        "published_at": None,
        "source_mime_type": source_mime_type,
        "parse_backend": backend,
        "full_text_status": "included_local",
    }
    return {"metadata": metadata, "content": content, "references": []}


def _item_for_paper(paper: dict[str, Any]) -> TypedPackItem:
    metadata = paper["metadata"]
    title = str(metadata.get("title") or metadata.get("identifier") or "Paper")
    body = str(paper.get("content") or metadata.get("abstract") or "").strip()
    markdown = "\n".join(
        [
            "# " + title,
            "",
            f"- Source: {metadata.get('source')}",
            f"- Identifier: `{metadata.get('identifier')}`",
            f"- URL: {metadata.get('canonical_url')}",
            f"- Published: {metadata.get('published_at') or 'unknown'}",
            f"- Authors: {', '.join(metadata.get('authors') or []) or 'unknown'}",
            f"- DOI: {metadata.get('doi') or 'unknown'}",
            "",
            "## Abstract / Content",
            "",
            body,
        ]
    )
    return TypedPackItem(
        title=title,
        url=str(metadata.get("canonical_url") or metadata.get("identifier")),
        markdown=markdown,
        source_type="paper",
        item_kind=str(metadata.get("source") or "paper"),
        metadata=metadata,
        route={"source_kind": metadata.get("source"), "source_url": metadata.get("canonical_url")},
        rights={"status": "unknown", "license": metadata.get("license")},
        public={
            "identifier": metadata.get("identifier"),
            "source": metadata.get("source"),
            "published_at": metadata.get("published_at"),
        },
    )


def _children(element: Any, name: str) -> list[Any]:
    return [child for child in list(element) if _local_name(child.tag) == name]


def _child_text(element: Any, name: str) -> str | None:
    for child in _children(element, name):
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _arxiv_link(entry: Any, *, title: str | None = None, rel: str | None = None) -> str | None:
    for child in _children(entry, "link"):
        if title and str(child.attrib.get("title") or "").lower() != title:
            continue
        if rel and str(child.attrib.get("rel") or "").lower() != rel:
            continue
        href = child.attrib.get("href")
        if href:
            return str(href)
    return None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _html_to_text(value: str) -> str:
    if not value:
        return ""
    return _clean(BeautifulSoup(value, "html.parser").get_text("\n"))


def _first_list_string(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                return item.strip()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _crossref_author(author: dict[str, Any]) -> str:
    name = " ".join(str(author.get(key) or "").strip() for key in ("given", "family")).strip()
    return name or str(author.get("name") or "").strip()


def _crossref_date(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    parts = value.get("date-parts")
    if isinstance(parts, list) and parts and isinstance(parts[0], list):
        return "-".join(str(part) for part in parts[0])
    return None


def _article_id(record: dict[str, Any], kind: str) -> str | None:
    for item in record.get("articleids") or []:
        if isinstance(item, dict) and item.get("idtype") == kind:
            return str(item.get("value") or "")
    return None


def _meta_content(soup: BeautifulSoup, name: str) -> str | None:
    tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
    if isinstance(tag, Tag):
        content = tag.get("content")
        if content:
            return str(content).strip()
    return None


def _references_from_text(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    match = re.search(r"\bReferences\b(?P<body>.+)$", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []
    body = match.group("body")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    references: list[dict[str, Any]] = []
    for line in lines:
        if len(line) < 12:
            continue
        if not re.match(r"^(\[\d+\]|\d+\.|\w.+\(\d{4}\))", line):
            continue
        references.append(
            {
                "schema_version": 3,
                "source": "text",
                "index": len(references) + 1,
                "raw": line[:2000],
            }
        )
        if len(references) >= 200:
            break
    return references


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text):
        if block.strip():
            return block.strip()[:2000]
    return text[:2000]


__all__ = ["DEFAULT_PAPER_OUTPUT_DIR", "async_build_paper_pack", "build_paper_pack"]
