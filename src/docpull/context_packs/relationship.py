"""Evidence-backed relationship candidates for human review."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

from ..contracts import (
    CoverageResult,
    RelationshipCandidate,
    RelationshipPack,
    WorkflowWarning,
    canonical_sha256,
    stable_id,
    workflow_failure_from_mapping,
)
from ..evidence import evidence_span_payload
from ..policy import PolicyConfig
from .common import (
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    artifact_ref,
    domain_from_input,
    fetch_pages_blocking,
    homepage_url_for_domain,
    likely_internal_pages,
    public_url,
    status_from_errors,
    write_basic_pack_files,
    write_json,
)

RELATIONSHIP_WORKFLOW = "relationship-pack"
DEFAULT_RELATIONSHIP_OUTPUT_DIR = Path("packs/relationship")
SUPPORTED_RELATIONSHIP_PREDICATES = (
    "owned_by",
    "operated_by",
    "acquired_by",
    "franchised_by",
    "invested_in",
)


def extract_relationship_candidates_from_records(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Extract deterministic review candidates from existing pack records."""

    candidates: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        content = str(record.get("content") or "")
        url = str(record.get("url") or "")
        if not content or not url:
            continue
        metadata_raw = record.get("metadata")
        extraction_raw = record.get("extraction")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
        subject = next(
            (
                str(value).strip()
                for value in (
                    metadata.get("entity_name"),
                    metadata.get("brand_name"),
                    metadata.get("company_name"),
                    extraction.get("entity_name"),
                )
                if isinstance(value, str) and value.strip()
            ),
            "",
        )
        if not subject:
            title = str(record.get("title") or "").strip()
            subject = re.split(r"\s+[|—–-]\s+", title, maxsplit=1)[0].strip()
        if not subject:
            subject = (urlparse(url).hostname or "Unknown entity").removeprefix("www.")
        page = PageSnapshot(
            url=url,
            title=str(record.get("title") or subject),
            html="",
            markdown=content,
            metadata=metadata,
            extraction=extraction,
            source_type=str(record.get("source_type") or "relationship-source"),
        )
        candidates.extend(
            _extract_relationship_candidates(
                {
                    "name": subject,
                    "location_scope": metadata.get("location_scope"),
                },
                [page],
                citation_offset=index,
            )
        )
    deduped = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if candidate.get("candidate_id")
    }
    return [deduped[key] for key in sorted(deduped)]


