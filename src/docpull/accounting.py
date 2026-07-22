"""Budget enforcement and local run accounting helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .time_utils import utc_now_iso

ACCOUNTING_SCHEMA_VERSION = 1
ACCOUNTING_ARTIFACT = "run.accounting.json"


class BudgetError(RuntimeError):
    """Raised when a paid-capable action violates the configured budget."""


@dataclass(frozen=True)
class BlockedAction:
    """Non-secret description of an action blocked by budget policy."""

    action: str
    reason: str
    estimated_cost_usd: float | None = None
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "action": self.action,
            "reason": self.reason,
        }
        if self.estimated_cost_usd is not None:
            payload["estimated_cost_usd"] = round(float(self.estimated_cost_usd), 6)
        if self.provider:
            payload["provider"] = self.provider
        return payload


@dataclass(frozen=True)
class RouteStep:
    """One step in the local-first acquisition route."""

    name: str
    status: str
    cost_class: str = "local"
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "status": self.status,
            "cost_class": self.cost_class,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class RunAccounting:
    """Portable, non-secret accounting payload for one docpull run."""

    budget_limit_usd: float | None = None
    estimated_paid_cost_usd: float = 0.0
    actual_paid_cost_usd: float | None = None
    paid_request_count: int = 0
    local_browser_seconds: float = 0.0
    http_request_count: int = 0
    cache_hit_count: int = 0
    blocked_actions: list[BlockedAction] = field(default_factory=list)
    route_steps: list[RouteStep] = field(default_factory=list)
    command: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": ACCOUNTING_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "budget_limit_usd": _round_money_or_none(self.budget_limit_usd),
            "estimated_paid_cost_usd": round(float(self.estimated_paid_cost_usd), 6),
            "actual_paid_cost_usd": _round_money_or_none(self.actual_paid_cost_usd),
            "paid_request_count": int(self.paid_request_count),
            "local_browser_seconds": round(float(self.local_browser_seconds), 3),
            "http_request_count": int(self.http_request_count),
            "cache_hit_count": int(self.cache_hit_count),
            "blocked_actions": [item.to_dict() for item in self.blocked_actions],
            "route_steps": [step.to_dict() for step in self.route_steps],
        }
        if self.command:
            payload["command"] = self.command
        if self.metadata:
            payload["metadata"] = _jsonable(self.metadata)
        return payload


def effective_budget_limit(*limits: float | None) -> float | None:
    """Return the strictest configured non-negative cost cap."""
    parsed = [float(limit) for limit in limits if limit is not None]
    if not parsed:
        return None
    return min(parsed)


def parse_budget_value(value: str) -> float:
    """Parse a non-negative budget CLI value."""
    try:
        parsed = float(value)
    except ValueError as err:
        raise ValueError("budget must be a number") from err
    if parsed < 0:
        raise ValueError("budget must be at least 0")
    return parsed


def extract_budget_flags(argv: list[str] | None) -> tuple[list[str] | None, float | None, bool]:
    """Strip cross-command ``--budget`` and ``--explain-route`` flags.

    This supports subcommands where adding a parent argparse option would force
    users to place the flag before the subcommand.
    """
    if argv is None:
        return None, None, False
    cleaned: list[str] = []
    budget: float | None = None
    explain_route = False
    index = 0
    while index < len(argv):
        item = argv[index]
        if item == "--explain-route":
            explain_route = True
            index += 1
            continue
        if item == "--budget":
            if index + 1 >= len(argv):
                raise ValueError("--budget requires a value")
            budget = parse_budget_value(argv[index + 1])
            index += 2
            continue
        if item.startswith("--budget="):
            budget = parse_budget_value(item.split("=", 1)[1])
            index += 1
            continue
        cleaned.append(item)
        index += 1
    return cleaned, budget, explain_route


def paid_action_blocked(
    budget_limit_usd: float | None,
    *,
    estimated_cost_usd: float | None = None,
) -> bool:
    """Return whether a paid-capable action must be blocked."""
    if budget_limit_usd is None:
        return False
    estimated = float(estimated_cost_usd or 0.0)
    return estimated > float(budget_limit_usd) or float(budget_limit_usd) <= 0.0


def blocked_action(
    action: str,
    *,
    budget_limit_usd: float | None,
    estimated_cost_usd: float | None = None,
    provider: str | None = None,
) -> BlockedAction:
    reason = (
        f"paid-capable action blocked by budget ${float(budget_limit_usd or 0.0):.6f}"
        if budget_limit_usd is not None
        else "paid-capable action blocked"
    )
    return BlockedAction(
        action=action,
        reason=reason,
        estimated_cost_usd=estimated_cost_usd,
        provider=provider,
    )


def enforce_paid_budget(
    action: str,
    *,
    budget_limit_usd: float | None,
    estimated_cost_usd: float | None = None,
    provider: str | None = None,
) -> None:
    """Raise before invoking a paid-capable provider or cloud backend."""
    if paid_action_blocked(budget_limit_usd, estimated_cost_usd=estimated_cost_usd):
        item = blocked_action(
            action,
            budget_limit_usd=budget_limit_usd,
            estimated_cost_usd=estimated_cost_usd,
            provider=provider,
        )
        estimate = "" if estimated_cost_usd is None else f" Estimated cost: ${estimated_cost_usd:.6f}."
        raise BudgetError(f"{item.reason}: {action}.{estimate}")


def budget_block_payload(
    action: str,
    *,
    budget_limit_usd: float | None,
    estimated_cost_usd: float | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Structured dry-run payload for a budget-blocked action."""
    item = blocked_action(
        action,
        budget_limit_usd=budget_limit_usd,
        estimated_cost_usd=estimated_cost_usd,
        provider=provider,
    )
    return {
        "blocked_by_budget": True,
        "budget_limit_usd": _round_money_or_none(budget_limit_usd),
        "blocked_action": item.to_dict(),
    }


