"""Command-line interface for the isolated DocPull evaluation lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from .adapters import (
    AdapterError,
    CommandAdapter,
    ContextCrawlAdapter,
    ContextMarkdownAdapter,
    DocPullAdapter,
    ExaContentsAdapter,
    ExaFullContentsAdapter,
    ExaSearchAdapter,
    ParallelFullExtractAdapter,
    ParallelSearchAdapter,
    ReplayAdapter,
    SystemAdapter,
    TavilyAdvancedExtractAdapter,
    TavilyCrawlAdapter,
    TavilyExtractAdapter,
    TavilyGuidedAdvancedCrawlAdapter,
    TavilySearchAdapter,
)
from .baselines import check_baseline, update_baseline
from .comparison import compare_reports, comparison_markdown
from .fixtures import verify_fixture_manifest
from .models import (
    BenchmarkInput,
    BenchmarkSuite,
    ComparisonReport,
    PortableReport,
    RunObservation,
)
from .publication import publish_results
from .runner import run_suite


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    validate = actions.add_parser("validate", help="validate suite schema, freshness, and fixtures")
    validate.add_argument("suite", type=Path)
    validate.add_argument("--allow-stale-gold", action="store_true")
    listing = actions.add_parser("list", help="list suite cases")
    listing.add_argument("suite", type=Path)
    listing.add_argument("--json", action="store_true")
    schema = actions.add_parser("schema", help="emit a portable JSON schema")
    schema.add_argument(
        "--kind",
        choices=("suite", "input", "observation", "report", "comparison"),
        default="suite",
    )
    schema.add_argument("--output", type=Path)

    fixtures = actions.add_parser("fixtures", help="fixture operations").add_subparsers(
        dest="fixtures_action", required=True
    )
    fixtures_verify = fixtures.add_parser("verify", help="verify fixture hashes")
    fixtures_verify.add_argument(
        "manifest", type=Path, nargs="?", default=Path("bench/fixtures/manifest.json")
    )

    run = actions.add_parser("run", help="run a black-box adapter")
    _add_run_arguments(run)
    lifecycle = actions.add_parser("lifecycle", help="alias for the unified lifecycle suite")
    lifecycle.add_argument("--output-dir", type=Path, default=Path("bench/runs/lifecycle"))
    lifecycle.add_argument("--repeat", type=int, default=1)
    lifecycle.add_argument("--json", action="store_true")

    compare = actions.add_parser("compare", help="compare matching suite and protocol reports")
    compare.add_argument("reports", nargs="+", type=Path)
    compare.add_argument("--output", type=Path)
    compare.add_argument("--markdown", type=Path)
    compare.add_argument("--json", action="store_true")

    baseline = actions.add_parser("baseline", help="controlled baseline operations").add_subparsers(
        dest="baseline_action", required=True
    )
    baseline_check = baseline.add_parser("check")
    baseline_check.add_argument("report", type=Path)
    baseline_check.add_argument("baseline", type=Path)
    baseline_check.add_argument("--output", type=Path)
    baseline_update = baseline.add_parser("update")
    baseline_update.add_argument("report", type=Path)
    baseline_update.add_argument("baseline", type=Path)
    baseline_update.add_argument("--reason", required=True)

    publish = actions.add_parser("publish", help="build data and methodology bundle")
    publish.add_argument("suite", type=Path)
    publish.add_argument("reports", nargs="+", type=Path)
    publish.add_argument("--output-dir", type=Path, required=True)
    publish.add_argument("--unavailable", action="append", default=[])
    publish.add_argument("--provisional", action="store_true")
    return parser


def _add_run_arguments(run: argparse.ArgumentParser) -> None:
    run.add_argument("suite", type=Path)
    run.add_argument("--system", required=True)
    run.add_argument("--version", default="unknown")
    run.add_argument(
        "--adapter",
        choices=(
            "docpull",
            "tavily",
            "tavily-advanced",
            "tavily-crawl",
            "tavily-crawl-guided",
            "tavily-search",
            "exa",
            "exa-full",
            "exa-search",
            "parallel",
            "parallel-search",
            "contextdev",
            "contextdev-crawl",
            "command",
            "replay",
        ),
        default="docpull",
    )
    run.add_argument("--command")
    run.add_argument("--allow-env", action="append", default=[])
    run.add_argument("--replay-dir", type=Path)
    run.add_argument("--output-dir", type=Path, default=Path("bench/runs"))
    run.add_argument("--case", action="append", dest="case_ids")
    run.add_argument("--repeat", type=int, default=1)
    run.add_argument("--max-concurrency", type=int, default=1)
    run.add_argument("--max-cost-usd", type=float)
    run.add_argument("--environment-label", default="local")
    run.add_argument(
        "--network-isolation", choices=("enforced", "best_effort", "open"), default="best_effort"
    )
    run.add_argument("--allow-stale-gold", action="store_true")
    run.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.action == "validate":
            suite = _validate_suite(args.suite, allow_stale=args.allow_stale_gold)
            print(f"valid: {suite.name} {suite.version} ({len(suite.cases)} cases)")
            return 0
        if args.action == "list":
            return _list_cases(args.suite, args.json)
        if args.action == "schema":
            return _schema(args.kind, args.output)
        if args.action == "fixtures":
            payload = verify_fixture_manifest(args.manifest)
            print(f"valid: {len(payload['files'])} fixture files")
            return 0
        if args.action == "run":
            return _run(args)
        if args.action == "lifecycle":
            return _lifecycle(args)
        if args.action == "compare":
            return _compare(args)
        if args.action == "baseline":
            return _baseline(args)
        if args.action == "publish":
            output = publish_results(
                args.suite,
                args.reports,
                output_dir=args.output_dir,
                unavailable=args.unavailable,
                provisional=args.provisional,
            )
            print(f"publication: {output}")
            return 0
    except (AdapterError, OSError, ValueError, ValidationError) as error:
        print(f"docpull-bench: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled action: {args.action}")


def _validate_suite(path: Path, *, allow_stale: bool) -> BenchmarkSuite:
    from .runner import _validate_freshness

    suite = BenchmarkSuite.from_yaml(path)
    _validate_freshness(suite, allow_stale=allow_stale)
    if suite.fixture_manifest_sha256:
        manifest = path.parents[1] / "fixtures" / "manifest.json"
        verify_fixture_manifest(manifest)
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if digest != suite.fixture_manifest_sha256:
            raise ValueError("suite fixture manifest hash does not match")
    return suite


def _list_cases(path: Path, as_json: bool) -> int:
    suite = BenchmarkSuite.from_yaml(path)
    rows = [
        {
            "id": case.id,
            "lane": case.input.lane.value,
            "split": case.metadata.split,
            "live": case.metadata.live,
            "critical": case.metadata.critical,
            "description": case.metadata.description,
        }
        for case in suite.cases
    ]
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for row in rows:
            mode = "live" if row["live"] else "controlled"
            print(f"{row['id']:<42} {row['lane']:<12} {row['split']:<5} {mode}")
    return 0


def _schema(kind: str, output: Path | None) -> int:
    adapters: dict[str, TypeAdapter[Any]] = {
        "suite": TypeAdapter(BenchmarkSuite),
        "input": TypeAdapter(BenchmarkInput),
        "observation": TypeAdapter(RunObservation),
        "report": TypeAdapter(PortableReport),
        "comparison": TypeAdapter(ComparisonReport),
    }
    payload = json.dumps(adapters[kind].json_schema(), indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0


def _adapter(args: argparse.Namespace) -> SystemAdapter:
    hosted = {
        "tavily": ("tavily", TavilyExtractAdapter),
        "tavily-advanced": ("tavily-advanced", TavilyAdvancedExtractAdapter),
        "tavily-crawl": ("tavily-crawl-basic", TavilyCrawlAdapter),
        "tavily-crawl-guided": (
            "tavily-crawl-guided-advanced",
            TavilyGuidedAdvancedCrawlAdapter,
        ),
        "tavily-search": ("tavily-search", TavilySearchAdapter),
        "exa": ("exa", ExaContentsAdapter),
        "exa-full": ("exa-full", ExaFullContentsAdapter),
        "exa-search": ("exa-search", ExaSearchAdapter),
        "parallel": ("parallel", ParallelFullExtractAdapter),
        "parallel-search": ("parallel-search", ParallelSearchAdapter),
        "contextdev": ("context.dev", ContextMarkdownAdapter),
        "contextdev-crawl": ("context.dev-crawl", ContextCrawlAdapter),
    }
    if args.adapter == "docpull":
        if args.system != "docpull":
            raise ValueError("docpull adapter requires --system docpull")
        return DocPullAdapter()
    if args.adapter in hosted:
        if args.max_cost_usd is None:
            raise ValueError("hosted adapters require --max-cost-usd")
        expected, factory = hosted[args.adapter]
        if args.system != expected:
            raise ValueError(f"{args.adapter} adapter requires --system {expected}")
        return cast(SystemAdapter, factory(max_cost_usd=args.max_cost_usd))
    if args.adapter == "command":
        if not args.command:
            raise ValueError("command adapter requires --command")
        return CommandAdapter(
            system=args.system,
            version=args.version,
            command=args.command,
            allowed_env=args.allow_env,
        )
    if not args.replay_dir:
        raise ValueError("replay adapter requires --replay-dir")
    return ReplayAdapter(system=args.system, version=args.version, replay_dir=args.replay_dir)


def _run(args: argparse.Namespace) -> int:
    if args.repeat < 1 or args.max_concurrency < 1:
        raise ValueError("repeat and concurrency must be at least one")
    report, run_dir = run_suite(
        args.suite,
        _adapter(args),
        output_dir=args.output_dir,
        repeat=args.repeat,
        max_concurrency=args.max_concurrency,
        case_ids=set(args.case_ids) if args.case_ids else None,
        progress=not args.json,
        command=sys.argv,
        environment_label=args.environment_label,
        network_isolation=args.network_isolation,
        allow_stale_gold=args.allow_stale_gold,
    )
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(f"report: {run_dir / 'report.json'}")
        print(
            f"completed {report.summary.completed}/{report.summary.case_runs}; "
            f"trial pass {report.summary.trial_pass_rate:.1%}; "
            f"pass^{report.summary.repeat} {report.summary.pass_all_trials_rate:.1%}"
        )
    return 0


def _lifecycle(args: argparse.Namespace) -> int:
    namespace = argparse.Namespace(
        suite=Path("bench/cases/lifecycle-v2.yaml"),
        system="docpull",
        version="unknown",
        adapter="docpull",
        command=None,
        allow_env=[],
        replay_dir=None,
        output_dir=args.output_dir,
        case_ids=None,
        repeat=args.repeat,
        max_concurrency=1,
        max_cost_usd=None,
        environment_label="local",
        network_isolation="best_effort",
        allow_stale_gold=False,
        json=args.json,
    )
    return _run(namespace)


def _compare(args: argparse.Namespace) -> int:
    comparison = compare_reports(args.reports)
    markdown = comparison_markdown(comparison)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(comparison.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
    if args.json:
        print(comparison.model_dump_json(indent=2))
    elif not args.markdown:
        print(markdown, end="")
    return 0


def _baseline(args: argparse.Namespace) -> int:
    if args.baseline_action == "update":
        payload = update_baseline(args.report, args.baseline, reason=args.reason)
        print(json.dumps(payload, indent=2))
        return 0
    result, passed = check_baseline(args.report, args.baseline)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
