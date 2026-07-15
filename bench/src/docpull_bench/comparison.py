"""Comparable, lane-local statistics for portable benchmark reports."""

from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from statistics import mean, median
from typing import Literal

from .models import (
    CaseScore,
    ComparisonCaseRow,
    ComparisonReport,
    ComparisonRow,
    Lane,
    PairwiseComparisonRow,
    PortableReport,
)

SliceType = Literal["overall", "scope", "split", "family"]


def compare_reports(paths: list[Path]) -> ComparisonReport:
    if len(paths) < 2:
        raise ValueError("comparison requires at least two reports")
    reports = [PortableReport.model_validate_json(path.read_text(encoding="utf-8")) for path in paths]
    first = reports[0].manifest
    for report in reports[1:]:
        if report.manifest.suite_sha256 != first.suite_sha256:
            raise ValueError("reports use different suite hashes")
        if report.manifest.protocol_sha256 != first.protocol_sha256:
            raise ValueError("reports use different protocol hashes")
        if report.manifest.scorer_version != first.scorer_version:
            raise ValueError("reports use different scorer versions")
    systems = [report.manifest.system for report in reports]
    if len(systems) != len(set(systems)):
        raise ValueError("comparison accepts one report per system")

    latency_comparable = (
        len({(report.manifest.environment_label, report.manifest.cache_policy) for report in reports}) == 1
    )
    boundary_cases = _boundary_cases(reports)
    boundary_case_ids = set(boundary_cases)
    rows: list[ComparisonRow] = []
    case_rows: list[ComparisonCaseRow] = []
    for report in reports:
        by_lane: dict[Lane, list[CaseScore]] = defaultdict(list)
        for score in report.scores:
            by_lane[score.lane].append(score)
        for lane, lane_scores in by_lane.items():
            slices: list[tuple[SliceType, str, list[CaseScore]]] = [("overall", "all", lane_scores)]
            if any(score.case_id in boundary_case_ids for score in lane_scores):
                slices.extend(
                    (
                        "scope",
                        value,
                        [
                            score
                            for score in lane_scores
                            if (score.case_id in boundary_case_ids) == (value == "boundary")
                        ],
                    )
                    for value in ("core", "boundary")
                )
            slices.extend(
                ("split", value, [score for score in lane_scores if score.split == value])
                for value in sorted({score.split for score in lane_scores})
            )
            slices.extend(
                ("family", value, [score for score in lane_scores if score.family == value])
                for value in sorted({score.family for score in lane_scores})
            )
            rows.extend(
                _comparison_row(
                    lane,
                    slice_type,
                    slice_value,
                    scores,
                    report,
                    latency_comparable,
                )
                for slice_type, slice_value, scores in slices
            )
            case_rows.extend(_case_rows(lane_scores, report, boundary_case_ids))

    order = {"overall": 0, "scope": 1, "split": 2, "family": 3}
    rows.sort(key=lambda row: (row.lane.value, order[row.slice_type], row.slice_value, row.system))
    case_rows.sort(key=lambda row: (row.lane.value, row.case_id, row.system))
    return ComparisonReport(
        analysis_version="v3-ops-quality-slice-holm-paired-bootstrap",
        suite_name=first.suite_name,
        suite_version=first.suite_version,
        suite_sha256=first.suite_sha256,
        protocol_sha256=first.protocol_sha256,
        scorer_version=first.scorer_version,
        system_count=len(reports),
        source_report_schema_versions=[report.schema_version for report in reports],
        boundary_cases=boundary_cases,
        rows=rows,
        case_rows=case_rows,
        pairwise=_holm_adjust(_pairwise_rows(case_rows)),
    )