def build_relationship_pack(
    sources: list[str | dict[str, Any]] | str,
    *,
    output_dir: Path = DEFAULT_RELATIONSHIP_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    max_pages_per_source: int = 4,
) -> dict[str, Any]:
    """Extract cited relationship observations and one coverage result per input."""

    raw_sources = [sources] if isinstance(sources, str) else list(sources)
    if not raw_sources:
        raise ContextPackError("relationship-pack requires at least one source input.")
    if max_pages_per_source < 1:
        raise ContextPackError("max_pages_per_source must be at least 1.")

    specs = [_normalize_source(item, index=index) for index, item in enumerate(raw_sources, start=1)]
    domains = sorted(
        {
            domain
            for spec in specs
            for domain in spec["official_domains"]
            if isinstance(domain, str) and domain
        }
    )
    effective_policy = policy or PolicyConfig(allowed_domains=domains)
    if policy is not None and not policy.allowed_domains and domains:
        effective_policy = PolicyConfig.model_validate(
            {**policy.model_dump(mode="json"), "allowed_domains": domains}
        )
    output_dir = output_dir.resolve()
    run = ContextPackRun(
        workflow=RELATIONSHIP_WORKFLOW,
        output_dir=output_dir,
        policy=effective_policy,
        input_value=str(specs[0].get("url") or specs[0].get("path") or specs[0]["name"]),
    )

    all_pages: list[PageSnapshot] = []
    coverage_rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for spec in specs:
        error_start = len(run.errors)
        warning_start = len(run.warnings)
        pages = _pages_for_source(spec, run=run, max_pages=max_pages_per_source)
        page_offset = len(all_pages)
        all_pages.extend(pages)
        extracted = _extract_relationship_candidates(
            spec,
            pages,
            citation_offset=page_offset,
        )
        candidates.extend(extracted)
        failures = [
            workflow_failure_from_mapping(item, default_stage="fetch") for item in run.errors[error_start:]
        ]
        warnings = [
            WorkflowWarning.model_validate(
                {
                    "code": str(item.get("code") or "warning"),
                    "message": str(item.get("message") or "Workflow warning"),
                    "metadata": item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
                }
            )
            for item in run.warnings[warning_start:]
        ]
        if extracted:
            coverage_status = "candidate_found"
        elif any(failure.retryable for failure in failures):
            coverage_status = "retryable_failure"
        elif pages:
            coverage_status = "acquired_no_candidate"
        else:
            coverage_status = "blocked"
        if not extracted:
            warnings.append(
                WorkflowWarning(
                    code="coverage_gap",
                    message=(
                        "No reviewable relationship candidate was established for this input; "
                        "this is not a negative ownership or independence claim."
                    ),
                    metadata={"input_id": spec["input_id"]},
                )
            )
        coverage = CoverageResult(
            input_id=spec["input_id"],
            input={
                "name": spec["name"],
                "url": spec.get("url"),
                "path": spec.get("path"),
                "location_scope": spec.get("location_scope"),
                "official_domains": spec["official_domains"],
            },
            status=cast(
                Literal[
                    "candidate_found",
                    "acquired_no_candidate",
                    "retryable_failure",
                    "blocked",
                ],
                coverage_status,
            ),
            acquired_document_count=len(pages),
            coverage_gap=not bool(extracted),
            candidates=[RelationshipCandidate.model_validate(item) for item in extracted],
            failures=failures,
            warnings=warnings,
        )
        coverage_rows.append(coverage.model_dump(mode="json", exclude_none=True))

    if len(coverage_rows) != len(specs):
        raise ContextPackError("relationship-pack did not emit exactly one coverage result per input.")

    pack_identity = {
        "pack_id": stable_id("pack", {"workflow": RELATIONSHIP_WORKFLOW, "coverage": coverage_rows}),
        "input_count": len(specs),
        "content_hash": canonical_sha256(coverage_rows),
    }
    run_identity = {
        "run_id": stable_id(
            "run",
            {
                "pack_id": pack_identity["pack_id"],
                "inputs": [spec["input_id"] for spec in specs],
                "max_pages_per_source": max_pages_per_source,
            },
        ),
        "scheduler": None,
    }
    contract = RelationshipPack(
        pack_identity=pack_identity,
        run_identity=run_identity,
        coverage=[CoverageResult.model_validate(item) for item in coverage_rows],
        candidates=[RelationshipCandidate.model_validate(item) for item in candidates],
        summary={
            "input_count": len(specs),
            "coverage_count": len(coverage_rows),
            "candidate_count": len(candidates),
            "candidate_found_count": sum(item["status"] == "candidate_found" for item in coverage_rows),
            "acquired_no_candidate_count": sum(
                item["status"] == "acquired_no_candidate" for item in coverage_rows
            ),
            "retryable_failure_count": sum(item["status"] == "retryable_failure" for item in coverage_rows),
            "blocked_count": sum(item["status"] == "blocked" for item in coverage_rows),
        },
    )
    contract_path = output_dir / "relationship.pack.v1.json"
    write_json(contract_path, contract.model_dump(mode="json", exclude_none=True))
    result_payload = {
        "workflow": RELATIONSHIP_WORKFLOW,
        "provider": "local",
        "status": status_from_errors(run.errors),
        "input": {"sources": specs},
        "summary": contract.summary,
        "coverage": coverage_rows,
        "relationship_candidates": candidates,
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {"max_pages_per_source": max_pages_per_source},
    }
    return write_basic_pack_files(
        run=run,
        pages=all_pages,
        result_filename="relationship.result.json",
        result_payload=result_payload,
        markdown_filename="RELATIONSHIPS.md",
        markdown_text=_relationship_markdown(contract),
        pack_filename="relationship.pack.json",
        extra_artifacts={
            "relationship_contract": artifact_ref(output_dir, contract_path),
        },
    )


