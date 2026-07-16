"""Versioned, scheduler-neutral change-event generation."""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any, Literal

from .contracts import ChangeEvent, ReplayConfiguration, canonical_sha256, stable_id
from .evidence import evidence_span

ChangeClassification = Literal["pricing", "positioning", "product", "security", "policy", "other"]

_CLASSIFICATION_TERMS: dict[ChangeClassification, tuple[str, ...]] = {
    "pricing": ("price", "pricing", "plan", "$", "usd", "billing", "trial"),
    "positioning": ("positioning", "mission", "leader", "best", "platform for", "built for"),
    "product": ("feature", "product", "launch", "available", "integration", "api"),
    "security": ("security", "encryption", "soc 2", "iso 27001", "vulnerability", "breach"),
    "policy": ("terms", "privacy", "cookie", "dpa", "subprocessor", "refund", "effective date"),
}


def build_change_events(
    old_records: dict[str, list[dict[str, Any]]],
    new_records: dict[str, list[dict[str, Any]]],
    *,
    workflow: str = "pack-diff",
) -> list[dict[str, Any]]:
    """Build idempotent events from already acquired document versions."""

    events: list[dict[str, Any]] = []
    for url in sorted(set(old_records) | set(new_records)):
        old_items = old_records.get(url, [])
        new_items = new_records.get(url, [])
        old_hashes = sorted(_hashes(old_items))
        new_hashes = sorted(_hashes(new_items))
        if old_hashes == new_hashes and _titles(old_items) == _titles(new_items):
            continue
        old_primary = old_items[0] if old_items else None
        new_primary = new_items[0] if new_items else None
        old_text = _combined_content(old_items)
        new_text = _combined_content(new_items)
        classifications = _classifications(old_text, new_text, url)
        structural = _structural_changes(old_items, new_items)
        textual = _textual_changes(old_text, new_text)
        old_evidence = _record_evidence(old_primary, old_text) if old_primary else []
        new_evidence = _record_evidence(new_primary, new_text) if new_primary else []
        identity = {
            "workflow": workflow,
            "url": url,
            "old_hashes": old_hashes,
            "new_hashes": new_hashes,
        }
        idempotency_key = canonical_sha256(identity)
        semantic_candidates = [
            {
                "classification": item,
                "status": "candidate",
                "confidence": _classification_confidence(item, old_text, new_text, url),
                "requires_review": True,
            }
            for item in classifications
        ]
        event = ChangeEvent(
            event_id=stable_id("change_event", identity),
            idempotency_key=idempotency_key,
            workflow=workflow,
            url=url,
            old_document_id=_string(old_primary, "document_id"),
            new_document_id=_string(new_primary, "document_id"),
            old_hash=canonical_sha256(old_hashes) if old_hashes else None,
            new_hash=canonical_sha256(new_hashes) if new_hashes else None,
            old_evidence=old_evidence,
            new_evidence=new_evidence,
            structural_changes=structural,
            textual_changes=textual,
            semantic_candidates=semantic_candidates,
            classifications=classifications,
            replay_configuration=ReplayConfiguration(
                configuration={
                    "workflow": workflow,
                    "old_hashes": old_hashes,
                    "new_hashes": new_hashes,
                }
            ),
        )
        events.append(event.model_dump(mode="json", exclude_none=True))
    return events


def write_change_events(path: Path, events: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def _record_evidence(record: dict[str, Any], content: str) -> list[Any]:
    if not content:
        return []
    excerpt = content[: min(600, len(content))]
    citation_id = str(record.get("source_citation_id") or "S0")
    record_citation_id = record.get("record_citation_id")
    return [
        evidence_span(
            url=str(record.get("url") or ""),
            content=content,
            exact_text=excerpt,
            citation_id=citation_id,
            record_citation_id=str(record_citation_id) if record_citation_id else None,
        )
    ]


def _structural_changes(
    old_items: list[dict[str, Any]],
    new_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if not old_items:
        changes.append({"type": "document_added", "new_record_count": len(new_items)})
    elif not new_items:
        changes.append({"type": "document_removed", "old_record_count": len(old_items)})
    if len(old_items) != len(new_items):
        changes.append(
            {
                "type": "record_count_changed",
                "before": len(old_items),
                "after": len(new_items),
            }
        )
    if _titles(old_items) != _titles(new_items):
        changes.append({"type": "title_changed", "before": _titles(old_items), "after": _titles(new_items)})
    return changes


def _textual_changes(old_text: str, new_text: str) -> list[dict[str, Any]]:
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    matcher = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changes: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before = "\n".join(old_lines[i1:i2])[:1000]
        after = "\n".join(new_lines[j1:j2])[:1000]
        changes.append(
            {
                "type": tag,
                "old_line_start": i1,
                "old_line_end": i2,
                "new_line_start": j1,
                "new_line_end": j2,
                "before": before,
                "after": after,
                "before_sha256": canonical_sha256(before),
                "after_sha256": canonical_sha256(after),
            }
        )
        if len(changes) >= 100:
            break
    return changes


def _classifications(old_text: str, new_text: str, url: str) -> list[ChangeClassification]:
    haystack = f"{url}\n{old_text}\n{new_text}".lower()
    output = [
        classification
        for classification, terms in _CLASSIFICATION_TERMS.items()
        if any(term in haystack for term in terms)
    ]
    return output or ["other"]


def _classification_confidence(
    classification: ChangeClassification,
    old_text: str,
    new_text: str,
    url: str,
) -> float:
    terms = _CLASSIFICATION_TERMS.get(classification, ())
    haystack = f"{url}\n{old_text}\n{new_text}".lower()
    hits = sum(1 for term in terms if term in haystack)
    return min(0.95, 0.5 + hits * 0.1) if terms else 0.4


def _combined_content(items: list[dict[str, Any]]) -> str:
    return "\n\n".join(str(item.get("content") or "") for item in items)


def _hashes(items: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("content_hash")) for item in items if item.get("content_hash")}


def _titles(items: list[dict[str, Any]]) -> list[str]:
    return sorted({str(item.get("title") or "") for item in items})


def _string(item: dict[str, Any] | None, key: str) -> str | None:
    if not item or item.get(key) is None:
        return None
    value = str(item[key]).strip()
    return value or None


__all__ = ["build_change_events", "write_change_events"]
