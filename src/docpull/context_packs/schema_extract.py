"""Schema-shaped extraction grounded in local evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..basis import basis_record, write_basis
from ..parity import load_output_schema, validate_structured_output
from ..policy import PolicyConfig
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    artifact_ref,
    domain_from_input,
    ensure_policy_for_domain,
    fetch_pages_blocking,
    homepage_url_for_domain,
    public_url,
    quote_markdown,
    text_excerpt,
    write_basic_pack_files,
    write_json,
)

SCHEMA_WORKFLOW = "extract-schema"
DEFAULT_SCHEMA_OUTPUT_DIR = Path("packs/schema")
PRICE_RE = re.compile(
    r"(?P<currency>[$€£]|USD|EUR|GBP|CAD|AUD|JPY)\s*(?P<amount>\d+(?:,\d{3})*(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
AVAILABILITY_RE = re.compile(
    r"\b(in stock|out of stock|available|unavailable|sold out|backorder(?:ed)?|preorder)\b",
    re.IGNORECASE,
)


def extract_schema(
    url_or_pack: str | Path,
    *,
    schema_path: Path,
    output_dir: Path = DEFAULT_SCHEMA_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    fact_check: bool = False,
) -> dict[str, Any]:
    """Extract a JSON shape from URL or pack evidence without provider calls."""
    schema = load_output_schema(schema_path)
    output_dir = output_dir.resolve()
    pages, run = _pages_from_input(url_or_pack, policy=policy, output_dir=output_dir)
    if not pages:
        raise ContextPackError("No local evidence was available for schema extraction.")
    domain = domain_from_input(pages[0].url) or (urlparse(pages[0].url).hostname or "")
    run.policy = ensure_policy_for_domain(policy, domain) if domain else (policy or PolicyConfig())
    result, field_evidence = _deterministic_output(schema, pages)
    validation = validate_structured_output(result, schema)
    fact_check_payload = _fact_check_output(result, field_evidence, enabled=fact_check)
    if fact_check and not fact_check_payload["valid"]:
        validation = {
            **validation,
            "valid": False,
            "errors": list(validation.get("errors", [])) + fact_check_payload["errors"],
        }

    basis_records = _basis_records(result, field_evidence)
    basis_path = output_dir / "basis.ndjson"
    schema_out_path = output_dir / "structured.schema.json"
    validation_path = output_dir / "structured.validation.json"
    write_basis(basis_path, basis_records)
    write_json(schema_out_path, schema)
    write_json(validation_path, validation)
    result_payload = {
        "workflow": SCHEMA_WORKFLOW,
        "provider": "local",
        "status": "completed" if validation.get("valid") else "completed_with_validation_errors",
        "input": {"value": str(url_or_pack), "schema_path": str(schema_path), "fact_check": fact_check},
        "summary": {
            "basis_count": len(basis_records),
            "validation_valid": bool(validation.get("valid")),
            "fact_check_valid": bool(fact_check_payload.get("valid")),
        },
        "data": result,
        "validation": validation,
        "fact_check": fact_check_payload,
        "field_evidence": field_evidence,
        "basis": basis_records,
        "warnings": [],
        "errors": [],
        "replay_config": {
            "url_or_pack": str(url_or_pack),
            "schema_path": str(schema_path),
            "fact_check": fact_check,
        },
    }
    return write_basic_pack_files(
        run=run,
        pages=pages,
        result_filename="structured.result.json",
        result_payload=result_payload,
        markdown_filename="STRUCTURED.md",
        markdown_text=_structured_markdown(result_payload, pages),
        pack_filename="structured.pack.json",
        extra_artifacts={
            "schema": artifact_ref(output_dir, schema_out_path),
            "validation": artifact_ref(output_dir, validation_path),
            "basis": artifact_ref(output_dir, basis_path),
        },
    )


def _pages_from_input(
    url_or_pack: str | Path,
    *,
    policy: PolicyConfig | None,
    output_dir: Path,
) -> tuple[list[PageSnapshot], ContextPackRun]:
    path = Path(url_or_pack)
    run = ContextPackRun(
        workflow=SCHEMA_WORKFLOW,
        output_dir=output_dir,
        policy=policy or PolicyConfig(),
        input_value=str(url_or_pack),
    )
    if path.exists() and path.is_dir():
        return _pages_from_pack(path), run
    value = str(url_or_pack)
    domain = domain_from_input(value)
    if not domain:
        raise ContextPackError("extract-schema URL input must resolve to a domain.")
    run.policy = ensure_policy_for_domain(policy, domain)
    start_url = public_url(value if "://" in value else homepage_url_for_domain(domain))
    return fetch_pages_blocking([start_url], run=run, max_pages=1), run


def _pages_from_pack(pack_dir: Path) -> list[PageSnapshot]:
    records_path = pack_dir / "documents.ndjson"
    if not records_path.exists():
        raise ContextPackError(f"Pack has no documents.ndjson: {pack_dir}")
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
        metadata_raw = record.get("metadata")
        extraction_raw = record.get("extraction")
        metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
        extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
        pages.append(
            PageSnapshot(
                url=public_url(url),
                title=str(record.get("title") or "") or None,
                html="",
                markdown=content,
                metadata=metadata,
                extraction=extraction,
                source_type=str(record.get("source_type") or "pack_record"),
            )
        )
    return pages


def _deterministic_output(
    schema: dict[str, Any],
    pages: list[PageSnapshot],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    output: dict[str, Any] = {}
    field_evidence: dict[str, list[dict[str, Any]]] = {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return output, field_evidence
    context_text = "\n\n".join(page.markdown for page in pages)
    first = pages[0]
    summary = text_excerpt(context_text, limit=500)
    for name, prop_schema in properties.items():
        if not isinstance(prop_schema, dict):
            continue
        value = _field_value(name, prop_schema, first, summary, pages)
        output[name] = value
        if _is_non_null_scalar(value):
            field_evidence[name] = [
                {
                    "citation_id": "S1",
                    "url": first.url,
                    "title": first.title,
                    "excerpt": text_excerpt(first.markdown, str(value), limit=260),
                }
            ]
    return output, field_evidence


def _field_value(
    name: str,
    prop_schema: dict[str, Any],
    first: PageSnapshot,
    summary: str,
    pages: list[PageSnapshot],
) -> Any:
    field = name.lower()
    expected_type = prop_schema.get("type")
    if field in {"summary", "description", "answer"}:
        return summary
    if field in {"title", "name"}:
        return first.title or _first_heading(first) or None
    if field == "url":
        return first.url
    if field == "domain":
        return urlparse(first.url).hostname
    if field in {"price", "amount", "cost"}:
        return _price_value(prop_schema, pages)
    if field in {"currency", "currency_code"}:
        return _currency_value(pages)
    if field in {"availability", "stock", "stock_status"}:
        return _availability_value(pages)
    if field in {"citations", "sources"}:
        return [
            {
                "citation_id": f"S{index}",
                "url": page.url,
                "title": page.title or page.url,
            }
            for index, page in enumerate(pages, start=1)
        ]
    if expected_type == "array":
        return []
    if expected_type == "object":
        return {}
    return None


def _price_value(prop_schema: dict[str, Any], pages: list[PageSnapshot]) -> str | float | None:
    expected_type = prop_schema.get("type")
    for page in pages:
        match = PRICE_RE.search(page.markdown)
        if not match:
            continue
        raw = f"{match.group('currency')}{match.group('amount')}"
        if expected_type in {"number", "integer"}:
            try:
                return float(match.group("amount").replace(",", ""))
            except ValueError:
                return None
        return raw
    return None


def _currency_value(pages: list[PageSnapshot]) -> str | None:
    for page in pages:
        match = PRICE_RE.search(page.markdown)
        if match:
            return _currency_code(match.group("currency"))
    return None


def _currency_code(value: str) -> str:
    normalized = value.upper()
    return {"$": "USD", "€": "EUR", "£": "GBP"}.get(normalized, normalized)


def _availability_value(pages: list[PageSnapshot]) -> str | None:
    for page in pages:
        for line in page.markdown.splitlines():
            match = AVAILABILITY_RE.search(line)
            if match:
                return " ".join(line.split())[:200]
    return None


def _fact_check_output(
    payload: dict[str, Any],
    field_evidence: dict[str, list[dict[str, Any]]],
    *,
    enabled: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    if enabled:
        for path, value in _scalar_paths(payload):
            if value is None:
                continue
            if path not in field_evidence:
                errors.append(f"{path}: non-null scalar has no cited evidence")
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "enabled": enabled,
        "valid": not errors,
        "errors": errors,
        "checked_scalar_count": len(list(_scalar_paths(payload))) if enabled else 0,
    }


def _scalar_paths(value: Any, prefix: str = "$") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        output: list[tuple[str, Any]] = []
        for key, item in value.items():
            output.extend(_scalar_paths(item, f"{prefix}.{key}"))
        return output
    if isinstance(value, list):
        output = []
        for index, item in enumerate(value):
            output.extend(_scalar_paths(item, f"{prefix}[{index}]"))
        return output
    return [(prefix.removeprefix("$."), value)]


def _is_non_null_scalar(value: Any) -> bool:
    return value is not None and not isinstance(value, dict | list)


def _basis_records(
    payload: dict[str, Any],
    field_evidence: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path, value in _scalar_paths(payload):
        evidence = field_evidence.get(path) or []
        citation_ids = [str(item.get("citation_id")) for item in evidence if item.get("citation_id")]
        source_urls = [str(item.get("url")) for item in evidence if item.get("url")]
        excerpts = [
            {
                "citation_id": item.get("citation_id"),
                "source_url": item.get("url"),
                "text": item.get("excerpt"),
            }
            for item in evidence
            if item.get("excerpt")
        ]
        if value is None:
            state = "insufficient"
            confidence = "low"
            warnings = ["field value is null because no local evidence matched"]
        elif evidence:
            state = "supported"
            confidence = "medium"
            warnings = []
        else:
            state = "partial"
            confidence = "low"
            warnings = ["field value has no cited evidence"]
        records.append(
            basis_record(
                claim_path=f"data.{path}",
                claim=f"{path} = {value!r}",
                citation_ids=citation_ids,
                source_urls=source_urls,
                excerpts=excerpts,
                confidence=confidence,  # type: ignore[arg-type]
                evidence_state=state,  # type: ignore[arg-type]
                warnings=warnings,
                producer="docpull.extract-schema",
            )
        )
    if not records:
        records.append(
            basis_record(
                claim_path="data",
                claim="Structured extraction produced no scalar fields.",
                confidence="low",
                evidence_state="insufficient",
                warnings=["schema produced no scalar fields"],
                producer="docpull.extract-schema",
            )
        )
    return records


def _first_heading(page: PageSnapshot) -> str | None:
    for line in page.markdown.splitlines():
        stripped = line.strip("# ").strip()
        if stripped:
            return stripped
    return None


def _structured_markdown(payload: dict[str, Any], pages: list[PageSnapshot]) -> str:
    lines = ["# Structured Extraction", ""]
    lines.append(f"- Status: `{payload.get('status')}`")
    summary_raw = payload.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    lines.append(f"- Validation: `{summary.get('validation_valid')}`")
    lines.append("")
    lines.append("## Data")
    lines.append("```json")
    lines.append(json.dumps(payload.get("data"), indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")
    lines.append("## Evidence")
    for index, page in enumerate(pages, start=1):
        lines.append(f"- [S{index}] [{quote_markdown(page.title or page.url)}]({page.url})")
    return "\n".join(lines)
