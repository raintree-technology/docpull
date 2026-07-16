"""DocPull output contract v3 helpers and validation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from .time_utils import utc_now_iso

OUTPUT_CONTRACT_SCHEMA_VERSION = 3
RAW_REQUIRED_ARTIFACTS = (
    "corpus.manifest.json",
    "sources.md",
    "acquisition.routes.json",
)
AGENT_REQUIRED_ARTIFACTS = (
    *RAW_REQUIRED_ARTIFACTS,
    "context.lock.json",
    "coverage.report.json",
    "citation.index.json",
    "pack.score.json",
    "pack.audit.json",
)
EVAL_REQUIRED_ARTIFACTS = (
    *AGENT_REQUIRED_ARTIFACTS,
    "rights.manifest.json",
    "provenance.graph.json",
    "basis.ndjson",
    "basis.report.json",
    "PACK_CARD.md",
)
VALIDATION_LEVELS = ("raw", "agent", "eval")
ValidationLevel = Literal["raw", "agent", "eval"]


class OutputContractError(RuntimeError):
    """Raised when output contract validation cannot run."""


def default_rights_state() -> dict[str, Any]:
    """Return the conservative rights state for generated document records."""
    return {
        "status": "unknown",
        "allowed_use": {
            "internal_indexing": "unknown",
            "redistribution": "unknown",
            "model_training": "unknown",
            "eval_generation": "unknown",
        },
        "obligations": [],
        "basis": "conservative_default",
    }


def document_context_fields(ctx: Any, *, output_format: str) -> dict[str, Any]:
    """Build v3 record fields from a pipeline page context."""
    content_type = str(getattr(ctx, "content_type", "") or "text/markdown").strip() or "text/markdown"
    rendered_at = _first_string(
        _dict_get(getattr(ctx, "metadata", None), "rendered_at"),
        _dict_get(getattr(ctx, "extraction_info", None), "rendered_at"),
        _dict_get(_dict_get(getattr(ctx, "metadata", None), "render"), "rendered_at"),
    )
    return {
        "content_type": content_type,
        "mime_type": content_type_base(content_type) or "text/markdown",
        "rendered_at": rendered_at,
        "route": {
            "name": "local-fetch",
            "output_format": output_format,
            "status_code": getattr(ctx, "status_code", None),
            "bytes_downloaded": getattr(ctx, "bytes_downloaded", None),
        },
        "rights": default_rights_state(),
    }


def content_type_base(value: str | None) -> str | None:
    """Return a lower-case media type without parameters."""
    if not value:
        return None
    base = str(value).split(";", 1)[0].strip().lower()
    return base or None


def record_key(record: Any) -> str:
    """Return the stable key for a record or record-like mapping."""
    if isinstance(record, dict):
        return str(record.get("chunk_id") or record.get("document_id") or record.get("url") or "")
    return str(getattr(record, "chunk_id", None) or getattr(record, "document_id", None) or "")


def write_raw_contract_sidecars(
    base_dir: Path,
    *,
    manifest_payload: dict[str, Any],
    output_format: str,
) -> dict[str, Path]:
    """Write raw-level v3 sidecars shared by all file-backed sinks."""
    base_dir.mkdir(parents=True, exist_ok=True)
    records = [item for item in manifest_payload.get("records", []) if isinstance(item, dict)]
    artifacts: dict[str, Path] = {}
    sources_path = base_dir / "sources.md"
    if not sources_path.exists():
        sources_path.write_text(_sources_markdown(records, output_format=output_format), encoding="utf-8")
    artifacts["sources"] = sources_path

    acquisition_path = base_dir / "acquisition.routes.json"
    if not acquisition_path.exists():
        acquisition_path.write_text(
            json.dumps(_acquisition_routes(records, output_format=output_format), indent=2) + "\n",
            encoding="utf-8",
        )
    artifacts["acquisition_routes"] = acquisition_path
    return artifacts


def ensure_agent_contract_sidecars(
    pack_dir: Path,
    *,
    workflow: str = "pack-prepare",
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    """Write minimal agent-level sidecars when a local pack lacks them."""
    pack_dir = pack_dir.resolve()
    records = records if records is not None else _read_records(pack_dir)
    artifacts: dict[str, Path] = {}

    context_lock = pack_dir / "context.lock.json"
    if not context_lock.exists():
        payload = {
            "schema_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "workflow": workflow,
            "output_contract_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
            "record_count": len(records),
            "sources": _source_summary(records),
        }
        _write_json(context_lock, payload)
    artifacts["context_lock"] = context_lock

    coverage = pack_dir / "coverage.report.json"
    if not coverage.exists():
        unique_urls = sorted({str(record.get("url") or "") for record in records if record.get("url")})
        payload = {
            "schema_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "summary": {
                "coverage_confidence": _coverage_confidence(records, unique_urls),
                "discovered_url_count": len(unique_urls),
                "selected_url_count": len(unique_urls),
                "extracted_doc_count": len(records),
                "blocked_by_robots": 0,
            },
            "routes": [{"route": "pack-records", "fetched_count": len(records), "skip_counts": {}}],
            "recommendations": [],
        }
        _write_json(coverage, payload)
    artifacts["coverage_report"] = coverage

    listing_items = _listing_items(records)
    if listing_items:
        listing_path = pack_dir / "listing.items.ndjson"
        if not listing_path.exists():
            listing_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in listing_items) + "\n",
                encoding="utf-8",
            )
        artifacts["listing_items"] = listing_path
    return artifacts


def _coverage_confidence(records: list[dict[str, Any]], unique_urls: list[str]) -> str:
    if not records:
        return "low"
    typed_records = 0
    cited_records = 0
    for record in records:
        metadata = _dict_value(record.get("metadata"))
        if metadata.get("item_citation_id"):
            typed_records += 1
        if record.get("source_citation_id") and record.get("record_citation_id"):
            cited_records += 1
    if typed_records == len(records) and cited_records == len(records):
        return "high"
    if len(unique_urls) <= 1:
        return "single-source"
    return "medium"


def validate_pack_contract(
    pack_dir: Path | str,
    *,
    level: ValidationLevel = "raw",
) -> dict[str, Any]:
    """Validate a local pack against the DocPull output contract v3."""
    if level not in VALIDATION_LEVELS:
        raise OutputContractError(f"Unsupported validation level: {level}")
    root = Path(pack_dir).expanduser().resolve()
    issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not root.exists() or not root.is_dir():
        raise OutputContractError(f"Pack directory does not exist: {root}")

    required = _required_artifacts(level)
    for filename in required:
        path = root / filename
        if not path.exists():
            issues.append(
                _issue(
                    "missing_artifact",
                    f"Missing required {level} artifact: {filename}",
                    path=filename,
                )
            )

    manifest = _read_json(root / "corpus.manifest.json", required=False)
    manifest_records: list[dict[str, Any]] = []
    if isinstance(manifest, dict):
        manifest_version = manifest.get("schema_version")
        if manifest_version != OUTPUT_CONTRACT_SCHEMA_VERSION:
            issues.append(
                _issue(
                    "manifest_schema_version",
                    "corpus.manifest.json must use schema_version 3 for output contract v3.",
                    path="corpus.manifest.json",
                    details={"actual": manifest_version},
                )
            )
        manifest_records = [item for item in manifest.get("records", []) if isinstance(item, dict)]
    else:
        issues.append(_issue("invalid_manifest", "corpus.manifest.json is missing or invalid JSON."))

    documents: list[Any] = []
    load_error: str | None = None
    try:
        from .pack_reader import load_pack

        pack = load_pack(root)
        documents = list(pack.documents)
        citation_payload = pack.citations_payload()
    except Exception as err:  # noqa: BLE001
        load_error = str(err)
        citation_payload = {"sources": []}
        issues.append(_issue("pack_load_failed", f"Pack could not be loaded: {load_error}"))

    manifest_count = _optional_int(manifest.get("record_count") if isinstance(manifest, dict) else None)
    if manifest_count is not None and documents and manifest_count != len(documents):
        issues.append(
            _issue(
                "record_count_mismatch",
                "Manifest record_count does not match loaded documents.",
                details={"manifest": manifest_count, "loaded": len(documents)},
            )
        )

    for index, document in enumerate(documents, start=1):
        _validate_document(document, index=index, issues=issues, warnings=warnings)

    if documents and not _has_precise_record_citations(citation_payload):
        issues.append(
            _issue(
                "missing_record_citations",
                "Citation payload must include record-level citations such as S1.1.",
            )
        )

    if level in {"agent", "eval"}:
        _validate_agent_sidecars(root, issues=issues, warnings=warnings)
    if level == "eval":
        _validate_eval_sidecars(root, issues=issues, warnings=warnings)

    return {
        "schema_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(root),
        "level": level,
        "status": "pass" if not issues else "fail",
        "summary": {
            "required_artifact_count": len(required),
            "missing_artifact_count": sum(1 for issue in issues if issue["code"] == "missing_artifact"),
            "manifest_record_count": len(manifest_records),
            "loaded_record_count": len(documents),
            "issue_count": len(issues),
            "warning_count": len(warnings),
        },
        "issues": issues,
        "warnings": warnings,
    }


def validation_report_text(payload: dict[str, Any]) -> str:
    """Render a concise text validation report."""
    lines = [
        f"DocPull output contract v{payload.get('schema_version')} validation",
        f"Status: {payload.get('status')}",
        f"Level: {payload.get('level')}",
        f"Pack: {payload.get('pack_dir')}",
        "",
    ]
    issues = payload.get("issues") if isinstance(payload.get("issues"), list) else []
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    if issues:
        lines.append("Issues:")
        lines.extend(
            f"- {item.get('code')}: {item.get('message')}" for item in issues if isinstance(item, dict)
        )
    if warnings:
        if issues:
            lines.append("")
        lines.append("Warnings:")
        lines.extend(
            f"- {item.get('code')}: {item.get('message')}" for item in warnings if isinstance(item, dict)
        )
    if not issues and not warnings:
        lines.append("No issues found.")
    return "\n".join(lines).rstrip() + "\n"


def _validate_document(
    document: Any,
    *,
    index: int,
    issues: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    prefix = f"document[{index}]"
    required_strings = {
        "document_id": getattr(document, "document_id", None),
        "url": getattr(document, "url", None),
        "title": getattr(document, "title", None),
        "content_hash": getattr(document, "content_hash", None),
        "fetched_at": getattr(document, "fetched_at", None),
        "content_type": getattr(document, "content_type", None),
        "mime_type": getattr(document, "mime_type", None),
    }
    for key, value in required_strings.items():
        if not isinstance(value, str) or not value.strip():
            issues.append(_issue("missing_record_field", f"{prefix} is missing required field {key}."))
    if getattr(document, "schema_version", None) != OUTPUT_CONTRACT_SCHEMA_VERSION:
        issues.append(
            _issue(
                "record_schema_version",
                f"{prefix} must use schema_version 3.",
                details={"actual": getattr(document, "schema_version", None)},
            )
        )
    if getattr(document, "token_count", None) is None:
        issues.append(_issue("missing_record_field", f"{prefix} is missing required field token_count."))
    route = getattr(document, "route", None)
    if not isinstance(route, dict) or not route.get("name"):
        issues.append(_issue("missing_route_metadata", f"{prefix} is missing route metadata."))
    rights = getattr(document, "rights", None)
    if not isinstance(rights, dict) or not rights.get("status"):
        issues.append(_issue("missing_rights_state", f"{prefix} is missing conservative rights state."))
    if getattr(document, "chunk_index", None) is not None and not getattr(document, "chunk_id", None):
        issues.append(_issue("missing_chunk_id", f"{prefix} has chunk_index but no chunk_id."))
    if not str(getattr(document, "content", "") or "").strip():
        warnings.append(_issue("empty_content", f"{prefix} has empty content."))


def _validate_agent_sidecars(
    pack_dir: Path,
    *,
    issues: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    citation_index = _read_json(pack_dir / "citation.index.json", required=False)
    if isinstance(citation_index, dict):
        entries = _list_value(citation_index.get("entries"))
        missing_precise = [
            entry
            for entry in entries
            if isinstance(entry, dict) and not str(entry.get("record_citation_id") or "").strip()
        ]
        if missing_precise:
            issues.append(
                _issue(
                    "citation_index_missing_record_ids",
                    "citation.index.json entries must include record_citation_id.",
                    path="citation.index.json",
                )
            )
    elif (pack_dir / "citation.index.json").exists():
        issues.append(_issue("invalid_json", "citation.index.json is invalid.", path="citation.index.json"))

    coverage = _read_json(pack_dir / "coverage.report.json", required=False)
    if isinstance(coverage, dict):
        summary = coverage.get("summary")
        if not isinstance(summary, dict) or not summary.get("coverage_confidence"):
            warnings.append(
                _issue(
                    "coverage_confidence_unknown",
                    "coverage.report.json has no coverage_confidence summary.",
                    path="coverage.report.json",
                )
            )


def _validate_eval_sidecars(
    pack_dir: Path,
    *,
    issues: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    rights = _read_json(pack_dir / "rights.manifest.json", required=False)
    if isinstance(rights, dict):
        allowed_use = rights.get("allowed_use")
        if not isinstance(allowed_use, dict):
            issues.append(
                _issue("rights_allowed_use_missing", "rights.manifest.json must include allowed_use.")
            )
    elif (pack_dir / "rights.manifest.json").exists():
        issues.append(_issue("invalid_json", "rights.manifest.json is invalid.", path="rights.manifest.json"))


def _required_artifacts(level: ValidationLevel) -> tuple[str, ...]:
    if level == "raw":
        return RAW_REQUIRED_ARTIFACTS
    if level == "agent":
        return AGENT_REQUIRED_ARTIFACTS
    return EVAL_REQUIRED_ARTIFACTS


def _sources_markdown(records: list[dict[str, Any]], *, output_format: str) -> str:
    lines = []
    if output_format == "okf":
        lines.extend(["---", 'type: "Source Index"', 'title: "Context Pack Sources"', "---", ""])
    lines.extend(
        [
            "# Context Pack Sources",
            "",
            f"Output format: `{output_format}`.",
            "",
            "## Sources",
            "",
        ]
    )
    by_url: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        url = str(record.get("url") or "")
        if url:
            by_url.setdefault(url, []).append(record)
    if not by_url:
        lines.append("_No records were emitted._")
    for index, (url, url_records) in enumerate(by_url.items(), start=1):
        title = str(url_records[0].get("title") or url)
        token_count = sum(_safe_int(record.get("token_count")) for record in url_records)
        lines.append(f"{index}. [{title}]({url})")
        lines.append(f"   - Records: {len(url_records)}")
        if token_count:
            lines.append(f"   - Tokens: {token_count}")
        output_paths = sorted(
            {str(record.get("output_path") or "") for record in url_records if record.get("output_path")}
        )
        for output_path in output_paths[:5]:
            lines.append(f"   - Records file: `{output_path}`")
    return "\n".join(lines).rstrip() + "\n"


def _acquisition_routes(records: list[dict[str, Any]], *, output_format: str) -> dict[str, Any]:
    domains = Counter(_domain(str(record.get("url") or "")) for record in records if record.get("url"))
    return {
        "schema_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "output_contract_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
        "routes": [
            {
                "route": "local-fetch",
                "output_format": output_format,
                "fetched_count": len(records),
                "record_count": len(records),
                "domain_count": len(domains),
                "skip_counts": {"robots_disallowed": 0},
            }
        ],
        "domains": dict(domains.most_common()),
    }


def _read_records(pack_dir: Path) -> list[dict[str, Any]]:
    ndjson = pack_dir / "documents.ndjson"
    if ndjson.exists():
        records: list[dict[str, Any]] = []
        for line in ndjson.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if isinstance(item, dict):
                    records.append(item)
        return records
    try:
        from .pack_reader import load_pack

        pack = load_pack(pack_dir)
    except Exception:  # noqa: BLE001
        pack = None
    if pack is not None:
        return [record.model_dump(mode="json", exclude_none=True) for record in pack.documents]
    manifest = _read_json(pack_dir / "corpus.manifest.json", required=False)
    if isinstance(manifest, dict) and isinstance(manifest.get("records"), list):
        return [item for item in manifest["records"] if isinstance(item, dict)]
    return []


def _source_summary(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        url = str(record.get("url") or "")
        if url:
            by_url.setdefault(url, []).append(record)
    return [
        {
            "url": url,
            "domain": _domain(url),
            "record_count": len(url_records),
            "content_hashes": sorted(
                {
                    str(record.get("content_hash") or "")
                    for record in url_records
                    if record.get("content_hash")
                }
            ),
        }
        for url, url_records in sorted(by_url.items())
    ]


def _listing_items(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    item_index = 1
    for record in records:
        content = str(record.get("content") or "")
        if not content:
            continue
        candidates = _markdown_link_items(content)
        if len(candidates) < 3:
            continue
        parent_key = record_key(record)
        for candidate in candidates:
            candidate.update(
                {
                    "schema_version": OUTPUT_CONTRACT_SCHEMA_VERSION,
                    "item_id": f"item_{item_index:04d}",
                    "item_citation_id": f"I{item_index}",
                    "parent_record_key": parent_key,
                    "parent_document_id": record.get("document_id"),
                    "parent_chunk_id": record.get("chunk_id"),
                    "parent_url": record.get("url"),
                    "parent_title": record.get("title"),
                }
            )
            items.append(candidate)
            item_index += 1
    return items


def _markdown_link_items(content: str) -> list[dict[str, Any]]:
    import re

    pattern = re.compile(r"(?P<prefix>^.{0,160}?)\[(?P<title>[^\]]{8,160})]\((?P<url>https?://[^)]+)\)", re.M)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in pattern.finditer(content):
        url = match.group("url").strip()
        title = match.group("title").strip()
        if not _looks_like_listing_item(title, url):
            continue
        if url in seen:
            continue
        seen.add(url)
        prefix = match.group("prefix").strip(" -#*\t")
        items.append(
            {
                "title": title,
                "url": url,
                "context": prefix[:160] if prefix else None,
            }
        )
    return items


def _looks_like_listing_item(title: str, url: str) -> bool:
    import re

    lowered = title.strip().lower()
    if "![" in title or lowered.startswith("image"):
        return False
    nav_titles = {
        "blogs",
        "budgets and reports",
        "media contacts",
        "nasa social",
        "nasa social media",
        "news releases",
        "newsletters",
        "recently published",
        "social media contacts",
        "upcoming launches",
    }
    if lowered in nav_titles or lowered.endswith(" contacts"):
        return False
    parsed = urlparse(url)
    path = parsed.path.strip("/").lower()
    collection_suffixes = {
        "events",
        "nasa-blogs",
        "news/media-contacts",
        "news/recently-published",
        "newsletters",
        "social-media",
        "social-media-contacts",
    }
    if path in collection_suffixes or path.endswith("-news-releases"):
        return False
    words = [part for part in title.replace(":", " ").split() if part.strip()]
    return len(words) >= 3 or re.search(r"/20\d{2}/", parsed.path) is not None


def _has_precise_record_citations(citation_payload: dict[str, Any]) -> bool:
    sources = citation_payload.get("sources")
    if not isinstance(sources, list):
        return False
    for source in sources:
        if not isinstance(source, dict):
            continue
        records = source.get("record_citations")
        if isinstance(records, list) and records:
            return any(
                isinstance(record, dict) and str(record.get("record_citation_id") or "").startswith("S")
                for record in records
            )
    return False


def _read_json(path: Path, *, required: bool = True) -> Any:
    if not path.exists():
        if required:
            raise OutputContractError(f"Missing required file: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        if required:
            raise OutputContractError(f"Invalid JSON in {path}: {err}") from err
        return None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _issue(
    code: str,
    message: str,
    *,
    path: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if path:
        payload["path"] = path
    if details:
        payload["details"] = details
    return payload


def _dict_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return None


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _domain(url: str) -> str:
    return urlparse(url).netloc.lower()
