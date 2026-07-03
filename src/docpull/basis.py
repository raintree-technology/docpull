"""Shared local evidence basis records."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from .pack_reader import PackReadError, load_pack
from .time_utils import utc_now_iso

BasisConfidence = Literal["high", "medium", "low"]
EvidenceState = Literal["supported", "partial", "insufficient"]
BASIS_SCHEMA_VERSION = 2
DEFAULT_MIN_SUPPORTED_RATIO = 0.80


class BasisError(RuntimeError):
    """Raised when basis artifacts cannot be read or written."""


def basis_record(
    *,
    claim_path: str,
    claim: str,
    citation_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
    excerpts: list[dict[str, Any]] | None = None,
    excerpt: str | None = None,
    confidence: BasisConfidence = "medium",
    evidence_state: EvidenceState | None = None,
    warnings: list[str] | None = None,
    producer: str = "docpull.basis",
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build one v2 evidence basis record."""

    normalized_citations = _string_list(citation_ids)
    normalized_urls = _string_list(source_urls)
    normalized_excerpts = _normalize_excerpts(excerpts, fallback_text=excerpt)
    state = evidence_state or _evidence_state(
        citation_ids=normalized_citations,
        source_urls=normalized_urls,
        excerpts=normalized_excerpts,
    )
    record = {
        "schema_version": BASIS_SCHEMA_VERSION,
        "basis_id": _basis_id(
            claim_path=claim_path,
            claim=claim,
            citation_ids=normalized_citations,
            source_urls=normalized_urls,
            excerpts=normalized_excerpts,
            producer=producer,
        ),
        "generated_at": generated_at or utc_now_iso(),
        "claim_path": str(claim_path or "claim"),
        "claim": str(claim or ""),
        "evidence_state": state,
        "confidence": confidence,
        "citation_ids": normalized_citations,
        "source_urls": normalized_urls,
        "excerpts": normalized_excerpts,
        "warnings": _string_list(warnings),
        "producer": producer,
    }
    return normalize_basis_record(record)


