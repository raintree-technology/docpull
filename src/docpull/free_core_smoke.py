"""Real-site free-core acceptance smoke harness."""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .free_core import (
    answer_target,
    batch_scrape,
    brief_target,
    crawl_url,
    entities_target,
    extract_target,
    image_target,
    map_url,
    monitor_target,
    screenshot_url,
)
from .local_workflows import audit_pack
from .policy import PolicyConfig
from .time_utils import utc_now_iso

SMOKE_SCHEMA_VERSION = 1
DEFAULT_OUTPUT_DIR = Path("packs/free-core-smoke")
PYTHON_URL = "https://www.python.org"
PYTHON_DOWNLOADS_URL = "https://www.python.org/downloads/"
BOOK_PRODUCT_URL = "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
PYTHON_DOCS_URL = "https://docs.python.org/3/library/http.html"
FASTAPI_URL = "https://fastapi.tiangolo.com/"


class FreeCoreSmokeError(RuntimeError):
    """User-facing smoke harness error."""


@dataclass(frozen=True)
class SmokeCase:
    name: str
    workflow: str
    description: str


SMOKE_CASES: tuple[SmokeCase, ...] = (
    SmokeCase("ordinary-map", "map", "Map ordinary static website links and site signals."),
    SmokeCase("batch-scrape", "batch", "Fetch multiple ordinary website URLs into one pack."),
    SmokeCase("local-answer", "answer", "Answer from the local ordinary-site pack with citations."),
    SmokeCase("local-entities", "entities", "Extract cited entities from the local ordinary-site pack."),
    SmokeCase("local-brief", "brief", "Prepare cited brief/search/entity artifacts from the pack."),
    SmokeCase("visual-assets", "images", "Extract image candidates from an existing pack."),
    SmokeCase("brand", "extract", "Extract brand evidence from a public website."),
    SmokeCase("styleguide", "extract", "Extract design tokens and component samples from CSS/HTML."),
    SmokeCase("product-pricing", "extract", "Extract product/pricing evidence from a public product page."),
    SmokeCase("docs-scrape", "batch", "Fetch a public documentation page into a local pack."),
    SmokeCase("docs-search", "search", "Search the fetched documentation pack locally."),
    SmokeCase("monitor-dry-run", "monitor", "Create and dry-run a local monitor config."),
    SmokeCase("redaction-audit", "audit", "Audit the local pack and scan deterministic redaction patterns."),
    SmokeCase(
        "screenshot-dry-run",
        "screenshot",
        "Validate screenshot workflow without launching a browser.",
    ),
)

DEEP_SMOKE_CASES: tuple[SmokeCase, ...] = (
    SmokeCase(
        "recursive-docs-crawl",
        "crawl",
        "Recursively discover and crawl useful same-origin docs pages.",
    ),
    SmokeCase(
        "schema-product-fields",
        "extract",
        "Extract common product fields through a user schema.",
    ),
)

CASE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "local-answer": ("batch-scrape",),
    "local-entities": ("batch-scrape",),
    "local-brief": ("batch-scrape",),
    "visual-assets": ("batch-scrape",),
    "monitor-dry-run": ("batch-scrape",),
    "redaction-audit": ("batch-scrape",),
    "docs-search": ("docs-scrape",),
}


def run_free_core_smoke_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docpull free-core-smoke",
        description="Run the built-in free/local real-site acceptance matrix",
    )
    parser.add_argument("--output-dir", "-o", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--case", action="append", dest="cases", default=[], help="Run one case by name")
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Include slower/deeper real-site acceptance cases",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write the planned matrix without network")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--keep-going", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    console = Console()
    try:
        payload = run_free_core_smoke(
            output_dir=args.output_dir,
            case_names=args.cases or None,
            deep=args.deep,
            dry_run=args.dry_run,
            keep_going=args.keep_going,
        )
    except FreeCoreSmokeError as err:
        console.print("[red]Free-core smoke error:[/red] " + escape(str(err)))
        return 1
    if args.json_output:
        console.print_json(data=payload)
    else:
        console.print(
            "[green]Free-core smoke:[/green] "
            f"{payload['summary']['passed_count']} passed, "
            f"{payload['summary']['failed_count']} failed -> {payload['artifacts']['json']}"
        )
    return 0 if payload["summary"]["failed_count"] == 0 else 1