def default_route_steps(
    *,
    include_local_render: bool = False,
    include_provider: bool = False,
    include_cloud: bool = False,
    budget_limit_usd: float | None = None,
) -> list[RouteStep]:
    """Return the standard cheapest-capable route ladder for explanations."""
    steps = [
        RouteStep("cache", "available", detail="reuse local artifacts when fresh"),
        RouteStep("direct_http", "available", detail="fetch public HTTPS source directly"),
        RouteStep("sitemap_link_discovery", "available", detail="use sitemap and static links"),
        RouteStep("embedded_data_extraction", "available", detail="use framework/static page data"),
        RouteStep(
            "archive_fallback",
            "available",
            detail="replay Wayback CDX and Common Crawl index snapshots at zero cost",
        ),
    ]
    steps.append(
        RouteStep(
            "local_render",
            "available" if include_local_render else "off",
            detail="local agent-browser is treated as local/free when explicitly enabled",
        )
    )
    provider_status = "blocked_by_budget" if include_provider and budget_limit_usd == 0 else "available"
    steps.append(
        RouteStep(
            "byok_provider",
            provider_status if include_provider else "off",
            cost_class="paid-capable",
            detail="Tavily, Exa, and Parallel require explicit opt-in",
        )
    )
    cloud_status = "blocked_by_budget" if include_cloud and budget_limit_usd == 0 else "available"
    steps.append(
        RouteStep(
            "hosted_cloud",
            cloud_status if include_cloud else "off",
            cost_class="paid-capable",
            detail="cloud rendering or hosted execution",
        )
    )
    return steps


def write_run_accounting(output_dir: Path, accounting: RunAccounting) -> Path:
    """Write ``run.accounting.json`` under ``output_dir``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / ACCOUNTING_ARTIFACT
    path.write_text(json.dumps(accounting.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _reference_accounting_artifact(output_dir, path)
    return path


def maybe_write_run_accounting(
    output_dir: Path,
    *,
    budget_limit_usd: float | None,
    paid_capable: bool = False,
    accounting: RunAccounting,
) -> Path | None:
    """Write accounting only when a budget or paid-capable route is involved."""
    if budget_limit_usd is None and not paid_capable and not accounting.blocked_actions:
        return None
    return write_run_accounting(output_dir, accounting)


def _round_money_or_none(value: float | None) -> float | None:
    return None if value is None else round(float(value), 6)


def _reference_accounting_artifact(output_dir: Path, accounting_path: Path) -> None:
    """Link accounting from pack metadata and agent context when those files exist."""
    relative = accounting_path.name
    _reference_accounting_from_agent_context(output_dir / "AGENT_CONTEXT.md", relative)
    for pack_path in sorted(output_dir.glob("*.pack.json")):
        _reference_accounting_from_pack_metadata(pack_path, relative)


def _reference_accounting_from_agent_context(path: Path, relative: str) -> None:
    if not path.exists():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    if relative in content:
        return
    section = (
        "\n\n## Run Accounting\n\n"
        f"- `{relative}` - non-secret budget, route, HTTP/cache, browser, and blocked-action accounting.\n"
    )
    path.write_text(content.rstrip() + section, encoding="utf-8")


def _reference_accounting_from_pack_metadata(path: Path, relative: str) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        data["artifacts"] = artifacts
    if artifacts.get("accounting") == relative:
        return
    artifacts["accounting"] = relative
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value)
    except TypeError:
        if isinstance(value, dict):
            return {str(key): _jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(item) for item in value]
        return str(value)
    return value