def _comparison_row(
    lane: Lane,
    slice_type: SliceType,
    slice_value: str,
    scores: list[CaseScore],
    report: PortableReport,
    latency_comparable: bool,
) -> ComparisonRow:
    by_case: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        by_case[score.case_id].append(score)
    pass_all = {case_id: all(item.passed for item in items) for case_id, items in by_case.items()}
    passed_count = sum(pass_all.values())
    ci_low, ci_high = wilson_interval(passed_count, len(pass_all))
    completed_scores = [score for score in scores if score.completed]
    completion_count = len(completed_scores)
    completion_low, completion_high = wilson_interval(completion_count, len(scores))
    family_rates = [
        mean(float(pass_all[case_id]) for case_id in pass_all if by_case[case_id][0].family == family)
        for family in sorted({score.family for score in scores})
    ]
    rss = [score.peak_rss_bytes for score in scores if score.peak_rss_bytes is not None]
    cost = sum(score.cost_usd or 0.0 for score in scores)
    return ComparisonRow(
        lane=lane,
        slice_type=slice_type,
        slice_value=slice_value,
        system=report.manifest.system,
        adapter_version=report.manifest.adapter_version,
        case_count=len(by_case),
        trial_count=len(scores),
        completion_rate=mean(float(score.completed) for score in scores),
        trial_pass_rate=mean(float(score.passed) for score in scores),
        pass_all_trials_rate=mean(float(value) for value in pass_all.values()),
        pass_all_ci95_low=ci_low,
        pass_all_ci95_high=ci_high,
        pass_any_trial_rate=mean(float(any(score.passed for score in items)) for items in by_case.values()),
        mean_required_check_rate=mean(score.required_check_rate for score in scores),
        macro_family_pass_all_rate=mean(family_rates),
        trial_stability_rate=mean(
            float(len({score.passed for score in items}) == 1) for items in by_case.values()
        ),
        median_elapsed_seconds=median(score.elapsed_seconds for score in scores),
        p95_elapsed_seconds=percentile([score.elapsed_seconds for score in scores], 0.95),
        median_peak_rss_bytes=int(median(rss)) if rss else None,
        accounted_cost_usd=cost,
        cost_per_passing_case_usd=cost / passed_count if passed_count else None,
        latency_comparable=latency_comparable,
        completion_ci95_low=completion_low,
        completion_ci95_high=completion_high,
        quality_eligible_trials=completion_count,
        quality_pass_rate_completed=(
            mean(float(score.passed) for score in completed_scores) if completed_scores else 0
        ),
    )


def _case_rows(
    scores: list[CaseScore],
    report: PortableReport,
    boundary_case_ids: set[str],
) -> list[ComparisonCaseRow]:
    by_case: dict[str, list[CaseScore]] = defaultdict(list)
    for score in scores:
        by_case[score.case_id].append(score)
    output: list[ComparisonCaseRow] = []
    for case_id, items in by_case.items():
        first = items[0]
        statuses = {item.status for item in items}
        status = next(iter(statuses)) if len(statuses) == 1 else "mixed"
        output.append(
            ComparisonCaseRow(
                case_id=case_id,
                lane=first.lane,
                split=first.split,
                family=first.family,
                critical=first.critical,
                comparison_scope="boundary" if case_id in boundary_case_ids else "core",
                system=report.manifest.system,
                status=status,
                trial_count=len(items),
                completed_trials=sum(item.completed for item in items),
                passed_trials=sum(item.passed for item in items),
                pass_all_trials=all(item.passed for item in items),
                mean_required_check_rate=mean(item.required_check_rate for item in items),
                mean_elapsed_seconds=mean(item.elapsed_seconds for item in items),
                accounted_cost_usd=sum(item.cost_usd or 0.0 for item in items),
            )
        )
    return output


def _pairwise_rows(rows: list[ComparisonCaseRow]) -> list[PairwiseComparisonRow]:
    output: list[PairwiseComparisonRow] = []
    lanes = sorted({row.lane for row in rows}, key=lambda lane: lane.value)
    for lane in lanes:
        lane_rows = [row for row in rows if row.lane == lane]
        slices: list[tuple[SliceType, str]] = [("overall", "all")]
        if any(row.comparison_scope == "boundary" for row in lane_rows):
            slices.extend(("scope", value) for value in ("core", "boundary"))
        slices.extend(("split", value) for value in sorted({row.split for row in lane_rows}))
        slices.extend(("family", value) for value in sorted({row.family for row in lane_rows}))
        systems = sorted({row.system for row in lane_rows})
        for slice_type, slice_value in slices:
            selected = [
                row
                for row in lane_rows
                if slice_type == "overall"
                or (slice_type == "scope" and row.comparison_scope == slice_value)
                or (slice_type == "split" and row.split == slice_value)
                or (slice_type == "family" and row.family == slice_value)
            ]
            indexed = {
                system: {row.case_id: row for row in selected if row.system == system} for system in systems
            }
            for system_a, system_b in combinations(systems, 2):
                common = sorted(set(indexed[system_a]) & set(indexed[system_b]))
                if not common:
                    continue
                outcomes = [
                    (
                        indexed[system_a][case_id].pass_all_trials,
                        indexed[system_b][case_id].pass_all_trials,
                    )
                    for case_id in common
                ]
                both = sum(a and b for a, b in outcomes)
                a_only = sum(a and not b for a, b in outcomes)
                b_only = sum(b and not a for a, b in outcomes)
                neither = len(common) - both - a_only - b_only
                p_value = exact_mcnemar(a_only, b_only)
                system_a_rows = [indexed[system_a][case_id] for case_id in common]
                system_b_rows = [indexed[system_b][case_id] for case_id in common]
                completion_a = sum(row.completed_trials for row in system_a_rows) / sum(
                    row.trial_count for row in system_a_rows
                )
                completion_b = sum(row.completed_trials for row in system_b_rows) / sum(
                    row.trial_count for row in system_b_rows
                )
                operationally_comparable = completion_a >= 0.95 and completion_b >= 0.95
                delta_low, delta_high = paired_bootstrap_interval(
                    outcomes,
                    seed=f"{lane.value}:{slice_type}:{slice_value}:{system_a}:{system_b}",
                )
                output.append(
                    PairwiseComparisonRow(
                        lane=lane,
                        slice_type=slice_type,
                        slice_value=slice_value,
                        system_a=system_a,
                        system_b=system_b,
                        common_cases=len(common),
                        both_pass=both,
                        a_only_pass=a_only,
                        b_only_pass=b_only,
                        neither_pass=neither,
                        pass_rate_delta=(a_only - b_only) / len(common),
                        exact_mcnemar_p_value=p_value,
                        holm_adjusted_p_value=p_value,
                        verdict=(
                            "no_significant_difference"
                            if operationally_comparable
                            else "insufficient_operational_conformance"
                        ),
                        operationally_comparable=operationally_comparable,
                        pass_rate_delta_ci95_low=delta_low,
                        pass_rate_delta_ci95_high=delta_high,
                        discordant_cases=a_only + b_only,
                    )
                )
    return output


