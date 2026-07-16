"""Common execution protocol for evidence-backed knowledge-pack workflows."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Protocol

from .contracts import WorkflowRequest, WorkflowResult, build_workflow_request
from .policy import PolicyConfig


class WorkflowExecutionError(RuntimeError):
    """Raised when a generic workflow request cannot be executed."""


_CURRENT_WORKFLOW_REQUEST: ContextVar[WorkflowRequest | None] = ContextVar(
    "docpull_workflow_request",
    default=None,
)


class PackWorkflow(Protocol):
    """Transport-neutral protocol implemented by every registered pack lane."""

    name: str

    def execute(self, request: WorkflowRequest) -> dict[str, Any]: ...


class FunctionPackWorkflow:
    """Small adapter that gives existing SDK builders the common protocol."""

    def __init__(self, name: str, executor: Callable[[WorkflowRequest], dict[str, Any]]) -> None:
        self.name = name
        self._executor = executor

    def execute(self, request: WorkflowRequest) -> dict[str, Any]:
        return self._executor(request)


def create_workflow_request(
    workflow: str,
    value: str | None = None,
    *,
    output_dir: Path,
    input_payload: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    policy: PolicyConfig | None = None,
) -> WorkflowRequest:
    if input_payload is not None and value is not None:
        raise WorkflowExecutionError("Pass either value or input_payload, not both")
    if input_payload is None:
        if not isinstance(value, str) or not value.strip():
            raise WorkflowExecutionError("A non-empty value or input_payload is required")
        effective_input = {"value": value.strip()}
    elif not input_payload:
        raise WorkflowExecutionError("input_payload must not be empty")
    else:
        effective_input = dict(input_payload)
    effective_policy = policy or PolicyConfig()
    merged_options = dict(options or {})
    if policy is not None:
        merged_options["policy"] = policy.model_dump(mode="json")
    return build_workflow_request(
        workflow=_normalize_workflow(workflow),
        input_payload=effective_input,
        output_dir=output_dir,
        options=merged_options,
        source_policy=effective_policy.to_source_policy_payload(source=workflow),
        budget={"maximum_paid_cost_usd": effective_policy.budget.maximum_paid_cost_usd},
        browser_enabled=_normalize_workflow(workflow) == "screenshot-pack"
        or bool(merged_options.get("render")),
        paid_routes_enabled=False,
    )


def run_workflow(request: WorkflowRequest | dict[str, Any]) -> dict[str, Any]:
    """Execute one request and return the canonical ``workflow.result.v1`` payload."""

    parsed = request if isinstance(request, WorkflowRequest) else WorkflowRequest.model_validate(request)
    workflow_name = _normalize_workflow(parsed.workflow)
    workflow = WORKFLOW_REGISTRY.get(workflow_name)
    if workflow is None:
        available = ", ".join(sorted(WORKFLOW_REGISTRY))
        raise WorkflowExecutionError(f"Unsupported workflow {parsed.workflow!r}. Available: {available}")
    token = _CURRENT_WORKFLOW_REQUEST.set(parsed)
    try:
        workflow.execute(parsed)
    finally:
        _CURRENT_WORKFLOW_REQUEST.reset(token)
    output_dir = _request_output_dir(parsed)
    result_path = output_dir / "workflow.result.json"
    if not result_path.exists():
        raise WorkflowExecutionError(
            f"Workflow {workflow_name} did not emit the required workflow.result.json contract"
        )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    return WorkflowResult.model_validate(payload).model_dump(mode="json", exclude_none=True)


async def async_run_workflow(request: WorkflowRequest | dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(run_workflow, request)


def current_workflow_request() -> WorkflowRequest | None:
    """Return the active request while a registered builder is materializing artifacts."""

    return _CURRENT_WORKFLOW_REQUEST.get()


def _run_brand(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.brand import build_brand_pack

    options = request.options
    return build_brand_pack(
        _request_value(request),
        email=_optional_str(options, "email"),
        name=_optional_str(options, "name"),
        ticker=_optional_str(options, "ticker"),
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        allow_free_email=bool(options.get("allow_free_email", False)),
        download_assets=bool(options.get("download_assets", True)),
        max_pages=_positive_int(options, "max_pages", 6),
    )


def _run_product(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.product import build_product_pack

    options = request.options
    return build_product_pack(
        _request_value(request),
        mode=str(options.get("mode") or "page"),
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        max_pages=_positive_int(options, "max_pages", 8),
    )


def _run_styleguide(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.styleguide import build_styleguide_pack

    options = request.options
    return build_styleguide_pack(
        _request_value(request),
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        render=bool(options.get("render", False)),
        max_stylesheets=_positive_int(options, "max_stylesheets", 12),
    )


def _run_visual(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.visuals import build_image_pack

    options = request.options
    return build_image_pack(
        _request_value(request),
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        download_assets=bool(options.get("download_assets", True)),
        max_assets=_positive_int(options, "max_assets", 40),
    )


def _run_screenshot(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.visuals import capture_screenshot_pack

    options = request.options
    return capture_screenshot_pack(
        _request_value(request),
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        viewport=str(options.get("viewport") or "1280x720"),
        full_page=bool(options.get("full_page", False)),
        wait_for=str(options.get("wait_for") or "load"),
        agent_browser_binary=_optional_str(options, "agent_browser_binary"),
    )


def _run_policy(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.policy_pack import build_policy_pack

    options = request.options
    baseline = _optional_str(options, "baseline_pack")
    return build_policy_pack(
        _request_value(request),
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        max_pages=_positive_int(options, "max_pages", 16),
        baseline_pack=Path(baseline) if baseline else None,
    )


def _run_dataset(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.dataset import build_dataset_pack

    options = request.options
    prepare_level = str(options.get("prepare_level") or "raw")
    if prepare_level not in {"raw", "agent", "eval"}:
        raise WorkflowExecutionError("WorkflowRequest.options.prepare_level must be raw, agent, or eval")
    sources: list[str | Path] = []
    sources.extend(_request_values(request))
    return build_dataset_pack(
        sources,
        output_dir=_request_output_dir(request),
        max_items=_positive_int(options, "max_items", 50),
        chunk_tokens=_positive_int(options, "chunk_tokens", 4000),
        prepare_level=prepare_level,  # type: ignore[arg-type]
    )


def _run_relationship(request: WorkflowRequest) -> dict[str, Any]:
    from .context_packs.relationship import build_relationship_pack

    raw_sources = request.input.get("sources")
    if raw_sources is None:
        sources: list[str | dict[str, Any]] = [_request_value(request)]
    elif isinstance(raw_sources, list) and all(isinstance(item, (str, dict)) for item in raw_sources):
        sources = list(raw_sources)
    else:
        raise WorkflowExecutionError("WorkflowRequest.input.sources must be a list of strings or objects")
    return build_relationship_pack(
        sources,
        output_dir=_request_output_dir(request),
        policy=_request_policy(request),
        max_pages_per_source=_positive_int(request.options, "max_pages_per_source", 4),
    )


def _run_fetch(request: WorkflowRequest) -> dict[str, Any]:
    from .acquisition_workflows import execute_acquisition_workflow

    return execute_acquisition_workflow(request, crawl=False)


def _run_crawl(request: WorkflowRequest) -> dict[str, Any]:
    from .acquisition_workflows import execute_acquisition_workflow

    return execute_acquisition_workflow(request, crawl=True)


WORKFLOW_REGISTRY: dict[str, PackWorkflow] = {
    "brand-pack": FunctionPackWorkflow("brand-pack", _run_brand),
    "product-pack": FunctionPackWorkflow("product-pack", _run_product),
    "styleguide-pack": FunctionPackWorkflow("styleguide-pack", _run_styleguide),
    "visual-pack": FunctionPackWorkflow("visual-pack", _run_visual),
    "image-pack": FunctionPackWorkflow("image-pack", _run_visual),
    "screenshot-pack": FunctionPackWorkflow("screenshot-pack", _run_screenshot),
    "policy-pack": FunctionPackWorkflow("policy-pack", _run_policy),
    "dataset-pack": FunctionPackWorkflow("dataset-pack", _run_dataset),
    "relationship-pack": FunctionPackWorkflow("relationship-pack", _run_relationship),
    "fetch": FunctionPackWorkflow("fetch", _run_fetch),
    "crawl": FunctionPackWorkflow("crawl", _run_crawl),
}


def _normalize_workflow(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    aliases = {
        "brand": "brand-pack",
        "product": "product-pack",
        "styleguide": "styleguide-pack",
        "visual": "visual-pack",
        "image": "image-pack",
        "screenshot": "screenshot-pack",
        "policy": "policy-pack",
        "dataset": "dataset-pack",
        "relationship": "relationship-pack",
    }
    return aliases.get(normalized, normalized)


def _request_value(request: WorkflowRequest) -> str:
    for key in ("value", "url", "domain_or_url", "url_or_domain", "url_or_pack"):
        value = request.input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise WorkflowExecutionError("WorkflowRequest.input must include a non-empty value or URL")


def _request_values(request: WorkflowRequest) -> list[str]:
    values = request.input.get("sources")
    if isinstance(values, list) and all(isinstance(item, str) and item.strip() for item in values):
        return [item.strip() for item in values]
    return [_request_value(request)]


def _request_output_dir(request: WorkflowRequest) -> Path:
    value = request.output.get("directory")
    if not isinstance(value, str) or not value.strip():
        raise WorkflowExecutionError("WorkflowRequest.output.directory must be a non-empty path")
    return Path(value).expanduser().resolve()


def _request_policy(request: WorkflowRequest) -> PolicyConfig | None:
    raw = request.options.get("policy")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise WorkflowExecutionError("WorkflowRequest.options.policy must be an object")
    return PolicyConfig.model_validate(raw)


def _positive_int(options: dict[str, Any], key: str, default: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkflowExecutionError(f"WorkflowRequest.options.{key} must be a positive integer")
    return value


def _optional_str(options: dict[str, Any], key: str) -> str | None:
    value = options.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkflowExecutionError(f"WorkflowRequest.options.{key} must be a string")
    return value


__all__ = [
    "PackWorkflow",
    "WORKFLOW_REGISTRY",
    "WorkflowExecutionError",
    "async_run_workflow",
    "current_workflow_request",
    "create_workflow_request",
    "run_workflow",
]
