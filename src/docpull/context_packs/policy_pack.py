"""Local policy-document discovery and clause extraction workflow."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..contracts import stable_id
from ..policy import PolicyConfig
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    append_ndjson,
    artifact_ref,
    domain_from_input,
    ensure_policy_for_domain,
    evidence_for_page,
    extract_links,
    fetch_pages_blocking,
    homepage_url_for_domain,
    public_url,
    quote_markdown,
    same_policy_domain,
    status_from_errors,
    write_basic_pack_files,
    write_json,
)

POLICY_WORKFLOW = "policy-pack"
DEFAULT_POLICY_OUTPUT_DIR = Path("packs/policies")
POLICY_DOCUMENT_TYPES = (
    "terms",
    "privacy",
    "dpa",
    "cookies",
    "ai_terms",
    "subprocessors",
    "security",
    "refund",
)
_TYPE_TERMS: dict[str, tuple[str, ...]] = {
    "terms": ("terms", "terms-of-service", "terms-of-use", "tos"),
    "privacy": ("privacy", "privacy-policy"),
    "dpa": ("dpa", "data-processing", "data processing addendum"),
    "cookies": ("cookie", "cookies"),
    "ai_terms": ("ai-terms", "ai terms", "generative-ai", "artificial intelligence terms"),
    "subprocessors": ("subprocessor", "sub-processors"),
    "security": ("security", "trust-center", "trust center"),
    "refund": ("refund", "cancellation", "return-policy"),
}
_EFFECTIVE_DATE_RE = re.compile(
    r"(?:effective|last\s+updated|updated|revision\s+date)\s*(?:date)?\s*[:\-]?\s*"
    r"(?P<date>(?:[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})|(?:\d{4}-\d{2}-\d{2})|"
    r"(?:\d{1,2}/\d{1,2}/\d{4}))",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def build_policy_pack(
    domain_or_url: str,
    *,
    output_dir: Path = DEFAULT_POLICY_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    max_pages: int = 16,
    baseline_pack: Path | None = None,
) -> dict[str, Any]:
    """Discover policy pages and emit neutral, clause-level evidence records."""

    domain = domain_from_input(domain_or_url)
    if not domain:
        raise ContextPackError("Could not resolve a domain from policy-pack input.")
    effective_policy = ensure_policy_for_domain(policy, domain)
    run = ContextPackRun(
        workflow=POLICY_WORKFLOW,
        output_dir=output_dir.resolve(),
        policy=effective_policy,
        input_value=domain_or_url,
    )
    start_url = public_url(domain_or_url if "://" in domain_or_url else homepage_url_for_domain(domain))
    discovery_pages = fetch_pages_blocking([start_url], run=run, max_pages=1)
    if not discovery_pages:
        raise ContextPackError(f"Could not fetch policy discovery target: {start_url}")

    urls = _policy_urls(discovery_pages[0], domain=domain, max_pages=max_pages)
    if _policy_target_hint(discovery_pages[0]) and start_url not in urls:
        urls.insert(0, start_url)
    pages = fetch_pages_blocking(urls, run=run, max_pages=max_pages) if urls else []
    if not pages:
        run.warn(
            "policy_documents_not_found",
            (
                "No linked policy documents were found; the discovery page is retained "
                "as unknown policy evidence."
            ),
        )
        pages = discovery_pages

    policy_documents = [_policy_document(page, pages) for page in pages]
    clauses = [
        clause
        for document, page in zip(policy_documents, pages, strict=False)
        for clause in _stable_clauses(page, pages, document_type=str(document["document_type"]))
    ]
    changes = _clause_changes(baseline_pack, clauses) if baseline_pack else []
    run.warn(
        "no_legal_conclusions",
        (
            "Policy extraction reports source text and change candidates only; "
            "it does not provide legal conclusions."
        ),
    )

    policies_path = run.output_dir / "policies.ndjson"
    clauses_path = run.output_dir / "policy.clauses.ndjson"
    changes_path = run.output_dir / "policy.changes.json"
    append_ndjson(policies_path, policy_documents)
    append_ndjson(clauses_path, clauses)
    write_json(
        changes_path,
        {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "baseline_pack": str(baseline_pack.resolve()) if baseline_pack else None,
            "change_count": len(changes),
            "changes": changes,
            "disclaimer": "Textual change candidates only; no legal conclusions.",
        },
    )
    result_payload = {
        "workflow": POLICY_WORKFLOW,
        "provider": "local",
        "status": status_from_errors(run.errors),
        "input": {"value": public_url(domain_or_url), "domain": domain},
        "summary": {
            "domain": domain,
            "page_count": len(pages),
            "policy_document_count": len(policy_documents),
            "clause_count": len(clauses),
            "change_candidate_count": len(changes),
            "document_types": sorted({str(item["document_type"]) for item in policy_documents}),
        },
        "policies": policy_documents,
        "clauses": clauses,
        "change_candidates": changes,
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {
            "domain_or_url": domain_or_url,
            "max_pages": max_pages,
            "baseline_pack": str(baseline_pack) if baseline_pack else None,
        },
    }
    return write_basic_pack_files(
        run=run,
        pages=pages,
        result_filename="policy.result.json",
        result_payload=result_payload,
        markdown_filename="POLICIES.md",
        markdown_text=_policy_markdown(policy_documents, clauses, changes),
        pack_filename="policy.pack.json",
        extra_artifacts={
            "policies_ndjson": artifact_ref(run.output_dir, policies_path),
            "policy_clauses": artifact_ref(run.output_dir, clauses_path),
            "policy_changes": artifact_ref(run.output_dir, changes_path),
        },
    )


def classify_policy_document(page: PageSnapshot) -> str:
    haystack = " ".join((page.url, page.title or "", page.markdown[:2000])).lower()
    scored = [
        (sum(1 for term in terms if term in haystack), document_type)
        for document_type, terms in _TYPE_TERMS.items()
    ]
    score, document_type = max(scored, default=(0, "other"))
    return document_type if score else "other"


def _policy_target_hint(page: PageSnapshot) -> bool:
    haystack = f"{urlparse(page.url).path} {page.title or ''}".lower()
    return any(term in haystack for terms in _TYPE_TERMS.values() for term in terms)


def _policy_urls(page: PageSnapshot, *, domain: str, max_pages: int) -> list[str]:
    candidates: list[tuple[int, str]] = []
    for link in extract_links(page):
        url = public_url(urljoin(page.url, link["url"]))
        if not same_policy_domain(url, domain):
            continue
        haystack = f"{urlparse(url).path} {link['text']}".lower()
        score = sum(1 for terms in _TYPE_TERMS.values() for term in terms if term in haystack)
        if score:
            candidates.append((score, url))
    output: list[str] = []
    for _score, url in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if url not in output:
            output.append(url)
        if len(output) >= max_pages:
            break
    return output


def _policy_document(page: PageSnapshot, pages: list[PageSnapshot]) -> dict[str, Any]:
    document_type = classify_policy_document(page)
    content_hash = hashlib.sha256(page.markdown.encode("utf-8")).hexdigest()
    effective_match = _EFFECTIVE_DATE_RE.search(page.markdown)
    effective_date = effective_match.group("date") if effective_match else None
    evidence_text = effective_match.group(0) if effective_match else page.markdown[:280]
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "policy_document_id": stable_id("policy", {"url": page.url, "type": document_type}),
        "document_type": document_type,
        "title": page.title,
        "url": page.url,
        "effective_date": effective_date,
        "content_hash": content_hash,
        "classification_status": "heuristic",
        "evidence": evidence_for_page(
            page,
            pages,
            field="effective_date" if effective_date else "policy_document",
            excerpt=evidence_text,
        ).to_dict(),
    }


def _stable_clauses(
    page: PageSnapshot,
    pages: list[PageSnapshot],
    *,
    document_type: str,
) -> list[dict[str, Any]]:
    content = page.markdown.strip()
    matches = list(_HEADING_RE.finditer(content))
    sections: list[tuple[str, str, int]] = []
    if matches:
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            sections.append((match.group(2).strip(), content[start:end].strip(), start))
    else:
        paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
        cursor = 0
        for index, paragraph in enumerate(paragraphs, start=1):
            start = content.find(paragraph, cursor)
            cursor = max(cursor, start + len(paragraph))
            sections.append((f"Clause {index}", paragraph, max(0, start)))

    clauses: list[dict[str, Any]] = []
    duplicate_headings: dict[str, int] = {}
    for heading, text, _start in sections:
        normalized_heading = " ".join(heading.lower().split())
        duplicate_headings[normalized_heading] = duplicate_headings.get(normalized_heading, 0) + 1
        ordinal = duplicate_headings[normalized_heading]
        clause_key = {
            "url": page.url,
            "document_type": document_type,
            "heading": normalized_heading,
            "ordinal": ordinal,
        }
        clauses.append(
            {
                "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
                "clause_id": stable_id("clause", clause_key),
                "document_type": document_type,
                "heading": heading,
                "ordinal": ordinal,
                "url": page.url,
                "section_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
                "evidence": evidence_for_page(page, pages, field="clause", excerpt=text).to_dict(),
            }
        )
    return clauses


def _clause_changes(baseline_pack: Path, current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    path = baseline_pack.resolve() / "policy.clauses.ndjson"
    if not path.exists():
        raise ContextPackError(f"Baseline policy pack has no policy.clauses.ndjson: {baseline_pack}")
    previous = [
        value
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and isinstance((value := json.loads(line)), dict)
    ]
    old_by_id = {str(item.get("clause_id")): item for item in previous if item.get("clause_id")}
    new_by_id = {str(item.get("clause_id")): item for item in current if item.get("clause_id")}
    changes: list[dict[str, Any]] = []
    for clause_id in sorted(old_by_id.keys() | new_by_id.keys()):
        before = old_by_id.get(clause_id)
        after = new_by_id.get(clause_id)
        if before and after and before.get("section_hash") == after.get("section_hash"):
            continue
        kind = "modified" if before and after else ("removed" if before else "added")
        payload = {
            "clause_id": clause_id,
            "change_type": kind,
            "before": before.get("evidence") if before else None,
            "after": after.get("evidence") if after else None,
            "before_hash": before.get("section_hash") if before else None,
            "after_hash": after.get("section_hash") if after else None,
            "status": "candidate",
            "classification": "policy",
        }
        payload["change_candidate_id"] = stable_id("change", payload)
        changes.append(payload)
    return changes


def _policy_markdown(
    documents: list[dict[str, Any]],
    clauses: list[dict[str, Any]],
    changes: list[dict[str, Any]],
) -> str:
    lines = [
        "# Policy Evidence",
        "",
        "> Source text and change candidates only. DocPull does not provide legal conclusions.",
        "",
    ]
    for document in documents:
        lines.extend(
            [
                f"## {quote_markdown(str(document.get('title') or document['document_type']))}",
                f"- Type: `{document['document_type']}`",
                f"- Effective date: `{document.get('effective_date') or 'not detected'}`",
                f"- Source: {document['url']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Summary",
            "",
            f"- Stable clauses: {len(clauses)}",
            f"- Change candidates: {len(changes)}",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "DEFAULT_POLICY_OUTPUT_DIR",
    "POLICY_DOCUMENT_TYPES",
    "POLICY_WORKFLOW",
    "build_policy_pack",
    "classify_policy_document",
]