def _boundary_cases(reports: list[PortableReport]) -> dict[str, list[str]]:
    classifications: dict[str, set[tuple[str, str | None]]] = defaultdict(set)
    for report in reports:
        for observation in report.observations:
            scope = observation.comparison_scope or "core"
            classifications[observation.case_id].add((scope, observation.boundary_reason))
    conflicts = sorted(case_id for case_id, values in classifications.items() if len(values) != 1)
    if conflicts:
        raise ValueError(
            "reports contain conflicting predeclared scope classifications for: " + ", ".join(conflicts)
        )
    return {
        case_id: [reason]
        for case_id, values in sorted(classifications.items())
        for scope, reason in values
        if scope == "boundary" and reason is not None
    }


def _holm_adjust(rows: list[PairwiseComparisonRow]) -> list[PairwiseComparisonRow]:
    if not rows:
        return rows
    adjusted: list[float] = [1.0] * len(rows)
    families: dict[tuple[Lane, str, str], list[tuple[int, PairwiseComparisonRow]]] = defaultdict(list)
    for index, row in enumerate(rows):
        families[(row.lane, row.slice_type, row.slice_value)].append((index, row))
    for family in families.values():
        ordered = sorted(family, key=lambda pair: pair[1].exact_mcnemar_p_value)
        running = 0.0
        count = len(ordered)
        for rank, (original_index, row) in enumerate(ordered):
            running = max(running, min(1.0, (count - rank) * row.exact_mcnemar_p_value))
            adjusted[original_index] = running
    output: list[PairwiseComparisonRow] = []
    for index, row in enumerate(rows):
        verdict: Literal[
            "a_better",
            "b_better",
            "no_significant_difference",
            "insufficient_operational_conformance",
        ]
        verdict = (
            "no_significant_difference"
            if row.operationally_comparable
            else "insufficient_operational_conformance"
        )
        if row.operationally_comparable and adjusted[index] < 0.05 and row.a_only_pass > row.b_only_pass:
            verdict = "a_better"
        elif row.operationally_comparable and adjusted[index] < 0.05 and row.b_only_pass > row.a_only_pass:
            verdict = "b_better"
        output.append(row.model_copy(update={"holm_adjusted_p_value": adjusted[index], "verdict": verdict}))
    return output