def run_free_core_smoke(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    case_names: list[str] | None = None,
    deep: bool = False,
    dry_run: bool = False,
    keep_going: bool = True,
) -> dict[str, Any]:
    selected = _selected_cases(case_names, deep=deep)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    context: dict[str, Any] = {}
    results: list[dict[str, Any]] = []

    if dry_run:
        results = [_planned_case(case, output_dir=output_dir) for case in selected]
    else:
        for case in selected:
            result = _run_case(case, output_dir=output_dir, context=context)
            results.append(result)
            if result["status"] == "failed" and not keep_going:
                break

    passed = sum(1 for item in results if item["status"] in {"passed", "planned"})
    failed = sum(1 for item in results if item["status"] == "failed")
    payload: dict[str, Any] = {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "dry_run": dry_run,
        "suite": "deep" if deep else "quick",
        "output_dir": str(output_dir),
        "summary": {
            "case_count": len(results),
            "passed_count": passed if not dry_run else 0,
            "planned_count": passed if dry_run else 0,
            "failed_count": failed,
            "duration_seconds": round(time.monotonic() - started, 3),
            "paid_provider_calls": 0,
            "budget_limit_usd": 0,
        },
        "sites": {
            "ordinary": PYTHON_URL,
            "ordinary_secondary": PYTHON_DOWNLOADS_URL,
            "product_pricing": BOOK_PRODUCT_URL,
            "docs": PYTHON_DOCS_URL,
            "recursive_docs": FASTAPI_URL,
        },
        "cases": results,
        "artifacts": {
            "json": str(output_dir / "free-core-smoke.result.json"),
            "markdown": str(output_dir / "FREE_CORE_SMOKE.md"),
            "cases_ndjson": str(output_dir / "free-core-smoke.cases.ndjson"),
        },
    }
    _write_json(Path(payload["artifacts"]["json"]), payload)
    Path(payload["artifacts"]["markdown"]).write_text(_smoke_markdown(payload), encoding="utf-8")
    Path(payload["artifacts"]["cases_ndjson"]).write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in results),
        encoding="utf-8",
    )
    return payload


