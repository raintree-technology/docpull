"""Command-line interface for the isolated DocPull evaluation lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from .adapters import (
    AdapterError,
    CommandAdapter,
    ContextCrawlAdapter,
    ContextMarkdownAdapter,
    Crawl4AIAdapter,
    DocPullAdapter,
    ExaContentsAdapter,
    ExaFullContentsAdapter,
    ExaSearchAdapter,
    FirecrawlCrawlAdapter,
    FirecrawlScrapeAdapter,
    FirecrawlSearchAdapter,
    ParallelFullExtractAdapter,
    ParallelSearchAdapter,
    ReadabilityAdapter,
    ReplayAdapter,
    SystemAdapter,
    TavilyAdvancedExtractAdapter,
    TavilyCrawlAdapter,
    TavilyExtractAdapter,
    TavilyGuidedAdvancedCrawlAdapter,
    TavilySearchAdapter,
    TrafilaturaAdapter,
)
from .baselines import check_baseline, update_baseline
from .challenges import export_blinded_challenge, materialize_blinded_challenge, seal_blinded_gold
from .claims import (
    ClaimEvidence,
    ClaimPolicy,
    ClaimReadinessReport,
    check_claim_readiness,
    claim_readiness_markdown,
)
from .comparison import compare_reports, comparison_markdown
from .fixtures import verify_fixture_manifest
from .models import (
    BenchmarkInput,
    BenchmarkSuite,
    ComparisonReport,
    Lane,
    PortableReport,
    RunObservation,
)
from .publication import publish_results, sign_publication, verify_publication
from .runner import run_suite


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    validate = actions.add_parser("validate", help="validate suite schema, freshness, and fixtures")
    validate.add_argument("suite", type=Path)
    validate.add_argument("--allow-stale-gold", action="store_true")
    validate.add_argument("--claim-grade", action="store_true")
    listing = actions.add_parser("list", help="list suite cases")
    listing.add_argument("suite", type=Path)
    listing.add_argument("--json", action="store_true")
    schema = actions.add_parser("schema", help="emit a portable JSON schema")
    schema.add_argument(
        "--kind",
        choices=("suite", "input", "observation", "report", "comparison", "claim"),
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
    context = actions.add_parser("context", help="run the controlled context-dependency product profile")
    context.add_argument("--output-dir", type=Path, default=Path("bench/runs/context"))
    context.add_argument("--repeat", type=int, default=1)
    context.add_argument("--max-concurrency", type=int, default=1)
    context.add_argument("--json", action="store_true")

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

    publish = actions.add_parser("publish", help="create, sign, or verify a publication bundle")
    publish_actions = publish.add_subparsers(dest="publish_action", required=True)
    publish_create = publish_actions.add_parser("create", help="build a data and methodology bundle")
    publish_create.add_argument("suite", type=Path)
    publish_create.add_argument("reports", nargs="+", type=Path)
    publish_create.add_argument("--output-dir", type=Path, required=True)
    publish_create.add_argument("--unavailable", action="append", default=[])
    publish_create.add_argument("--provisional", action="store_true")
    publish_sign = publish_actions.add_parser("sign", help="GPG-sign a verified publication manifest")
    publish_sign.add_argument("bundle", type=Path)
    publish_sign.add_argument("--key")
    publish_verify = publish_actions.add_parser(
        "verify", help="recompute hashes, reports, and comparison for a publication"
    )
    publish_verify.add_argument("bundle", type=Path)
    publish_verify.add_argument("--trusted-gpg-fingerprint")

    claim = actions.add_parser("claim", help="fail-closed public-claim evidence gates").add_subparsers(
        dest="claim_action", required=True
    )
    claim_check = claim.add_parser("check", help="verify claim readiness without generating claims")
    claim_check.add_argument("suite", type=Path)
    claim_check.add_argument("reports", nargs="+", type=Path)
    claim_check.add_argument("--policy", type=Path, default=Path("bench/claim/policy-v2.yaml"))
    claim_check.add_argument("--evidence", type=Path)
    claim_check.add_argument("--output", type=Path)
    claim_check.add_argument("--markdown", type=Path)
    claim_check.add_argument("--json", action="store_true")

    challenge = actions.add_parser(
        "challenge", help="package never-published inputs separately from private gold"
    ).add_subparsers(dest="challenge_action", required=True)
    challenge_export = challenge.add_parser("export", help="split a private suite draft")
    challenge_export.add_argument("suite", type=Path)
    challenge_export.add_argument("--challenge", type=Path, required=True)
    challenge_export.add_argument("--gold", type=Path, required=True)
    challenge_export.add_argument("--manifest", type=Path, required=True)
    challenge_export.add_argument("--minimum-cases-per-lane", type=int, default=100)
    challenge_export.add_argument("--minimum-holdout-cases-per-lane", type=int, default=30)
    challenge_materialize = challenge.add_parser(
        "materialize", help="recombine a challenge with decrypted private gold"
    )
    challenge_materialize.add_argument("challenge", type=Path)
    challenge_materialize.add_argument("gold", type=Path)
    challenge_materialize.add_argument("--output", type=Path, required=True)
    challenge_seal = challenge.add_parser("seal", help="encrypt private gold with an age recipient")
    challenge_seal.add_argument("gold", type=Path)
    challenge_seal.add_argument("--ciphertext", type=Path, required=True)
    challenge_seal.add_argument("--recipient", required=True)
    challenge_seal.add_argument("--manifest", type=Path, required=True)
    return parser


def _add_run_arguments(run: argparse.ArgumentParser) -> None:
    run.add_argument("suite", type=Path)
    run.add_argument("--system", required=True)
    run.add_argument("--version", default="unknown")
    run.add_argument(
        "--adapter",
        choices=(
            "docpull",
            "trafilatura",
            "readability",
            "crawl4ai",
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
            "firecrawl",
            "firecrawl-crawl",
            "firecrawl-search",
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
    run.add_argument(
        "--docpull-python",
        type=Path,
        help="Python interpreter containing the isolated DocPull subject installation",
    )
    run.add_argument(
        "--subject-artifact",
        type=Path,
        help="Exact DocPull wheel installed in --docpull-python",
    )
    run.add_argument("--evidence-dir", type=Path, help="External directory for encrypted output escrow")
    run.add_argument("--evidence-recipient", help="age recipient for direct output encryption")
    run.add_argument("--json", action="store_true")


def main(argv: list[str] | None = None) -> int:
    normalized_argv = list(argv) if argv is not None else list(sys.argv[1:])
    if (
        normalized_argv
        and normalized_argv[0] == "publish"
        and len(normalized_argv) > 1
        and normalized_argv[1] not in {"create", "sign", "verify"}
    ):
        normalized_argv.insert(1, "create")
    args = _build_parser().parse_args(normalized_argv)
    try:
        if args.action == "validate":
            suite = _validate_suite(
                args.suite,
                allow_stale=args.allow_stale_gold,
                claim_grade=args.claim_grade,
            )
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
        if args.action == "context":
            return _context(args)
        if args.action == "compare":
            return _compare(args)
        if args.action == "baseline":
            return _baseline(args)
        if args.action == "publish":
            if args.publish_action == "create":
                output = publish_results(
                    args.suite,
                    args.reports,
                    output_dir=args.output_dir,
                    unavailable=args.unavailable,
                    provisional=args.provisional,
                )
                print(f"publication: {output}")
            elif args.publish_action == "sign":
                print(f"signature: {sign_publication(args.bundle, key=args.key)}")
            elif args.publish_action == "verify":
                print(
                    json.dumps(
                        verify_publication(
                            args.bundle,
                            trusted_gpg_fingerprint=args.trusted_gpg_fingerprint,
                        ),
                        indent=2,
                    )
                )
            else:
                raise AssertionError(f"unhandled publish action: {args.publish_action}")
            return 0
        if args.action == "claim":
            return _claim(args)
        if args.action == "challenge":
            return _challenge(args)
    except (AdapterError, OSError, ValueError, ValidationError) as error:
        print(f"docpull-bench: {error}", file=sys.stderr)
        return 2
    raise AssertionError(f"unhandled action: {args.action}")


def _validate_suite(
    path: Path,
    *,
    allow_stale: bool,
    claim_grade: bool = False,
) -> BenchmarkSuite:
    from .runner import _validate_freshness

    suite = BenchmarkSuite.from_yaml(path)
    _validate_freshness(suite, allow_stale=allow_stale)
    if suite.fixture_manifest_sha256:
        manifest = path.parents[1] / "fixtures" / "manifest.json"
        verify_fixture_manifest(manifest)
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        if digest != suite.fixture_manifest_sha256:
            raise ValueError("suite fixture manifest hash does not match")
    if claim_grade:
        _validate_claim_grade_suite(suite)
    return suite


def _validate_claim_grade_suite(suite: BenchmarkSuite) -> None:
    from .claims import ClaimPolicy

    policy = ClaimPolicy.from_yaml(Path(__file__).resolve().parents[2] / "claim" / "policy-v2.yaml")
    by_lane: dict[Lane, list[Any]] = defaultdict(list)
    for case in suite.cases:
        by_lane[case.input.lane].append(case)
        if "comparison_scope" not in case.metadata.model_fields_set:
            raise ValueError(f"claim-grade case {case.id} must predeclare comparison_scope")
        if case.metadata.live:
            if not case.metadata.reference_checked_at or not case.metadata.reference_expires_at:
                raise ValueError(f"claim-grade case {case.id} requires fresh references")
            if date.fromisoformat(case.metadata.reference_expires_at) < date.today():
                raise ValueError(f"claim-grade case {case.id} has stale references")
        if case.input.lane in {Lane.EXTRACT, Lane.CRAWL, Lane.PARSE}:
            expected = case.expected
            duplicate_limit = getattr(expected, "maximum_duplicate_rate", None)
            effective_duplicate_limit = duplicate_limit is not None and duplicate_limit < 1
            check_count = (
                int(expected.minimum_records > 0)
                + int(expected.minimum_content_chars > 0)
                + int(expected.maximum_content_chars is not None)
                + len(expected.required_terms)
                + len(expected.forbidden_terms)
                + int(bool(expected.required_ordered_terms))
                + int(expected.maximum_long_token_rate is not None)
                + int(expected.minimum_markdown_links > 0)
                + int(expected.minimum_fenced_code_blocks > 0)
                + int(expected.minimum_markdown_table_rows > 0)
                + len(getattr(expected, "required_urls", []))
                + len(getattr(expected, "required_headings", []))
                + len(getattr(expected, "required_metadata", {}))
                + int(effective_duplicate_limit)
            )
            if check_count < 5:
                raise ValueError(
                    f"claim-grade content case {case.id} requires at least five independent evidence checks"
                )
            has_cleanliness = bool(
                expected.forbidden_terms
                or expected.maximum_content_chars is not None
                or expected.maximum_long_token_rate is not None
                or effective_duplicate_limit
            )
            if not has_cleanliness:
                raise ValueError(
                    f"claim-grade content case {case.id} requires a cleanliness or upper-bound check"
                )
    for lane, cases in by_lane.items():
        families = Counter(case.metadata.family for case in cases)
        if len(cases) < policy.minimum_cases_per_lane:
            raise ValueError(f"claim-grade lane {lane.value} has too few cases")
        if sum(case.metadata.split == "test" for case in cases) < policy.minimum_test_cases_per_lane:
            raise ValueError(f"claim-grade lane {lane.value} has too few test cases")
        if len(families) < policy.minimum_families_per_lane:
            raise ValueError(f"claim-grade lane {lane.value} has too few families")
        if max(families.values()) / len(cases) > policy.maximum_family_share:
            raise ValueError(f"claim-grade lane {lane.value} exceeds maximum family share")


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
        "claim": TypeAdapter(ClaimReadinessReport),
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
        "firecrawl": ("firecrawl", FirecrawlScrapeAdapter),
        "firecrawl-crawl": ("firecrawl-crawl", FirecrawlCrawlAdapter),
        "firecrawl-search": ("firecrawl-search", FirecrawlSearchAdapter),
        "contextdev": ("context.dev", ContextMarkdownAdapter),
        "contextdev-crawl": ("context.dev-crawl", ContextCrawlAdapter),
    }
    local_baselines: dict[str, Callable[[], SystemAdapter]] = {
        "trafilatura": TrafilaturaAdapter,
        "readability": ReadabilityAdapter,
        "crawl4ai": Crawl4AIAdapter,
    }
    if args.adapter == "docpull":
        if args.system != "docpull":
            raise ValueError("docpull adapter requires --system docpull")
        return DocPullAdapter(python_executable=args.docpull_python)
    if args.adapter in local_baselines:
        if args.system != args.adapter:
            raise ValueError(f"{args.adapter} adapter requires --system {args.adapter}")
        return local_baselines[args.adapter]()
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
    adapter = _adapter(args)
    if args.subject_artifact is not None and adapter.system != "docpull":
        raise ValueError("--subject-artifact is only valid with the DocPull adapter")
    report, run_dir = run_suite(
        args.suite,
        adapter,
        output_dir=args.output_dir,
        repeat=args.repeat,
        max_concurrency=args.max_concurrency,
        case_ids=set(args.case_ids) if args.case_ids else None,
        progress=not args.json,
        command=sys.argv,
        environment_label=args.environment_label,
        network_isolation=args.network_isolation,
        allow_stale_gold=args.allow_stale_gold,
        subject_artifact=args.subject_artifact,
        evidence_dir=args.evidence_dir,
        evidence_recipient=args.evidence_recipient,
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
        docpull_python=None,
        subject_artifact=None,
        evidence_dir=None,
        evidence_recipient=None,
        json=args.json,
    )
    return _run(namespace)


def _context(args: argparse.Namespace) -> int:
    """Run every controlled lane that exercises DocPull's context contract."""
    suite_path = Path("bench/cases/controlled-v2.yaml")
    suite = BenchmarkSuite.from_yaml(suite_path)
    context_lanes = {
        Lane.PARSE,
        Lane.PACK,
        Lane.LIFECYCLE,
        Lane.RETRIEVAL,
    }
    case_ids = [case.id for case in suite.cases if case.input.lane in context_lanes]
    namespace = argparse.Namespace(
        suite=suite_path,
        system="docpull",
        version="unknown",
        adapter="docpull",
        command=None,
        allow_env=[],
        replay_dir=None,
        output_dir=args.output_dir,
        case_ids=case_ids,
        repeat=args.repeat,
        max_concurrency=args.max_concurrency,
        max_cost_usd=None,
        environment_label="controlled-local",
        network_isolation="enforced",
        allow_stale_gold=False,
        docpull_python=None,
        subject_artifact=None,
        evidence_dir=None,
        evidence_recipient=None,
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


def _claim(args: argparse.Namespace) -> int:
    if args.claim_action != "check":
        raise AssertionError(f"unhandled claim action: {args.claim_action}")
    policy = ClaimPolicy.from_yaml(args.policy)
    evidence = ClaimEvidence.from_yaml(args.evidence)
    result = check_claim_readiness(
        args.suite,
        args.reports,
        policy=policy,
        evidence=evidence,
        evidence_base=args.evidence.parent if args.evidence else None,
        policy_sha256=hashlib.sha256(args.policy.read_bytes()).hexdigest(),
        evidence_sha256=(
            hashlib.sha256(args.evidence.read_bytes()).hexdigest()
            if args.evidence
            else hashlib.sha256(evidence.model_dump_json().encode()).hexdigest()
        ),
    )
    markdown = claim_readiness_markdown(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
    if args.json:
        print(result.model_dump_json(indent=2))
    elif not args.markdown:
        print(markdown, end="")
    return 0 if result.ready else 1


def _challenge(args: argparse.Namespace) -> int:
    if args.challenge_action == "export":
        manifest = export_blinded_challenge(
            args.suite,
            challenge_path=args.challenge,
            gold_path=args.gold,
            manifest_path=args.manifest,
            minimum_cases_per_lane=args.minimum_cases_per_lane,
            minimum_holdout_cases_per_lane=args.minimum_holdout_cases_per_lane,
        )
        print(manifest.model_dump_json(indent=2))
        return 0
    if args.challenge_action == "materialize":
        suite = materialize_blinded_challenge(args.challenge, args.gold, output_path=args.output)
        print(f"materialized: {suite.name} {suite.version} ({len(suite.cases)} cases)")
        return 0
    if args.challenge_action == "seal":
        artifact = seal_blinded_gold(
            args.gold,
            ciphertext_path=args.ciphertext,
            recipient=args.recipient,
            manifest_path=args.manifest,
        )
        print(artifact.model_dump_json(indent=2))
        return 0
    raise AssertionError(f"unhandled challenge action: {args.challenge_action}")


if __name__ == "__main__":
    raise SystemExit(main())
