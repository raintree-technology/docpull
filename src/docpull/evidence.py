"""Evidence-span and source-authority helpers shared by pack workflows."""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from .contracts import EvidenceSpan, SourceAuthority

_LEGAL_TERMS = (
    "terms",
    "privacy",
    "legal",
    "dpa",
    "data-processing",
    "cookie",
    "subprocessor",
    "security",
    "refund",
    "ai-terms",
)
_SOCIAL_DOMAINS = {
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "medium.com",
    "threads.net",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "youtube.com",
}
_MARKETPLACE_DOMAINS = {
    "apps.apple.com",
    "play.google.com",
    "aws.amazon.com",
    "marketplace.atlassian.com",
    "marketplace.visualstudio.com",
    "chromewebstore.google.com",
}


def classify_source_authority(url: str, *, official_domain: str | None = None) -> SourceAuthority:
    """Classify source role and authority without making review decisions."""

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = (parsed.path or "/").lower()
    official = (official_domain or "").lower().removeprefix("www.").rstrip(".")
    same_official = bool(official) and (host == official or host.endswith(f".{official}"))

    if same_official and any(term in path for term in _LEGAL_TERMS):
        return SourceAuthority(
            role="legal",
            tier="tier_1_authoritative",
            rationale="Official-domain legal, policy, security, or contractual source.",
        )
    if same_official and (host.startswith("docs.") or "/docs" in path or "/documentation" in path):
        return SourceAuthority(
            role="documentation",
            tier="tier_1_authoritative",
            rationale="Official-domain product documentation source.",
        )
    if any(host == domain or host.endswith(f".{domain}") for domain in _SOCIAL_DOMAINS):
        return SourceAuthority(
            role="social",
            tier="tier_2_owned",
            rationale="Organization-controlled social distribution source; authorship still requires review.",
        )
    if any(host == domain or host.endswith(f".{domain}") for domain in _MARKETPLACE_DOMAINS):
        return SourceAuthority(
            role="marketplace",
            tier="tier_3_distribution",
            rationale="Platform marketplace listing rather than the canonical product site.",
        )
    if same_official:
        return SourceAuthority(
            role="official_product",
            tier="tier_1_authoritative",
            rationale="Official-domain product or company source.",
        )
    return SourceAuthority(
        role="third_party",
        tier="tier_4_external",
        rationale="External source; downstream review must decide how it may be used.",
    )


def evidence_span(
    *,
    url: str,
    content: str,
    exact_text: str,
    citation_id: str,
    record_citation_id: str | None = None,
    occurrence: int = 0,
) -> EvidenceSpan:
    """Return a character-precise span tied to a deterministic document version."""

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    document_id = document_identity(url, content_hash)
    start = _nth_index(content, exact_text, occurrence)
    selected = exact_text
    if start < 0:
        selected = exact_text.strip()
        start = content.find(selected)
    if start < 0:
        selected = content[: min(len(content), max(1, len(exact_text)))]
        start = 0
    end = start + len(selected)
    return EvidenceSpan(
        citation_id=citation_id,
        record_citation_id=record_citation_id,
        document_id=document_id,
        document_version=content_hash,
        url=url,
        char_start=start,
        char_end=end,
        exact_text=selected,
        exact_text_sha256=hashlib.sha256(selected.encode("utf-8")).hexdigest(),
    )


def evidence_span_payload(**kwargs: Any) -> dict[str, Any]:
    return evidence_span(**kwargs).model_dump(mode="json", exclude_none=True)


def document_identity(url: str, content_hash: str) -> str:
    """Match :meth:`DocumentRecord.from_page` document identity exactly."""

    digest = hashlib.sha256(f"{url}\x1f{content_hash}".encode()).hexdigest()
    return f"doc_{digest[:24]}"


def _nth_index(content: str, text: str, occurrence: int) -> int:
    if not text:
        return 0
    start = -1
    cursor = 0
    for _index in range(max(0, occurrence) + 1):
        start = content.find(text, cursor)
        if start < 0:
            return -1
        cursor = start + len(text)
    return start


__all__ = [
    "classify_source_authority",
    "document_identity",
    "evidence_span",
    "evidence_span_payload",
]
