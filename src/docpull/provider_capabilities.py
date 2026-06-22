"""Provider capability registry for optional live web-intelligence providers."""

from __future__ import annotations

from typing import Any

from .provider_keys import PROVIDER_NAMES, ProviderName, normalize_provider_name

PROVIDER_CAPABILITIES: dict[ProviderName, list[dict[str, Any]]] = {
    "parallel": [
        {
            "id": "probe",
            "status": "available",
            "surface": "docpull providers probe --provider parallel; docpull parallel probe",
            "kind": "auth",
            "safe_probe": "local-only; no documented zero-cost data API key endpoint",
            "validation_probe": "Parallel Search auth gate with intentionally invalid request body",
            "smoke_probe": "minimal Parallel Search request",
            "may_consume_quota": {"safe": False, "validation": False, "smoke": True},
        },
        {
            "id": "context-pack",
            "status": "available",
            "surface": "docpull parallel context-pack; docpull providers context-pack",
            "kind": "baseline",
        },
        {
            "id": "extract-pack",
            "status": "available",
            "surface": "docpull parallel extract-pack",
            "kind": "baseline",
        },
        {
            "id": "search-pack",
            "status": "available",
            "surface": "docpull parallel search-pack",
            "kind": "provider-specific",
        },
        {
            "id": "task-pack",
            "status": "available",
            "surface": "docpull parallel task-pack",
            "kind": "provider-specific",
        },
        {
            "id": "taskgroup-pack",
            "status": "available",
            "surface": "docpull parallel taskgroup-pack",
            "kind": "provider-specific",
        },
        {
            "id": "entity-pack",
            "status": "available",
            "surface": "docpull parallel entity-pack",
            "kind": "provider-specific",
        },
        {
            "id": "findall-pack",
            "status": "available",
            "surface": "docpull parallel findall-pack",
            "kind": "provider-specific",
        },
        {
            "id": "monitor-pack",
            "status": "available",
            "surface": "docpull parallel monitor-pack",
            "kind": "provider-specific",
        },
        {
            "id": "api-pack",
            "status": "available",
            "surface": "docpull parallel api-pack",
            "kind": "provider-specific",
        },
        {
            "id": "diff-brief",
            "status": "available",
            "surface": "docpull parallel diff-brief",
            "kind": "provider-specific",
        },
    ],
    "tavily": [
        {
            "id": "probe",
            "status": "available",
            "surface": "docpull providers probe --provider tavily; docpull tavily probe",
            "kind": "auth",
            "safe_probe": "GET /usage",
            "validation_probe": "GET /usage",
            "smoke_probe": "minimal Tavily Search request",
            "may_consume_quota": {"safe": False, "validation": False, "smoke": True},
        },
        {
            "id": "context-pack",
            "status": "available",
            "surface": "docpull tavily context-pack; docpull providers context-pack",
            "kind": "baseline",
        },
        {
            "id": "extract-pack",
            "status": "available",
            "surface": "docpull tavily extract-pack; docpull providers extract-pack",
            "kind": "baseline",
        },
        {
            "id": "map-pack",
            "status": "available",
            "surface": "docpull tavily map-pack",
            "kind": "provider-specific",
        },
        {
            "id": "crawl-pack",
            "status": "planned",
            "surface": "Tavily Crawl API",
            "kind": "provider-specific",
            "reason": (
                "Tavily Crawl returns extracted multi-page content and should map to DocPull crawl artifacts."
            ),
        },
        {
            "id": "research-pack",
            "status": "planned",
            "surface": "Tavily Research API",
            "kind": "provider-specific",
            "reason": (
                "Tavily Research is async/report-like and needs lifecycle metadata before "
                "becoming first-class."
            ),
        },
    ],
    "exa": [
        {
            "id": "probe",
            "status": "available",
            "surface": "docpull providers probe --provider exa; docpull exa probe",
            "kind": "auth",
            "safe_probe": "GET /websets/v0/teams/me",
            "validation_probe": "GET /websets/v0/teams/me",
            "smoke_probe": "minimal Exa Search request",
            "may_consume_quota": {"safe": False, "validation": False, "smoke": True},
        },
        {
            "id": "context-pack",
            "status": "available",
            "surface": "docpull exa context-pack; docpull providers context-pack",
            "kind": "baseline",
        },
        {
            "id": "extract-pack",
            "status": "available",
            "surface": "docpull exa extract-pack; docpull providers extract-pack",
            "kind": "baseline",
        },
        {
            "id": "category-search",
            "status": "planned",
            "surface": "Exa Search categories",
            "kind": "provider-specific",
            "reason": (
                "Exa categories should become provider options on context-pack, not a separate pack shape."
            ),
        },
        {
            "id": "agent-pack",
            "status": "planned",
            "surface": "Exa Agent API",
            "kind": "provider-specific",
            "reason": (
                "Exa Agent is async/high-compute and needs lifecycle events, status polling, "
                "and report artifacts."
            ),
        },
        {
            "id": "monitor-pack",
            "status": "planned",
            "surface": "Exa Monitors API",
            "kind": "provider-specific",
            "reason": (
                "Exa Monitors need webhook/schedule policy and should align with DocPull local monitors."
            ),
        },
    ],
}


def provider_capabilities(provider: ProviderName | str | None = None) -> dict[str, list[dict[str, Any]]]:
    if provider is not None:
        name = normalize_provider_name(provider)
        return {name: [dict(item) for item in PROVIDER_CAPABILITIES[name]]}
    return {name: [dict(item) for item in PROVIDER_CAPABILITIES[name]] for name in PROVIDER_NAMES}