def _run_case(case: SmokeCase, *, output_dir: Path, context: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    case_dir = output_dir / case.name
    try:
        payload = _case_action(case.name, case_dir=case_dir, context=context)()
        _remember_case_output(case.name, payload, case_dir=case_dir, context=context)
        return {
            **_case_base(case, output_dir=case_dir, status="passed", started=started),
            "summary": payload.get("summary") if isinstance(payload, dict) else None,
            "artifacts": payload.get("artifacts") if isinstance(payload, dict) else {},
            "quality": payload.get("quality") if isinstance(payload, dict) else None,
        }
    except Exception as err:  # noqa: BLE001
        return {
            **_case_base(case, output_dir=case_dir, status="failed", started=started),
            "error": str(err),
        }


def _case_action(
    name: str,
    *,
    case_dir: Path,
    context: dict[str, Any],
) -> Callable[[], dict[str, Any]]:
    policy = PolicyConfig()
    actions: dict[str, Callable[[], dict[str, Any]]] = {
        "ordinary-map": lambda: map_url(PYTHON_URL, output_dir=case_dir, policy=policy, max_results=25),
        "batch-scrape": lambda: batch_scrape(
            [PYTHON_URL, PYTHON_DOWNLOADS_URL],
            input_path=None,
            output_dir=case_dir,
            policy=policy,
        ),
        "local-answer": lambda: answer_target(
            _required_context(context, "batch_pack"),
            question="What can I download from Python.org?",
            output_dir=case_dir,
            policy=policy,
            limit=8,
        ),
        "local-entities": lambda: entities_target(
            _required_context(context, "batch_pack"),
            output_dir=case_dir,
            policy=policy,
            limit=100,
        ),
        "local-brief": lambda: brief_target(
            _required_context(context, "batch_pack"),
            objective="Summarize Python.org downloads and project context",
            search_queries=None,
            output_dir=case_dir,
            policy=policy,
            max_excerpts=8,
        ),
        "visual-assets": lambda: image_target(
            _required_context(context, "batch_pack"),
            output_dir=case_dir,
            policy=policy,
            download_assets=False,
            max_assets=40,
        ),
        "brand": lambda: extract_target(
            PYTHON_URL,
            schema_path=None,
            preset="brand",
            output_dir=case_dir,
            policy=policy,
        ),
        "styleguide": lambda: extract_target(
            PYTHON_URL,
            schema_path=None,
            preset="styleguide",
            output_dir=case_dir,
            policy=policy,
        ),
        "product-pricing": lambda: extract_target(
            BOOK_PRODUCT_URL,
            schema_path=None,
            preset="product",
            output_dir=case_dir,
            policy=policy,
        ),
        "docs-scrape": lambda: batch_scrape(
            [PYTHON_DOCS_URL],
            input_path=None,
            output_dir=case_dir,
            policy=policy,
        ),
        "docs-search": lambda: answer_target(
            _required_context(context, "docs_pack"),
            question="What does this Python HTTP documentation cover?",
            output_dir=case_dir,
            policy=policy,
            limit=8,
        ),
        "monitor-dry-run": lambda: monitor_target(
            _required_context(context, "batch_pack"),
            name="free-core-smoke",
            state_dir=case_dir / "monitors",
            output_dir=case_dir / "monitor-source",
            run_once=True,
        ),
        "redaction-audit": lambda: audit_pack(
            Path(_required_context(context, "batch_pack")),
            json_path=case_dir / "pack.audit.json",
        ),
        "screenshot-dry-run": lambda: screenshot_url(
            PYTHON_URL,
            output_dir=case_dir,
            policy=policy,
            viewport="1280x720",
            full_page=False,
            wait_for="load",
            agent_browser_binary=None,
            dry_run=True,
        ),
        "recursive-docs-crawl": lambda: _recursive_docs_crawl_case(case_dir=case_dir, policy=policy),
        "schema-product-fields": lambda: _schema_product_fields_case(case_dir=case_dir, policy=policy),
    }
    try:
        return actions[name]
    except KeyError as err:
        raise FreeCoreSmokeError(f"Unknown smoke case: {name}") from err


def _remember_case_output(
    name: str,
    payload: dict[str, Any],
    *,
    case_dir: Path,
    context: dict[str, Any],
) -> None:
    if name == "batch-scrape":
        context["batch_pack"] = str(Path(str(payload.get("output_dir") or case_dir)).resolve())
    elif name == "docs-scrape":
        context["docs_pack"] = str(Path(str(payload.get("output_dir") or case_dir)).resolve())


def _recursive_docs_crawl_case(*, case_dir: Path, policy: PolicyConfig) -> dict[str, Any]:
    payload = crawl_url(
        FASTAPI_URL,
        output_dir=case_dir,
        policy=policy,
        selectors=None,
        max_results=10,
        max_depth=3,
    )
    selected_urls_path = case_dir / "selected_urls.txt"
    selected_urls = selected_urls_path.read_text(encoding="utf-8").splitlines()
    if not any("/tutorial/" in url or "/reference/" in url for url in selected_urls):
        raise FreeCoreSmokeError("recursive-docs-crawl did not select tutorial/reference docs pages")
    if any((urlparse(url).hostname or "") != "fastapi.tiangolo.com" for url in selected_urls):
        raise FreeCoreSmokeError("recursive-docs-crawl selected an off-domain URL")
    payload["quality"] = {
        "selected_url_count": len(selected_urls),
        "has_tutorial_or_reference": True,
        "same_origin_only": True,
    }
    return payload


def _schema_product_fields_case(*, case_dir: Path, policy: PolicyConfig) -> dict[str, Any]:
    case_dir.mkdir(parents=True, exist_ok=True)
    schema_path = case_dir / "schema.json"
    _write_json(
        schema_path,
        {
            "type": "object",
            "required": ["title", "price", "availability"],
            "properties": {
                "title": {"type": "string"},
                "price": {"type": "string"},
                "availability": {"type": "string"},
            },
        },
    )
    payload = extract_target(
        BOOK_PRODUCT_URL,
        schema_path=schema_path,
        preset=None,
        output_dir=case_dir / "extract",
        policy=policy,
    )
    data = payload.get("data") if isinstance(payload, dict) else None
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or not summary.get("validation_valid"):
        raise FreeCoreSmokeError("schema-product-fields did not validate")
    if not isinstance(data, dict) or not data.get("price") or not data.get("availability"):
        raise FreeCoreSmokeError("schema-product-fields missed price or availability")
    payload["quality"] = {
        "price": data.get("price"),
        "availability": data.get("availability"),
    }
    return payload


def _selected_cases(case_names: list[str] | None, *, deep: bool = False) -> list[SmokeCase]:
    if not case_names:
        return list(SMOKE_CASES) + (list(DEEP_SMOKE_CASES) if deep else [])
    by_name = {case.name: case for case in (*SMOKE_CASES, *DEEP_SMOKE_CASES)}
    selected: list[SmokeCase] = []
    selected_names: set[str] = set()
    active: set[str] = set()

    def add_case(name: str) -> None:
        if name in selected_names:
            return
        if name in active:
            raise FreeCoreSmokeError(f"Smoke case dependency cycle includes '{name}'")
        if name not in by_name:
            known = ", ".join(sorted(by_name))
            raise FreeCoreSmokeError(f"Unknown smoke case '{name}'. Known cases: {known}")
        active.add(name)
        for dependency in CASE_DEPENDENCIES.get(name, ()):
            add_case(dependency)
        active.remove(name)
        selected.append(by_name[name])
        selected_names.add(name)

    for name in case_names:
        add_case(name)
    return selected


def _planned_case(case: SmokeCase, *, output_dir: Path) -> dict[str, Any]:
    return _case_base(case, output_dir=output_dir / case.name, status="planned", started=time.monotonic())


def _case_base(case: SmokeCase, *, output_dir: Path, status: str, started: float) -> dict[str, Any]:
    return {
        "schema_version": SMOKE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "name": case.name,
        "workflow": case.workflow,
        "description": case.description,
        "status": status,
        "output_dir": str(output_dir),
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def _required_context(context: dict[str, Any], key: str) -> str:
    value = context.get(key)
    if not value:
        raise FreeCoreSmokeError(f"Smoke case requires prior context: {key}")
    return str(value)


def _smoke_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Free-Core Smoke",
        "",
        f"- Dry run: {payload['dry_run']}",
        f"- Cases: {summary['case_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Planned: {summary['planned_count']}",
        f"- Failed: {summary['failed_count']}",
        f"- Paid provider calls: {summary['paid_provider_calls']}",
        "",
        "## Cases",
        "",
    ]
    for item in payload["cases"]:
        lines.append(f"- {item['status']}: `{item['name']}` ({item['workflow']})")
        if item.get("error"):
            lines.append(f"  Error: {item['error']}")
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