def wilson_interval(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z**2 / total
    center = (proportion + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt((proportion * (1 - proportion) + z**2 / (4 * total)) / total) / denominator
    return (max(0.0, center - margin), min(1.0, center + margin))


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def exact_mcnemar(a_only: int, b_only: int) -> float:
    discordant = a_only + b_only
    if discordant == 0:
        return 1.0
    low = min(a_only, b_only)
    tail = sum(math.comb(discordant, index) for index in range(low + 1)) / 2**discordant
    return float(min(1.0, 2 * tail))


def paired_bootstrap_interval(
    outcomes: list[tuple[bool, bool]], *, seed: str, resamples: int = 5000
) -> tuple[float, float]:
    """Deterministic paired bootstrap interval for the pass-rate effect size."""
    if not outcomes:
        return (0.0, 0.0)
    random_seed = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    generator = random.Random(random_seed)
    count = len(outcomes)
    deltas = [
        sum(
            int(outcomes[index][0]) - int(outcomes[index][1])
            for index in (generator.randrange(count) for _ in range(count))
        )
        / count
        for _ in range(resamples)
    ]
    return (percentile(deltas, 0.025), percentile(deltas, 0.975))


def comparison_markdown(report: ComparisonReport) -> str:
    lines = [
        f"# {report.suite_name} comparison",
        "",
        f"Suite: `{report.suite_sha256}`",
        f"Protocol: `{report.protocol_sha256}`",
        f"Scorer: `{report.scorer_version}`",
        "",
        "Every pass requires all lane assertions. No cross-lane composite or winner is computed.",
        "",
        "| Lane | System | Cases | Ops | Quality (completed) | Strict trial pass | "
        "pass@k | pass^k | Trial agreement | Checks | p50/p95 s | Provider spend |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    boundary_lanes = {row.lane for row in report.case_rows if row.comparison_scope == "boundary"}
    for row in report.rows:
        if row.slice_type not in {"overall", "scope"}:
            continue
        if row.slice_type == "overall" and row.lane in boundary_lanes:
            lane_label = f"{row.lane.value} (all)"
        elif row.slice_type == "scope":
            lane_label = f"{row.lane.value} ({row.slice_value})"
        else:
            lane_label = row.lane.value
        latency = f"{row.median_elapsed_seconds:.3f}/{row.p95_elapsed_seconds:.3f}"
        if not row.latency_comparable:
            latency += " (not comparable)"
        repeat = row.trial_count // row.case_count if row.case_count else 0
        quality = f"{row.quality_pass_rate_completed:.1%}" if row.quality_eligible_trials else "N/A"
        lines.append(
            f"| {lane_label} | {row.system} | {row.case_count} | {row.completion_rate:.1%} | "
            f"{quality} | {row.trial_pass_rate:.1%} | "
            f"{row.pass_any_trial_rate:.1%} | {row.pass_all_trials_rate:.1%} | "
            f"{row.trial_stability_rate:.1%} (k={repeat}) | {row.mean_required_check_rate:.1%} | "
            f"{latency} | ${row.accounted_cost_usd:.6f} |"
        )
    lines.extend(
        [
            "",
            "Quality (completed) is conditional on successful acquisition and must not be read as "
            "quality on failed or unsupported inputs. Trial agreement can include consistently "
            "incorrect outcomes and is weak evidence when k is small.",
            "",
            "Provider spend excludes local compute, operator time, and maintenance. Latency marked "
            "not comparable is descriptive only and must not be ranked.",
            "Pairs below 95% operational completion are labeled insufficient operational "
            "conformance; their failures are diagnostics, not successful-output quality evidence.",
            "Core slices exclude managed-access fixtures and any case where at least one compared "
            "system recorded a robots-policy block. Boundary outcomes remain reported separately; "
            "the evaluator never bypasses robots or access controls.",
            "",
            "Paired tests use exact McNemar p-values with Holm correction. A non-significant result "
            "does not establish equivalence.",
            "",
            "Holm correction is scoped to the compared systems within each declared slice; "
            "exploratory family slices do not dilute the overall hypothesis family.",
            "",
            "| Lane | A | B | Cases | Delta (95% paired bootstrap CI) | Discordant | "
            "Exact p | Holm p | Verdict |",
            "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for pair in report.pairwise:
        if pair.slice_type in {"overall", "scope"}:
            lane_label = (
                pair.lane.value if pair.slice_type == "overall" else f"{pair.lane.value} ({pair.slice_value})"
            )
            lines.append(
                f"| {lane_label} | {pair.system_a} | {pair.system_b} | {pair.common_cases} | "
                f"{pair.pass_rate_delta:+.1%} ({pair.pass_rate_delta_ci95_low:+.1%} to "
                f"{pair.pass_rate_delta_ci95_high:+.1%}) | {pair.discordant_cases} | "
                f"{pair.exact_mcnemar_p_value:.4f} | "
                f"{pair.holm_adjusted_p_value:.4f} | {pair.verdict} |"
            )
    if report.boundary_cases:
        lines.extend(["", "## Boundary cases", ""])
        for case_id, reasons in report.boundary_cases.items():
            lines.append(f"- `{case_id}`: {'; '.join(reasons)}")
    return "\n".join(lines) + "\n"
