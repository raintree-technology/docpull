"""Explicit live API-key probes for optional provider integrations."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from .provider_adapters import (
    HTTP_MAX_ERROR_BYTES,
    HTTP_MAX_RESPONSE_BYTES,
    HTTP_RETRY_MAX_ATTEMPTS,
    HTTP_RETRY_TRANSIENT_STATUSES,
    _NoRedirectHandler,
    _parse_retry_after,
    _redact_secret_like,
    _retry_delay_seconds,
    _short_error_detail,
)
from .provider_keys import (
    PROVIDER_CONFIGS,
    PROVIDER_NAMES,
    ProviderName,
    lookup_provider_api_key,
    normalize_provider_name,
)
from .time_utils import utc_now_iso

ProbeMode = Literal["safe", "validation", "smoke"]

TAVILY_USAGE_URL = "https://api.tavily.com/usage"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
EXA_TEAM_URL = "https://api.exa.ai/websets/v0/teams/me"
EXA_SEARCH_URL = "https://api.exa.ai/search"
PARALLEL_SEARCH_URL = "https://api.parallel.ai/v1/search"
DEFAULT_PROBE_TIMEOUT_SECONDS = 15.0
DEFAULT_SMOKE_MAX_ESTIMATED_COST_USD = 0.01
SMOKE_ESTIMATED_COST_USD: dict[ProviderName, float] = {
    "parallel": 0.005,
    "tavily": 0.01,
    "exa": 0.01,
}


class ProviderProbeError(RuntimeError):
    """User-facing provider probe error."""


@dataclass(frozen=True)
class ProbeHttpResponse:
    """Small normalized HTTP response for probe endpoints."""

    status: int
    body: dict[str, Any]
    headers: dict[str, str]


def provider_probe_payload(
    providers: list[str],
    *,
    mode: ProbeMode = "safe",
    include_account_metadata: bool = False,
    redact_paths: bool = False,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_estimated_cost: float = DEFAULT_SMOKE_MAX_ESTIMATED_COST_USD,
) -> dict[str, Any]:
    """Run explicit provider probes and return redaction-safe structured status."""

    if mode not in {"safe", "validation", "smoke"}:
        raise ProviderProbeError(f"Unsupported provider probe mode: {mode}")
    if timeout <= 0:
        raise ProviderProbeError("Probe timeout must be greater than 0.")
    if max_estimated_cost < 0:
        raise ProviderProbeError("Probe --max-estimated-cost must be at least 0.")
    selected = _normalize_probe_providers(providers)
    results: dict[str, dict[str, Any]] = {
        str(provider): probe_provider(
            provider,
            mode=mode,
            include_account_metadata=include_account_metadata,
            redact_paths=redact_paths,
            timeout=timeout,
            max_estimated_cost=max_estimated_cost,
        )
        for provider in selected
    }
    verified = [
        provider
        for provider, result in results.items()
        if result.get("live_valid") is True and result.get("workflow_ready") is True
    ]
    live_checked = [provider for provider, result in results.items() if result.get("live_checked")]
    return {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "mode": mode,
        "provider_count": len(results),
        "live_checked_count": len(live_checked),
        "verified_count": len(verified),
        "verified_providers": verified,
        "providers": results,
        "paths_redacted": redact_paths,
        "account_metadata_included": include_account_metadata,
        "next_actions": _probe_next_actions(results, mode=mode),
    }


def probe_provider(
    provider: ProviderName | str,
    *,
    mode: ProbeMode = "safe",
    include_account_metadata: bool = False,
    redact_paths: bool = False,
    timeout: float = DEFAULT_PROBE_TIMEOUT_SECONDS,
    max_estimated_cost: float = DEFAULT_SMOKE_MAX_ESTIMATED_COST_USD,
) -> dict[str, Any]:
    name = normalize_provider_name(provider)
    config = PROVIDER_CONFIGS[name]
    lookup = lookup_provider_api_key(name)
    base: dict[str, Any] = {
        "provider": name,
        "label": config.label,
        "mode": mode,
        "configured": bool(lookup.value),
        "local_key_valid": bool(lookup.value) and not lookup.invalid_reason,
        "api_key_env_var": config.api_key_env_var,
        "api_key_source": lookup.source,
        "api_key_source_path": "[redacted]"
        if redact_paths and lookup.path
        else str(lookup.path)
        if lookup.path
        else None,
        "api_key_invalid_reason": lookup.invalid_reason,
        "live_checked": False,
        "live_valid": False,
        "workflow_ready": False,
        "probe_kind": None,
        "endpoint": None,
        "method": None,
        "http_status": None,
        "request_id": None,
        "rate_limited": False,
        "quota_state": "not_checked",
        "may_consume_quota": False,
        "may_count_against_rate_limit": False,
        "estimated_cost_usd": None,
        "metadata_redacted": not include_account_metadata,
        "next_actions": [],
    }
    if lookup.invalid_reason:
        base["quota_state"] = "invalid_local_key"
        base["next_actions"] = [
            (
                f"Replace the invalid {config.api_key_env_var} value with "
                f"`docpull providers init {name} --force`."
            )
        ]
        return base
    if not lookup.value:
        base["quota_state"] = "missing_api_key"
        base["next_actions"] = [f"Store {config.api_key_env_var} with `docpull providers init {name}`."]
        return base
    if mode == "smoke":
        _enforce_smoke_cost_guard(name, max_estimated_cost=max_estimated_cost)
        return _smoke_probe(name, lookup.value, base=base, timeout=timeout)
    if name == "tavily":
        return _tavily_usage_probe(
            lookup.value,
            base=base,
            timeout=timeout,
            include_account_metadata=include_account_metadata,
        )
    if name == "exa":
        return _exa_team_probe(
            lookup.value,
            base=base,
            timeout=timeout,
            include_account_metadata=include_account_metadata,
        )
    if mode == "validation":
        return _parallel_validation_probe(lookup.value, base=base, timeout=timeout)

    base["live_valid"] = None
    base["workflow_ready"] = bool(lookup.value)
    base["probe_kind"] = "no_safe_live_probe"
    base["quota_state"] = "configured_not_live_verified"
    base["next_actions"] = [
        "Parallel has no documented zero-cost account probe for data API keys.",
        (
            "Use `docpull providers probe --provider parallel --mode validation --json` "
            "for an opt-in auth-gate check."
        ),
        (
            "Use `docpull providers probe --provider parallel --mode smoke "
            "--max-estimated-cost 0.01 --json` for a real Search smoke test."
        ),
    ]
    return base


def _normalize_probe_providers(providers: list[str]) -> list[ProviderName]:
    selected = providers or list(PROVIDER_NAMES)
    normalized: list[ProviderName] = []
    for raw_provider in selected:
        value = raw_provider.strip().lower()
        if value in {"all", "auto"}:
            for provider in PROVIDER_NAMES:
                if provider not in normalized:
                    normalized.append(provider)
            continue
        provider = normalize_provider_name(value)
        if provider not in normalized:
            normalized.append(provider)
    return normalized


def _tavily_usage_probe(
    api_key: str,
    *,
    base: dict[str, Any],
    timeout: float,
    include_account_metadata: bool,
) -> dict[str, Any]:
    response = _http_json_request(
        label="Tavily Usage",
        url=TAVILY_USAGE_URL,
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    result = _apply_http_probe_result(
        base,
        response,
        endpoint=TAVILY_USAGE_URL,
        method="GET",
        probe_kind="account_usage",
        may_consume_quota=False,
    )
    if include_account_metadata and response.status == 200:
        result["account_metadata"] = _account_metadata(response.body, ("key", "account"))
    return result


def _exa_team_probe(
    api_key: str,
    *,
    base: dict[str, Any],
    timeout: float,
    include_account_metadata: bool,
) -> dict[str, Any]:
    response = _http_json_request(
        label="Exa Team Info",
        url=EXA_TEAM_URL,
        method="GET",
        headers={"x-api-key": api_key},
        timeout=timeout,
    )
    result = _apply_http_probe_result(
        base,
        response,
        endpoint=EXA_TEAM_URL,
        method="GET",
        probe_kind="team_info",
        may_consume_quota=False,
    )
    if include_account_metadata and response.status == 200:
        result["account_metadata"] = _account_metadata(
            response.body,
            ("team", "teamId", "id", "limits", "usage"),
        )
    return result


def _parallel_validation_probe(api_key: str, *, base: dict[str, Any], timeout: float) -> dict[str, Any]:
    response = _http_json_request(
        label="Parallel Search validation",
        url=PARALLEL_SEARCH_URL,
        method="POST",
        headers={"x-api-key": api_key},
        body={},
        timeout=timeout,
    )
    result = _apply_http_probe_result(
        base,
        response,
        endpoint=PARALLEL_SEARCH_URL,
        method="POST",
        probe_kind="request_validation_auth_gate",
        may_consume_quota=False,
        valid_statuses={422},
    )
    result["may_count_against_rate_limit"] = True
    if response.status == 422:
        result["quota_state"] = "auth_verified_request_rejected"
        result["next_actions"] = [
            (
                "Parallel accepted the API key before request validation rejected the "
                "intentionally empty probe body."
            )
        ]
    return result


def _smoke_probe(name: ProviderName, api_key: str, *, base: dict[str, Any], timeout: float) -> dict[str, Any]:
    estimated_cost = SMOKE_ESTIMATED_COST_USD[name]
    if name == "tavily":
        response = _http_json_request(
            label="Tavily Search smoke",
            url=TAVILY_SEARCH_URL,
            method="POST",
            headers={"Authorization": f"Bearer {api_key}"},
            body={
                "query": "DocPull provider smoke test",
                "search_depth": "basic",
                "max_results": 1,
                "include_answer": False,
                "include_raw_content": False,
                "include_images": False,
                "include_usage": True,
            },
            timeout=timeout,
        )
        endpoint = TAVILY_SEARCH_URL
    elif name == "exa":
        response = _http_json_request(
            label="Exa Search smoke",
            url=EXA_SEARCH_URL,
            method="POST",
            headers={"x-api-key": api_key},
            body={
                "query": "DocPull provider smoke test",
                "type": "fast",
                "numResults": 1,
            },
            timeout=timeout,
        )
        endpoint = EXA_SEARCH_URL
    else:
        response = _http_json_request(
            label="Parallel Search smoke",
            url=PARALLEL_SEARCH_URL,
            method="POST",
            headers={"x-api-key": api_key},
            body={
                "objective": "Run a minimal DocPull provider smoke test.",
                "search_queries": ["DocPull provider smoke test"],
                "mode": "basic",
                "max_chars_total": 1000,
            },
            timeout=timeout,
        )
        endpoint = PARALLEL_SEARCH_URL
    result = _apply_http_probe_result(
        base,
        response,
        endpoint=endpoint,
        method="POST",
        probe_kind="search_smoke",
        may_consume_quota=True,
    )
    result["estimated_cost_usd"] = estimated_cost
    result["may_count_against_rate_limit"] = True
    return result


def _apply_http_probe_result(
    base: dict[str, Any],
    response: ProbeHttpResponse,
    *,
    endpoint: str,
    method: str,
    probe_kind: str,
    may_consume_quota: bool,
    valid_statuses: set[int] | None = None,
) -> dict[str, Any]:
    valid = valid_statuses or {200}
    result = dict(base)
    result.update(
        {
            "live_checked": True,
            "live_valid": response.status in valid or response.status in {402, 403},
            "workflow_ready": response.status in valid,
            "probe_kind": probe_kind,
            "endpoint": endpoint,
            "method": method,
            "http_status": response.status,
            "request_id": _request_id(response.body),
            "rate_limited": response.status == 429,
            "quota_state": _quota_state(response.status, response.body),
            "may_consume_quota": may_consume_quota,
            "may_count_against_rate_limit": True,
            "next_actions": _http_next_actions(response.status),
        }
    )
    return result


def _quota_state(status: int, body: dict[str, Any]) -> str:
    if status == 200:
        return "available"
    if status == 401:
        return "invalid_or_missing_key"
    if status == 402:
        return "payment_required"
    if status == 403:
        return "forbidden_or_plan_restricted"
    if status in {432, 433}:
        return "provider_limit_exceeded"
    if status == 429:
        return "rate_limited"
    if status == 422:
        return "request_validation_failed"
    error = body.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "")
    else:
        message = str(error or body.get("message") or "")
    return "unknown" if not message else "error"


def _http_next_actions(status: int) -> list[str]:
    if status == 200:
        return []
    if status == 401:
        return [
            (
                "Verify the API key value and source, then replace it with "
                "`docpull providers init <provider> --force`."
            )
        ]
    if status == 402:
        return ["The key authenticated, but the provider reports insufficient credits or budget."]
    if status == 403:
        return ["The key authenticated, but the provider reports insufficient permissions or plan access."]
    if status == 429:
        return ["Retry later; the provider rate limited the probe request."]
    if status in {432, 433}:
        return ["Provider plan or pay-as-you-go limits are exceeded."]
    if status == 422:
        return ["The key passed authentication, but the probe request body was rejected."]
    if status >= 500:
        return ["Provider service error; retry with backoff or check provider status."]
    return ["Inspect provider error details and retry after fixing the request or account state."]


def _request_id(body: dict[str, Any]) -> str | None:
    for key in ("request_id", "requestId", "search_id", "extract_id"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    error = body.get("error")
    if isinstance(error, dict):
        value = error.get("ref_id") or error.get("request_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _account_metadata(body: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in keys:
        if key in body:
            metadata[key] = body[key]
    return metadata


def _enforce_smoke_cost_guard(name: ProviderName, *, max_estimated_cost: float) -> None:
    estimated = SMOKE_ESTIMATED_COST_USD[name]
    if estimated > max_estimated_cost:
        raise ProviderProbeError(
            f"Estimated {PROVIDER_CONFIGS[name].label} smoke probe cost ${estimated:.3f} "
            f"exceeds --max-estimated-cost ${max_estimated_cost:.3f}."
        )


def _probe_next_actions(results: Mapping[str, dict[str, Any]], *, mode: ProbeMode) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for provider, result in results.items():
        if result.get("live_valid") is True and result.get("workflow_ready") is True:
            actions.append(
                {
                    "command": f'docpull {provider} context-pack "Find official docs" --dry-run --json',
                    "reason": f"Plan a {result['label']} run before spending provider credits.",
                }
            )
            continue
        if provider == "parallel" and mode == "safe" and result.get("configured"):
            actions.append(
                {
                    "command": "docpull providers probe --provider parallel --mode validation --json",
                    "reason": "Run an opt-in Parallel API-key auth-gate check.",
                }
            )
        elif not result.get("configured") or not result.get("local_key_valid"):
            actions.append(
                {
                    "command": f"docpull providers init {provider}",
                    "reason": f"Store or replace {result['api_key_env_var']}.",
                }
            )
    if mode != "smoke":
        actions.append(
            {
                "command": "docpull providers probe --mode smoke --max-estimated-cost 0.01 --json",
                "reason": "Run real minimal provider calls only when spending credits is acceptable.",
            }
        )
    return actions


def _http_json_request(
    *,
    label: str,
    url: str,
    method: Literal["GET", "POST"],
    headers: dict[str, str],
    timeout: float,
    body: dict[str, Any] | None = None,
    max_attempts: int = HTTP_RETRY_MAX_ATTEMPTS,
    sleep: Any = time.sleep,
) -> ProbeHttpResponse:
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https":
        raise ProviderProbeError(f"{label} URL must use HTTPS.")
    last_response: ProbeHttpResponse | None = None
    for attempt in range(1, max_attempts + 1):
        response = _http_json_request_once(
            label=label,
            url=url,
            method=method,
            headers=headers,
            timeout=timeout,
            body=body,
        )
        last_response = response
        if response.status not in HTTP_RETRY_TRANSIENT_STATUSES or attempt >= max_attempts:
            return response
        delay = _retry_delay_seconds(attempt=attempt, retry_after=_retry_after_header(response.headers))
        sleep(delay)
    if last_response is None:
        raise ProviderProbeError(f"{label} request failed without a captured response.")
    return last_response


def _http_json_request_once(
    *,
    label: str,
    url: str,
    method: Literal["GET", "POST"],
    headers: dict[str, str],
    timeout: float,
    body: dict[str, Any] | None = None,
) -> ProbeHttpResponse:
    data = json.dumps(body).encode("utf-8") if method == "POST" else None
    request = Request(
        url,
        data=data,
        headers={
            **({"Content-Type": "application/json"} if method == "POST" else {}),
            **headers,
        },
        method=method,
    )
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310
            raw_bytes = response.read(HTTP_MAX_RESPONSE_BYTES + 1)
            response_headers = dict(response.headers.items())
            status = int(response.status)
    except HTTPError as err:
        raw_bytes = err.read(HTTP_MAX_ERROR_BYTES)
        response_headers = dict(err.headers.items()) if err.headers else {}
        return ProbeHttpResponse(
            status=int(err.code),
            body=_parse_json_body(label, raw_bytes),
            headers=response_headers,
        )
    except URLError as err:
        raise ProviderProbeError(f"{label} request failed: {err.reason}") from err
    if len(raw_bytes) > HTTP_MAX_RESPONSE_BYTES:
        raise ProviderProbeError(f"{label} response exceeds {HTTP_MAX_RESPONSE_BYTES}-byte limit.")
    return ProbeHttpResponse(status=status, body=_parse_json_body(label, raw_bytes), headers=response_headers)


def _parse_json_body(label: str, raw_bytes: bytes) -> dict[str, Any]:
    text = _redact_secret_like(raw_bytes.decode("utf-8", errors="replace"))
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": _short_error_detail(text)}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _retry_after_header(headers: dict[str, str]) -> float | None:
    class _Headers:
        def __init__(self, values: dict[str, str]) -> None:
            self.values = values

        def get(self, key: str) -> str | None:
            return self.values.get(key)

    class _HTTPErrorShim:
        def __init__(self, values: dict[str, str]) -> None:
            self.headers = _Headers(values)

    return _parse_retry_after(_HTTPErrorShim(headers))  # type: ignore[arg-type]