def _normalize_source(item: str | dict[str, Any], *, index: int) -> dict[str, Any]:
    raw = {"value": item} if isinstance(item, str) else dict(item)
    value = str(raw.get("url") or raw.get("path") or raw.get("value") or "").strip()
    path = Path(value).expanduser()
    resolved_path = path.resolve() if value and path.exists() else None
    url: str | None = None
    if resolved_path is None:
        domain = domain_from_input(value)
        if domain:
            url = public_url(value if "://" in value else homepage_url_for_domain(domain))
    else:
        domain = None
    supplied_raw = raw.get("official_domains") or (
        [raw["official_domain"]] if raw.get("official_domain") else []
    )
    supplied_domains = [supplied_raw] if isinstance(supplied_raw, str) else list(supplied_raw)
    official_domains = sorted(
        {
            str(domain).lower().removeprefix("www.").rstrip(".")
            for domain in [*list(supplied_domains), domain]
            if domain
        }
    )
    name = str(raw.get("name") or "").strip()
    name_explicit = bool(name)
    if not name:
        name = resolved_path.stem if resolved_path else (domain or f"input-{index}")
    identity = {
        "name": name,
        "url": url,
        "path": str(resolved_path) if resolved_path else None,
        "location_scope": raw.get("location_scope"),
        "official_domains": official_domains,
    }
    return {
        **identity,
        "input_id": str(raw.get("input_id") or stable_id("input", identity)),
        "name_explicit": name_explicit,
    }


def _pages_for_source(
    spec: dict[str, Any],
    *,
    run: ContextPackRun,
    max_pages: int,
) -> list[PageSnapshot]:
    if spec.get("path"):
        pages = _pages_from_path(Path(str(spec["path"])))
        if pages and not spec.get("name_explicit"):
            entity_name = pages[0].metadata.get("entity_name")
            if isinstance(entity_name, str) and entity_name.strip():
                spec["name"] = entity_name.strip()
        return pages
    url = str(spec.get("url") or "")
    if not url:
        run.errors.append(
            {
                "code": "source_unavailable",
                "stage": "input",
                "error": "Relationship source has no usable HTTPS URL or local pack path.",
                "blocked": True,
            }
        )
        return []
    domain = domain_from_input(url)
    if not domain:
        run.errors.append(
            {"code": "invalid_source", "stage": "input", "error": f"Invalid source: {url}", "blocked": True}
        )
        return []
    allowed, reason = run.policy.allows_url(url)
    if not allowed:
        run.errors.append(
            {
                "code": "policy_denied",
                "stage": "policy",
                "url": url,
                "error": f"Source policy blocked relationship input: {reason}",
                "blocked": True,
            }
        )
        return []
    home = fetch_pages_blocking([url], run=run, max_pages=1)
    if not home or max_pages == 1:
        return home
    candidates = likely_internal_pages(home[0], domain, max_pages=max_pages)
    pages = fetch_pages_blocking(candidates, run=run, max_pages=max_pages)
    return pages or home


def _pages_from_path(path: Path) -> list[PageSnapshot]:
    records_path = path / "documents.ndjson" if path.is_dir() else path
    if not records_path.exists() or not records_path.is_file():
        return []
    pages: list[PageSnapshot] = []
    for line in records_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        content = str(record.get("content") or "")
        url = str(record.get("url") or "")
        if not content or not url:
            continue
        pages.append(
            PageSnapshot(
                url=url,
                title=str(record.get("title") or url),
                html="",
                markdown=content,
                metadata=dict(record.get("metadata") or {}),
                extraction=dict(record.get("extraction") or {}),
                source_type=str(record.get("source_type") or "relationship-source"),
            )
        )
    return pages


