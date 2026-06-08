"""Deterministic source scoring for docpull context packs."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


def score_source(
    *,
    url: str,
    title: str = "",
    expected_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Score one source URL for agent-loading priority.

    The score is intentionally heuristic and local-only. It favors official
    docs/API/spec/changelog sources and penalizes off-policy or low-signal URLs.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.lower()
    title_l = title.lower()
    expected = [domain.lower().removeprefix("www.") for domain in expected_domains or []]
    reasons: list[str] = []
    score = 50

    if expected:
        if any(domain == item or domain.endswith(f".{item}") for item in expected):
            score += 20
            reasons.append("expected_domain")
        else:
            score -= 25
            reasons.append("off_domain")

    if domain.startswith("docs.") or ".docs." in domain or "developer" in domain:
        score += 12
        reasons.append("docs_domain")

    if any(part in path for part in ("/docs", "/api", "/reference", "/developers")):
        score += 10
        reasons.append("docs_path")

    if path.endswith(("llms.txt", "openapi.json", "swagger.json", "openapi.yaml", "openapi.yml")):
        score += 15
        reasons.append("machine_readable_spec")

    if any(term in title_l for term in ("docs", "documentation", "api", "reference", "changelog")):
        score += 8
        reasons.append("source_title")

    if any(part in path for part in ("/blog", "/news", "/press", "/legal")):
        score -= 8
        reasons.append("lower_priority_path")

    if not parsed.scheme.startswith("http"):
        score -= 15
        reasons.append("non_web_source")

    score = max(0, min(100, score))
    return {
        "url": url,
        "title": title,
        "domain": domain,
        "score": score,
        "grade": _source_grade(score),
        "reasons": reasons or ["generic_web_source"],
    }


def score_source_entries(
    entries: list[dict[str, Any]],
    *,
    expected_domains: list[str] | None = None,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for entry in entries:
        url = str(entry.get("url") or "")
        if not url:
            continue
        title = str(entry.get("title") or "")
        payload = score_source(url=url, title=title, expected_domains=expected_domains)
        if "path" in entry:
            payload["path"] = entry["path"]
        if "index" in entry:
            payload["index"] = entry["index"]
        scored.append(payload)
    return sorted(scored, key=lambda item: (-int(item["score"]), str(item["url"])))


def _source_grade(score: int) -> str:
    if score >= 85:
        return "primary"
    if score >= 70:
        return "strong"
    if score >= 50:
        return "usable"
    return "review"
