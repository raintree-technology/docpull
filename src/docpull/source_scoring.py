"""Deterministic source scoring for docpull context packs."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

LOWER_PRIORITY_PATH_PARTS = (
    "/blog",
    "/news",
    "/newsletter",
    "/press",
    "/legal",
    "/privacy",
    "/terms",
    "/login",
    "/signup",
    "/sign-in",
    "/sign-up",
    "/account",
    "/sponsor",
    "/sponsors",
)
DOC_PATH_TOKENS = (
    "docs",
    "api",
    "reference",
    "developers",
    "tutorial",
    "guide",
    "guides",
    "learn",
    "how-to",
    "manual",
    "examples",
    "quickstart",
)
DOC_TITLE_TERMS = (
    "docs",
    "documentation",
    "api",
    "reference",
    "changelog",
    "tutorial",
    "guide",
    "quickstart",
    "manual",
)
LOCALE_SEGMENTS = {
    "ar",
    "bg",
    "bn",
    "ca",
    "cs",
    "da",
    "de",
    "el",
    "en",
    "es",
    "fa",
    "fi",
    "fr",
    "he",
    "hi",
    "id",
    "it",
    "ja",
    "ko",
    "nl",
    "no",
    "pl",
    "pt",
    "pt-br",
    "ro",
    "ru",
    "sv",
    "th",
    "tr",
    "uk",
    "vi",
    "zh",
    "zh-cn",
    "zh-hans",
    "zh-hant",
    "zh-tw",
}


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

    if domain.startswith("docs.") or ".docs." in domain or domain.startswith(("developer.", "developers.")):
        score += 12
        reasons.append("docs_domain")

    # Match a doc token as a whole path segment or a hyphen/underscore-prefixed
    # one (so "/api-reference" and "/api/v2" score, but "/apiary" does not).
    path_segments = [segment for segment in path.split("/") if segment]
    if any(
        segment == token or segment.startswith((f"{token}-", f"{token}_"))
        for segment in path_segments
        for token in DOC_PATH_TOKENS
    ):
        score += 10
        reasons.append("docs_path")

    if path.endswith(("llms.txt", "openapi.json", "swagger.json", "openapi.yaml", "openapi.yml")):
        score += 15
        reasons.append("machine_readable_spec")

    if any(term in title_l for term in DOC_TITLE_TERMS):
        score += 8
        reasons.append("source_title")

    if _is_locale_home_path(path_segments):
        score -= 18
        reasons.append("locale_home_path")

    if any(part in path for part in LOWER_PRIORITY_PATH_PARTS):
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


def _is_locale_home_path(path_segments: list[str]) -> bool:
    return len(path_segments) == 1 and path_segments[0].lower() in LOCALE_SEGMENTS


def _source_grade(score: int) -> str:
    if score >= 85:
        return "primary"
    if score >= 70:
        return "strong"
    if score >= 50:
        return "usable"
    return "review"