def _extract_relationship_candidates(
    spec: dict[str, Any],
    pages: list[PageSnapshot],
    *,
    citation_offset: int,
) -> list[dict[str, Any]]:
    subject = str(spec["name"])
    escaped = re.escape(subject)
    patterns = (
        (
            "owned_by",
            rf"\b{escaped}\b[^.!?\n]{{0,60}}?\b(?:is|was|became|remains)?\s*"
            rf"(?:wholly\s+)?owned by\s+(?P<object>[^.!?\n]{{2,100}})",
            False,
        ),
        (
            "operated_by",
            rf"\b{escaped}\b[^.!?\n]{{0,60}}?\b(?:is|was)?\s*operated by\s+(?P<object>[^.!?\n]{{2,100}})",
            False,
        ),
        (
            "acquired_by",
            rf"\b{escaped}\b[^.!?\n]{{0,60}}?\b(?:is|was)?\s*acquired by\s+(?P<object>[^.!?\n]{{2,100}})",
            False,
        ),
        (
            "franchised_by",
            rf"\b{escaped}\b[^.!?\n]{{0,60}}?\b(?:is|was)?\s*(?:a\s+)?"
            rf"franchis(?:e|ed) (?:of|by)\s+(?P<object>[^.!?\n]{{2,100}})",
            False,
        ),
        (
            "invested_in",
            rf"\b{escaped}\b[^.!?\n]{{0,40}}?\binvested in\s+(?P<object>[^.!?\n]{{2,100}})",
            False,
        ),
        (
            "invested_in",
            rf"(?P<object>[^.!?\n]{{2,100}}?)\s+(?:has\s+)?invested in\s+\b{escaped}\b",
            True,
        ),
        (
            "acquired_by",
            rf"(?P<object>[^.!?\n]{{2,100}}?)\s+acquired\s+\b{escaped}\b",
            False,
        ),
        (
            "operated_by",
            rf"(?P<object>[^.!?\n]{{2,100}}?)\s+operates\s+\b{escaped}\b",
            False,
        ),
    )
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for page_index, page in enumerate(pages, start=1):
        content = page.markdown
        for predicate, pattern, reverse in patterns:
            for match in re.finditer(pattern, content, flags=re.IGNORECASE):
                exact_text = _sentence_containing(content, match.start(), match.end())
                object_name = _clean_object_name(match.group("object"))
                if not object_name or object_name.casefold() == subject.casefold():
                    continue
                candidate_subject = object_name if reverse else subject
                candidate_object = subject if reverse else object_name
                key = (predicate, object_name.casefold(), hashlib.sha256(exact_text.encode()).hexdigest())
                if key in seen:
                    continue
                seen.add(key)
                citation_index = citation_offset + page_index
                evidence = evidence_span_payload(
                    url=page.url,
                    content=content,
                    exact_text=exact_text,
                    citation_id=f"S{citation_index}",
                    record_citation_id=f"S{citation_index}.1",
                )
                seed = {
                    "subject": candidate_subject,
                    "predicate": predicate,
                    "object": candidate_object,
                    "evidence": evidence,
                }
                candidate = RelationshipCandidate.model_validate(
                    {
                        "candidate_id": stable_id("relationship", seed),
                        "subject": {
                            "name": candidate_subject,
                            "location_scope": None if reverse else spec.get("location_scope"),
                        },
                        "predicate": predicate,
                        "object": {
                            "name": candidate_object,
                            "location_scope": spec.get("location_scope") if reverse else None,
                        },
                        "confidence": {
                            "owned_by": 0.86,
                            "acquired_by": 0.84,
                            "operated_by": 0.80,
                            "franchised_by": 0.78,
                            "invested_in": 0.76,
                        }[predicate],
                        "evidence": [evidence],
                        "warnings": ["Review candidate only; human approval is required before publication."],
                    }
                )
                output.append(candidate.model_dump(mode="json", exclude_none=True))
    return output


def _sentence_containing(content: str, start: int, end: int) -> str:
    sentence_start = max(content.rfind(".", 0, start), content.rfind("\n", 0, start)) + 1
    sentence_end_candidates = [
        index for index in (content.find(".", end), content.find("\n", end)) if index >= 0
    ]
    sentence_end = min(sentence_end_candidates) + 1 if sentence_end_candidates else len(content)
    return content[sentence_start:sentence_end].strip()


def _clean_object_name(value: str) -> str:
    cleaned = " ".join(value.split()).strip(" ,;:-")
    cleaned = re.split(
        r"\b(?:which|who|whose|after|before|when|while|according to|in \d{4})\b",
        cleaned,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip(" ,;:-")
    cleaned = re.sub(r"^(?:the\s+)", "", cleaned, flags=re.IGNORECASE)
    return cleaned[:160]


def _relationship_markdown(pack: RelationshipPack) -> str:
    lines = [
        "# Relationship Review Candidates",
        "",
        "All relationships are observations requiring human approval.",
        "Absence of a candidate is a coverage result, never an independence claim.",
        "",
        "## Coverage",
        "",
    ]
    for item in pack.coverage:
        lines.append(f"- `{item.input_id}`: **{item.status}** ({item.acquired_document_count} documents)")
    lines.extend(["", "## Candidates", ""])
    for candidate in pack.candidates:
        lines.append(
            f"- {candidate.subject.name} `{candidate.predicate}` {candidate.object.name} "
            f"(confidence {candidate.confidence:.2f})"
        )
    if not pack.candidates:
        lines.append("- No relationship candidates were found in acquired evidence.")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_RELATIONSHIP_OUTPUT_DIR",
    "RELATIONSHIP_WORKFLOW",
    "SUPPORTED_RELATIONSHIP_PREDICATES",
    "build_relationship_pack",
    "extract_relationship_candidates_from_records",
]