def normalize_basis_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return a v2 basis record, upgrading older ad hoc shapes when possible."""

    schema_version = record.get("schema_version")
    claim_path = str(record.get("claim_path") or record.get("field") or record.get("path") or "claim")
    claim = str(record.get("claim") or record.get("title") or record.get("url") or "")
    citation_ids = _string_list(record.get("citation_ids"))
    if not citation_ids and record.get("citation_id"):
        citation_ids = [str(record["citation_id"])]
    source_urls = _string_list(record.get("source_urls"))
    if not source_urls and record.get("url"):
        source_urls = [str(record["url"])]
    excerpts = _normalize_excerpts(record.get("excerpts"), fallback_text=record.get("excerpt"))
    warnings = _string_list(record.get("warnings"))
    if schema_version != BASIS_SCHEMA_VERSION:
        warnings.append("basis record was normalized from a legacy shape")
    evidence_state = str(record.get("evidence_state") or "").strip()
    if evidence_state not in {"supported", "partial", "insufficient"}:
        evidence_state = _evidence_state(
            citation_ids=citation_ids,
            source_urls=source_urls,
            excerpts=excerpts,
        )
    confidence = str(record.get("confidence") or "").strip()
    if confidence not in {"high", "medium", "low"}:
        confidence = "low" if evidence_state == "insufficient" else "medium"
    producer = str(record.get("producer") or "docpull.basis")
    return {
        "schema_version": BASIS_SCHEMA_VERSION,
        "basis_id": str(
            record.get("basis_id")
            or _basis_id(
                claim_path=claim_path,
                claim=claim,
                citation_ids=citation_ids,
                source_urls=source_urls,
                excerpts=excerpts,
                producer=producer,
            )
        ),
        "generated_at": str(record.get("generated_at") or utc_now_iso()),
        "claim_path": claim_path,
        "claim": claim,
        "evidence_state": evidence_state,
        "confidence": confidence,
        "citation_ids": citation_ids,
        "source_urls": source_urls,
        "excerpts": excerpts,
        "warnings": warnings,
        "producer": producer,
    }


def write_basis(
    path: Path,
    records: list[dict[str, Any]],
    *,
    min_supported_ratio: float = DEFAULT_MIN_SUPPORTED_RATIO,
) -> dict[str, Any]:
    """Write normalized v2 basis records as deterministic JSONL plus a report."""

    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = [normalize_basis_record(record) for record in records]
    lines = "".join(json.dumps(record, sort_keys=True) + "\n" for record in normalized)
    path.write_text(lines, encoding="utf-8")
    report = basis_report(normalized, path=path, min_supported_ratio=min_supported_ratio)
    report_path = path.with_name("basis.report.json")
    markdown_path = path.with_name("BASIS.md")
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(basis_markdown(report, normalized), encoding="utf-8")
    return report


def read_basis(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise BasisError(f"Invalid basis NDJSON in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise BasisError(f"Invalid basis NDJSON in {path} line {index}: expected object")
        records.append(normalize_basis_record(value))
    return records


def basis_report(
    records: list[dict[str, Any]],
    *,
    path: Path | None = None,
    min_supported_ratio: float = DEFAULT_MIN_SUPPORTED_RATIO,
) -> dict[str, Any]:
    normalized = [normalize_basis_record(record) for record in records]
    count = len(normalized)
    supported_count = sum(1 for record in normalized if record["evidence_state"] == "supported")
    partial_count = sum(1 for record in normalized if record["evidence_state"] == "partial")
    insufficient_count = sum(1 for record in normalized if record["evidence_state"] == "insufficient")
    cited_count = sum(1 for record in normalized if record["citation_ids"])
    low_confidence_count = sum(1 for record in normalized if record["confidence"] == "low")
    warning_count = sum(len(record.get("warnings") or []) for record in normalized)
    supported_ratio = supported_count / count if count else 0.0
    citation_coverage = cited_count / count if count else 0.0
    issues: list[str] = []
    if count == 0:
        issues.append("basis.ndjson is missing or empty")
    if supported_ratio < min_supported_ratio:
        issues.append("supported evidence ratio is below threshold")
    if low_confidence_count:
        issues.append("basis contains low-confidence records")
    if insufficient_count:
        issues.append("basis contains insufficient-evidence records")
    return {
        "schema_version": BASIS_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "path": str(path) if path else None,
        "summary": {
            "basis_count": count,
            "supported_count": supported_count,
            "partial_count": partial_count,
            "insufficient_count": insufficient_count,
            "supported_ratio": round(supported_ratio, 6),
            "citation_coverage": round(citation_coverage, 6),
            "low_confidence_count": low_confidence_count,
            "warning_count": warning_count,
            "min_supported_ratio": min_supported_ratio,
        },
        "passed": not issues,
        "issues": issues,
        "artifacts": {
            "basis": path.name if path else "basis.ndjson",
            "report": "basis.report.json",
            "markdown": "BASIS.md",
        },
    }


def basis_markdown(report: dict[str, Any], records: list[dict[str, Any]]) -> str:
    summary_raw = report.get("summary")
    summary: dict[str, Any] = summary_raw if isinstance(summary_raw, dict) else {}
    lines = [
        "# Evidence Basis",
        "",
        f"- Records: `{summary.get('basis_count', 0)}`",
        f"- Supported ratio: `{summary.get('supported_ratio', 0)}`",
        f"- Citation coverage: `{summary.get('citation_coverage', 0)}`",
        f"- Low-confidence records: `{summary.get('low_confidence_count', 0)}`",
        f"- Insufficient records: `{summary.get('insufficient_count', 0)}`",
        "",
        (
            "Agents should make only claims with supported evidence. If evidence is partial "
            "or insufficient, refuse or ask for fresher context."
        ),
        "",
        "## Claims",
        "",
    ]
    for record in records[:50]:
        excerpts = record.get("excerpts") if isinstance(record.get("excerpts"), list) else []
        first_excerpt = excerpts[0] if excerpts and isinstance(excerpts[0], dict) else {}
        text = _truncate(str(first_excerpt.get("text") or ""), 180)
        citation = ", ".join(record.get("citation_ids") or []) or "uncited"
        lines.append(
            f"- **{record.get('evidence_state')}** `{record.get('claim_path')}` "
            f"({record.get('confidence')}, {citation}): {_truncate(str(record.get('claim') or ''), 140)}"
        )
        if text:
            lines.append(f"  Evidence: {text}")
    return "\n".join(lines).rstrip() + "\n"


def build_pack_basis(
    pack_dir: Path,
    *,
    claim_path: str,
    claim: str,
    limit: int = 5,
    producer: str = "docpull.pack.basis",
) -> list[dict[str, Any]]:
    """Build basis records for a claim from local pack search results."""

    try:
        pack = load_pack(pack_dir)
    except PackReadError as err:
        return [
            basis_record(
                claim_path=claim_path,
                claim=claim,
                confidence="low",
                evidence_state="insufficient",
                warnings=[str(err)],
                producer=producer,
            )
        ]
    if not pack.documents:
        return [
            basis_record(
                claim_path=claim_path,
                claim=claim,
                confidence="low",
                evidence_state="insufficient",
                warnings=["pack has no documents"],
                producer=producer,
            )
        ]
    search_limit = max(1, limit)
    try:
        search = pack.search_payload(claim or claim_path, limit=search_limit)
        results = search.get("results") if isinstance(search.get("results"), list) else []
    except PackReadError:
        results = []
    if not results:
        return [
            basis_record(
                claim_path=claim_path,
                claim=claim,
                confidence="low",
                evidence_state="insufficient",
                warnings=["no local pack document matched the claim"],
                producer=producer,
            )
        ]
    records: list[dict[str, Any]] = []
    for result in results[:search_limit]:
        citation_id = str(result.get("citation_id") or "")
        record_citation_id = str(result.get("record_citation_id") or "")
        url = str(result.get("url") or "")
        excerpt_text = str(result.get("excerpt") or "")
        state: EvidenceState = "supported" if citation_id and excerpt_text else "partial"
        records.append(
            basis_record(
                claim_path=claim_path,
                claim=claim,
                citation_ids=[citation_id] if citation_id else [],
                source_urls=[url] if url else [],
                excerpts=[
                    {
                        "citation_id": citation_id or None,
                        "record_citation_id": record_citation_id or None,
                        "source_url": url or None,
                        "text": _truncate(excerpt_text, 600),
                    }
                ],
                confidence="medium" if state == "supported" else "low",
                evidence_state=state,
                warnings=[] if state == "supported" else ["search result lacks citation or excerpt"],
                producer=producer,
            )
        )
    return records


def _basis_id(
    *,
    claim_path: str,
    claim: str,
    citation_ids: list[str],
    source_urls: list[str],
    excerpts: list[dict[str, Any]],
    producer: str,
) -> str:
    excerpt_text = "\n".join(str(item.get("text") or "") for item in excerpts)
    seed = json.dumps(
        {
            "claim_path": claim_path,
            "claim": claim,
            "citation_ids": citation_ids,
            "source_urls": source_urls,
            "excerpt_text": excerpt_text,
            "producer": producer,
        },
        sort_keys=True,
    )
    return "basis_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _evidence_state(
    *,
    citation_ids: list[str],
    source_urls: list[str],
    excerpts: list[dict[str, Any]],
) -> EvidenceState:
    has_excerpt = any(str(item.get("text") or "").strip() for item in excerpts)
    if citation_ids and source_urls and has_excerpt:
        return "supported"
    if citation_ids or source_urls or has_excerpt:
        return "partial"
    return "insufficient"


def _normalize_excerpts(value: Any, *, fallback_text: Any = None) -> list[dict[str, Any]]:
    excerpts: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("excerpt") or "").strip()
                if not text:
                    continue
                excerpts.append(
                    {
                        "citation_id": str(item.get("citation_id")) if item.get("citation_id") else None,
                        "record_citation_id": (
                            str(item.get("record_citation_id")) if item.get("record_citation_id") else None
                        ),
                        "source_url": str(item.get("source_url") or item.get("url") or "") or None,
                        "text": _truncate(text, 1000),
                    }
                )
            elif str(item).strip():
                excerpts.append({"citation_id": None, "source_url": None, "text": _truncate(str(item), 1000)})
    elif isinstance(value, dict):
        text = str(value.get("text") or value.get("excerpt") or "").strip()
        if text:
            excerpts.append(
                {
                    "citation_id": str(value.get("citation_id")) if value.get("citation_id") else None,
                    "record_citation_id": (
                        str(value.get("record_citation_id")) if value.get("record_citation_id") else None
                    ),
                    "source_url": str(value.get("source_url") or value.get("url") or "") or None,
                    "text": _truncate(text, 1000),
                }
            )
    if not excerpts and fallback_text is not None and str(fallback_text).strip():
        excerpts.append(
            {"citation_id": None, "source_url": None, "text": _truncate(str(fallback_text), 1000)}
        )
    return excerpts


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, list | tuple | set):
        output: list[str] = []
        for item in value:
            text = str(item).strip()
            if text:
                output.append(text)
        return output
    text = str(value).strip()
    return [text] if text else []


def _truncate(value: str, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
