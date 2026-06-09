"""pass^k analyzer for docpull benchmark reports.

Reads a ``benchmark.report.json`` produced by the harness and reports how
many cases meet a score threshold on *every* trial — the framing the
Anthropic "Demystifying evals for AI agents" post argues for when
consistency is the product claim ("users expect reliable behavior every
time"). Median tells you the typical run; pass^k tells you how often a
case is reliably above bar.

Usage:
    python -m docpull.passk .bench/runs/<run-id>/benchmark.report.json
    python -m docpull.passk <report> --thresholds 70 80 90 --score benchmark
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCORE_KEYS = ("pack_score", "benchmark_score")


def _runs_for(case: dict[str, Any], score_key: str) -> list[int] | None:
    score = case.get(score_key)
    if not isinstance(score, dict):
        return None
    runs = score.get("score_runs")
    if not isinstance(runs, list) or not runs:
        return None
    out: list[int] = []
    for value in runs:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        out.append(int(value))
    return out


def _provider_of(case: dict[str, Any]) -> str:
    return str(case.get("provider") or case.get("workflow") or "unknown")


def pass_at_k(
    cases: Iterable[dict[str, Any]],
    *,
    score_key: str,
    threshold: int,
) -> dict[str, Any]:
    """Fraction of cases whose worst trial still meets ``threshold``.

    Returns a dict with aggregate counts plus a per-provider breakdown and a
    flat list of failing cases so the publication writeup can name them.
    """
    total = 0
    passed = 0
    by_provider: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    failures: list[dict[str, Any]] = []
    for case in cases:
        runs = _runs_for(case, score_key)
        if runs is None:
            continue
        total += 1
        provider = _provider_of(case)
        by_provider[provider]["total"] += 1
        worst = min(runs)
        if worst >= threshold:
            passed += 1
            by_provider[provider]["passed"] += 1
        else:
            failures.append(
                {
                    "name": case.get("name"),
                    "provider": provider,
                    "runs": runs,
                    "worst": worst,
                    "median": sorted(runs)[(len(runs) - 1) // 2],
                }
            )
    rate = passed / total if total else 0.0
    return {
        "score_key": score_key,
        "threshold": threshold,
        "k": _trials_per_case(cases),
        "cases_total": total,
        "cases_passed": passed,
        "rate": rate,
        "by_provider": dict(by_provider),
        "failures": failures,
    }


def _trials_per_case(cases: Iterable[dict[str, Any]]) -> int:
    lengths = {len(_runs_for(c, "benchmark_score") or []) for c in cases}
    lengths.discard(0)
    return max(lengths) if lengths else 0


def _format_table(report: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        f"run: {report.get('run_dir', '<unknown>')}",
        f"target_set: {report.get('target_set')}  cases: {len(report.get('cases', []))}  "
        f"runs_per_case: {report.get('runs_per_case')}",
        "",
        f"{'score':<16} {'threshold':<10} {'k':<3} {'pass^k':<10} {'cases':<10}",
        "-" * 52,
    ]
    for r in results:
        lines.append(
            f"{r['score_key']:<16} {r['threshold']:<10} {r['k']:<3} "
            f"{r['rate']:>6.1%}     {r['cases_passed']}/{r['cases_total']}"
        )
    by_provider = _aggregate_by_provider(results)
    if by_provider:
        lines += ["", "by provider (benchmark_score):"]
        for provider, rows in by_provider.items():
            row = "  ".join(f"@{t}={p:.0%}" for t, p in rows.items())
            lines.append(f"  {provider:<24} {row}")
    failure_lines = _failure_lines(results)
    if failure_lines:
        lines += ["", "fails-any-trial:", *failure_lines]
    return "\n".join(lines)


def _aggregate_by_provider(results: list[dict[str, Any]]) -> dict[str, dict[int, float]]:
    bench_results = [r for r in results if r["score_key"] == "benchmark_score"]
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for r in bench_results:
        for provider, counts in r["by_provider"].items():
            rate = counts["passed"] / counts["total"] if counts["total"] else 0.0
            out[provider][r["threshold"]] = rate
    return dict(out)


def _failure_lines(results: list[dict[str, Any]]) -> list[str]:
    seen: set[tuple[str, str, int]] = set()
    lines: list[str] = []
    for r in results:
        if r["score_key"] != "benchmark_score":
            continue
        for f in r["failures"]:
            key = (str(f["name"]), r["score_key"], r["threshold"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"  @{r['threshold']:<3} {f['name']}  runs={f['runs']}  worst={f['worst']}")
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("report", type=Path, help="path to benchmark.report.json")
    parser.add_argument(
        "--thresholds",
        type=int,
        nargs="+",
        default=[70, 80, 90],
        help="score thresholds to evaluate (default: 70 80 90)",
    )
    parser.add_argument(
        "--score",
        choices=("pack", "benchmark", "both"),
        default="both",
        help="which score to evaluate (default: both)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = json.loads(args.report.read_text())
    cases = report.get("cases") or []
    if args.score == "both":
        score_keys: tuple[str, ...] = SCORE_KEYS
    else:
        score_keys = (f"{args.score}_score",)
    results = [
        pass_at_k(cases, score_key=key, threshold=t) for key in score_keys for t in sorted(args.thresholds)
    ]
    if args.json:
        json.dump(
            {"run_dir": report.get("run_dir"), "results": results},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        print(_format_table(report, results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
