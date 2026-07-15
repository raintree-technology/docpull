"""Canonical deterministic scorers for every benchmark lane."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from typing import cast
from urllib.parse import urldefrag

from .models import (
    AssertionResult,
    BenchmarkCase,
    CaseScore,
    ChangeExpected,
    ChangePayload,
    ChangeScore,
    CheckPayload,
    ContentPayload,
    CrawlExpected,
    CrawlScore,
    ExtractExpected,
    ExtractScore,
    Lane,
    LifecycleExpected,
    LifecycleScore,
    MetricValue,
    PackExpected,
    PackPayload,
    PackScore,
    ParseExpected,
    ParseScore,
    PolicyExpected,
    PolicyScore,
    ResearchExpected,
    ResearchPayload,
    ResearchScore,
    RetrievalExpected,
    RetrievalPayload,
    RetrievalScore,
    RunObservation,
    SearchExpected,
    SearchPayload,
    SearchScore,
    StructuredExpected,
    StructuredPayload,
    StructuredScore,
    hostname,
)

_SPACE_RE = re.compile(r"\s+")
_LINE_BREAK_HYPHEN_RE = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_ALPHA_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]\n]+\]\([^\s)]+(?:\s+[^)]*)?\)")
_FENCE_LINE_RE = re.compile(r"(?m)^\s*(`{3,}|~{3,})")
_TABLE_ROW_RE = re.compile(r"(?m)^\s*\|[^\n]+\|\s*$")
SCORER_VERSION = "v4-token-boundary-quality-assertions"


def _normalized_text(value: str) -> str:
    repaired = _LINE_BREAK_HYPHEN_RE.sub("", value)
    return _SPACE_RE.sub(" ", repaired).strip().casefold()


def _tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(_normalized_text(value))


def _term_present(combined: str, term: str) -> bool:
    return _term_position(combined, term) >= 0


def _term_position(combined: str, term: str) -> int:
    haystack = _tokens(combined)
    needle = _tokens(term)
    if not needle:
        return -1
    width = len(needle)
    return next(
        (index for index in range(len(haystack) - width + 1) if haystack[index : index + width] == needle),
        -1,
    )


def _ordered_term_positions(combined: str, terms: list[str]) -> list[int]:
    haystack = _tokens(combined)
    positions: list[int] = []
    cursor = 0
    for term in terms:
        needle = _tokens(term)
        if not needle:
            positions.append(-1)
            break
        width = len(needle)
        position = next(
            (
                index
                for index in range(cursor, len(haystack) - width + 1)
                if haystack[index : index + width] == needle
            ),
            -1,
        )
        positions.append(position)
        if position < 0:
            break
        cursor = position + width
    positions.extend([-1] * (len(terms) - len(positions)))
    return positions


def _normalized_url(value: str) -> str:
    return urldefrag(value)[0].rstrip("/")


def _ratio(found: int, total: int) -> float:
    return found / total if total else 1.0


def _assert(
    name: str,
    passed: bool,
    *,
    actual: MetricValue = None,
    expected: MetricValue = None,
    detail: str | None = None,
) -> AssertionResult:
    return AssertionResult(
        name=name,
        passed=bool(passed),
        actual=actual,
        expected=expected,
        detail=detail,
    )


def _records(observation: RunObservation) -> list:
    if isinstance(observation.payload, (ContentPayload, PackPayload)):
        return observation.payload.records
    return []


def _finalize(
    case: BenchmarkCase,
    observation: RunObservation,
    assertions: list[AssertionResult],
    metrics: dict[str, MetricValue],
) -> CaseScore:
    rate = _ratio(sum(item.passed for item in assertions), len(assertions))
    common = {
        "case_id": case.id,
        "system": observation.system,
        "lane": case.input.lane,
        "split": case.metadata.split,
        "family": case.metadata.family,
        "critical": case.metadata.critical,
        "completed": observation.status == "completed",
        "passed": bool(assertions) and all(item.passed for item in assertions),
        "required_check_rate": rate,
        "assertions": assertions,
        "metrics": metrics,
        "elapsed_seconds": observation.elapsed_seconds,
        "peak_rss_bytes": observation.peak_rss_bytes,
        "cost_usd": observation.cost_usd,
        "cost_kind": observation.cost_kind,
        "status": observation.status,
    }
    score_types = {
        Lane.EXTRACT: ExtractScore,
        Lane.CRAWL: CrawlScore,
        Lane.PARSE: ParseScore,
        Lane.PACK: PackScore,
        Lane.STRUCTURED: StructuredScore,
        Lane.LIFECYCLE: LifecycleScore,
        Lane.CHANGE: ChangeScore,
        Lane.RETRIEVAL: RetrievalScore,
        Lane.SEARCH: SearchScore,
        Lane.RESEARCH: ResearchScore,
        Lane.POLICY: PolicyScore,
    }
    return cast(CaseScore, score_types[case.input.lane](**common))


def _content_checks(
    case: BenchmarkCase,
    observation: RunObservation,
    expected: ExtractExpected | CrawlExpected | ParseExpected,
) -> tuple[list[AssertionResult], dict[str, MetricValue]]:
    records = _records(observation)
    raw_combined = "\n".join(record.content for record in records)
    combined = _normalized_text(raw_combined)
    content_chars = sum(len(record.content) for record in records)
    assertions = [
        _assert("status.completed", observation.status == "completed", actual=observation.status),
        _assert(
            "records.minimum",
            len(records) >= expected.minimum_records,
            actual=len(records),
            expected=expected.minimum_records,
        ),
        _assert(
            "content.minimum_chars",
            content_chars >= expected.minimum_content_chars,
            actual=content_chars,
            expected=expected.minimum_content_chars,
        ),
    ]
    if expected.maximum_content_chars is not None:
        assertions.append(
            _assert(
                "content.maximum_chars",
                content_chars <= expected.maximum_content_chars,
                actual=content_chars,
                expected=expected.maximum_content_chars,
            )
        )
    required_terms = [_normalized_text(term) for term in expected.required_terms]
    forbidden_terms = [_normalized_text(term) for term in expected.forbidden_terms]
    term_found = sum(_term_present(combined, term) for term in required_terms)
    forbidden_absent = sum(not _term_present(combined, term) for term in forbidden_terms)
    for term in required_terms:
        assertions.append(_assert(f"term.required:{term}", _term_present(combined, term)))
    for term in forbidden_terms:
        assertions.append(_assert(f"term.forbidden:{term}", not _term_present(combined, term)))
    positions = _ordered_term_positions(combined, expected.required_ordered_terms)
    if expected.required_ordered_terms:
        assertions.append(
            _assert(
                "content.required_order",
                all(position >= 0 for position in positions),
            )
        )
    alpha_tokens = [token for token in _TOKEN_RE.findall(raw_combined) if _ALPHA_TOKEN_RE.fullmatch(token)]
    long_token_rate = (
        sum(len(token) >= 25 for token in alpha_tokens) / len(alpha_tokens) if alpha_tokens else 0.0
    )
    if expected.maximum_long_token_rate is not None:
        assertions.append(
            _assert(
                "content.maximum_long_token_rate",
                long_token_rate <= expected.maximum_long_token_rate,
                actual=long_token_rate,
                expected=expected.maximum_long_token_rate,
            )
        )
    markdown_links = len(_MARKDOWN_LINK_RE.findall(raw_combined))
    fenced_code_blocks = len(_FENCE_LINE_RE.findall(raw_combined)) // 2
    markdown_table_rows = len(_TABLE_ROW_RE.findall(raw_combined))
    for name, actual, minimum in (
        ("markdown.links", markdown_links, expected.minimum_markdown_links),
        ("markdown.fenced_code_blocks", fenced_code_blocks, expected.minimum_fenced_code_blocks),
        ("markdown.table_rows", markdown_table_rows, expected.minimum_markdown_table_rows),
    ):
        if minimum:
            assertions.append(_assert(f"{name}.minimum", actual >= minimum, actual=actual, expected=minimum))
    metrics: dict[str, MetricValue] = {
        "record_count": len(records),
        "content_chars": content_chars,
        "term_coverage": _ratio(term_found, len(required_terms)),
        "forbidden_term_cleanliness": _ratio(forbidden_absent, len(forbidden_terms)),
        "ordered_term_recovery": _ratio(sum(position >= 0 for position in positions), len(positions)),
        "long_token_rate": long_token_rate,
        "markdown_links": markdown_links,
        "fenced_code_blocks": fenced_code_blocks,
        "markdown_table_rows": markdown_table_rows,
    }
    return assertions, metrics


def _score_extract_or_crawl(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, (ExtractExpected, CrawlExpected))
    assertions, metrics = _content_checks(case, observation, expected)
    records = _records(observation)
    urls = {_normalized_url(record.url) for record in records if record.url}
    if isinstance(observation.payload, ContentPayload):
        urls.update(_normalized_url(url) for url in observation.payload.selected_urls)
    required_urls = {_normalized_url(url) for url in expected.required_urls}
    url_recall = _ratio(len(required_urls & urls), len(required_urls))
    for url in required_urls:
        assertions.append(_assert(f"url.required:{url}", url in urls))
    allowed_domains = {domain.casefold().rstrip(".") for domain in expected.allowed_domains}
    allowed_count = sum(
        any(hostname(url) == domain or hostname(url).endswith(f".{domain}") for domain in allowed_domains)
        for url in urls
    )
    domain_precision = _ratio(allowed_count, len(urls)) if allowed_domains else 1.0
    if allowed_domains:
        assertions.append(
            _assert("urls.allowed_domains", domain_precision == 1, actual=domain_precision, expected=1.0)
        )
    combined = _normalized_text("\n".join(record.content for record in records))
    for heading in expected.required_headings:
        normalized = _normalized_text(heading)
        assertions.append(_assert(f"heading.required:{normalized}", _term_present(combined, normalized)))
    metrics.update({"url_recall": url_recall, "domain_precision": domain_precision})
    if isinstance(expected, CrawlExpected):
        hashes = [hashlib.sha256(_normalized_text(record.content).encode()).hexdigest() for record in records]
        duplicate_rate = 1 - _ratio(len(set(hashes)), len(hashes)) if hashes else 0.0
        assertions.append(
            _assert(
                "records.maximum_duplicate_rate",
                duplicate_rate <= expected.maximum_duplicate_rate,
                actual=duplicate_rate,
                expected=expected.maximum_duplicate_rate,
            )
        )
        metrics["duplicate_rate"] = duplicate_rate
    return _finalize(case, observation, assertions, metrics)


def _score_parse(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, ParseExpected)
    if expected.expected_status != "completed":
        return _finalize(
            case,
            observation,
            [
                _assert(
                    "status.expected",
                    observation.status == expected.expected_status,
                    actual=observation.status,
                )
            ],
            {},
        )
    assertions, metrics = _content_checks(case, observation, expected)
    records = _records(observation)
    for key, value in expected.required_metadata.items():
        found = any(str(record.metadata.get(key)) == value for record in records)
        assertions.append(_assert(f"metadata.required:{key}", found, expected=value))
    return _finalize(case, observation, assertions, metrics)


def _score_pack(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, PackExpected)
    payload = observation.payload if isinstance(observation.payload, PackPayload) else None
    records = payload.records if payload else []
    content_chars = sum(len(record.content) for record in records)
    files = set(payload.files if payload else [])
    identities = set(payload.stable_identities if payload else [])
    assertions = [
        _assert("status.completed", observation.status == "completed", actual=observation.status),
        _assert("records.minimum", len(records) >= expected.minimum_records, actual=len(records)),
        _assert(
            "content.minimum_chars", content_chars >= expected.minimum_content_chars, actual=content_chars
        ),
    ]
    assertions.extend(_assert(f"file.required:{name}", name in files) for name in expected.required_files)
    if expected.required_contract_level:
        assertions.append(
            _assert(
                "contract.level",
                bool(payload and payload.contract_level == expected.required_contract_level),
                actual=payload.contract_level if payload else None,
                expected=expected.required_contract_level,
            )
        )
    assertions.append(
        _assert(
            "identities.minimum",
            len(identities) >= expected.minimum_stable_identities,
            actual=len(identities),
            expected=expected.minimum_stable_identities,
        )
    )
    metrics: dict[str, MetricValue] = {
        "record_count": len(records),
        "content_chars": content_chars,
        "file_count": len(files),
        "stable_identity_count": len(identities),
    }
    return _finalize(case, observation, assertions, metrics)


def _score_structured(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, StructuredExpected)
    if expected.expected_status != "completed":
        return _finalize(
            case,
            observation,
            [
                _assert(
                    "status.expected",
                    observation.status == expected.expected_status,
                    actual=observation.status,
                )
            ],
            {},
        )
    payload = observation.payload if isinstance(observation.payload, StructuredPayload) else None
    evidence = set(payload.evidence_ids if payload else [])
    assertions = [
        _assert("status.completed", observation.status == "completed", actual=observation.status),
        _assert("schema.valid", bool(payload and payload.schema_valid)),
        _assert(
            "value.exact",
            bool(payload and payload.value == expected.expected_value),
            actual=(json.dumps(payload.value, sort_keys=True, default=str) if payload else None),
            expected=json.dumps(expected.expected_value, sort_keys=True, default=str),
        ),
    ]
    assertions.extend(
        _assert(f"evidence.required:{identity}", identity in evidence)
        for identity in expected.required_evidence_ids
    )
    metrics: dict[str, MetricValue] = {"evidence_count": len(evidence)}
    return _finalize(case, observation, assertions, metrics)


def _score_lifecycle(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, LifecycleExpected)
    payload = observation.payload if isinstance(observation.payload, CheckPayload) else None
    details = payload.details if payload else {}
    assertions = [_assert("status.completed", observation.status == "completed", actual=observation.status)]
    assertions.extend(
        _assert(f"detail.required:{key}", details.get(key) == value, actual=details.get(key), expected=value)
        for key, value in expected.required_details.items()
    )
    return _finalize(case, observation, assertions, {"detail_count": len(details)})


def _score_change(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, ChangeExpected)
    payload = observation.payload if isinstance(observation.payload, ChangePayload) else None
    actual = {(event.identity, event.kind, event.category) for event in (payload.events if payload else [])}
    gold = {(event.identity, event.kind, event.category) for event in expected.events}
    matched = len(actual & gold)
    precision = _ratio(matched, len(actual))
    recall = _ratio(matched, len(gold))
    false_positives = len(actual - gold)
    assertions = [
        _assert("status.completed", observation.status == "completed", actual=observation.status),
        _assert("events.recall", recall == 1, actual=recall, expected=1.0),
        _assert(
            "events.maximum_false_positives",
            false_positives <= expected.maximum_false_positives,
            actual=false_positives,
            expected=expected.maximum_false_positives,
        ),
    ]
    return _finalize(
        case,
        observation,
        assertions,
        {
            "event_precision": precision,
            "event_recall": recall,
            "false_positives": false_positives,
            "delay_seconds": payload.delay_seconds if payload else None,
        },
    )


def _dcg(relevances: Iterable[int]) -> float:
    return sum(value / math.log2(index + 2) for index, value in enumerate(relevances))


def _rank_metrics(actual_ids: list[str], relevant: set[str]) -> tuple[float, float, float]:
    recall = _ratio(len(set(actual_ids) & relevant), len(relevant))
    reciprocal_rank = next(
        (1 / (index + 1) for index, value in enumerate(actual_ids) if value in relevant), 0.0
    )
    relevance = [int(value in relevant) for value in actual_ids]
    ideal = [1] * min(len(relevant), len(actual_ids)) + [0] * max(0, len(actual_ids) - len(relevant))
    ideal_dcg = _dcg(ideal)
    ndcg = _dcg(relevance) / ideal_dcg if ideal_dcg else 1.0
    return recall, reciprocal_rank, ndcg


def _score_retrieval(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, RetrievalExpected)
    payload = observation.payload if isinstance(observation.payload, RetrievalPayload) else None
    ids = [result.identity for result in (payload.results if payload else [])]
    relevant = set(expected.relevant_ids)
    recall, mrr, ndcg = _rank_metrics(ids, relevant)
    forbidden = set(ids) & set(expected.forbidden_ids)
    empty_ok = not ids if expected.expected_empty else True
    assertions = [
        _assert("status.completed", observation.status == "completed", actual=observation.status),
        _assert(
            "results.expected_empty",
            empty_ok,
            actual=len(ids),
            expected=0 if expected.expected_empty else None,
        ),
        _assert("results.recall", recall == 1, actual=recall, expected=1.0),
        _assert("results.forbidden", not forbidden, actual=len(forbidden), expected=0),
    ]
    return _finalize(
        case,
        observation,
        assertions,
        {
            "recall_at_k": recall,
            "mrr": mrr,
            "ndcg": ndcg,
            "result_count": len(ids),
            "index_bytes": payload.index_bytes if payload else None,
        },
    )


def _score_search(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, SearchExpected)
    payload = observation.payload if isinstance(observation.payload, SearchPayload) else None
    results = payload.results if payload else []
    urls = [_normalized_url(result.url or "") for result in results]
    relevant_urls = {_normalized_url(url) for url in expected.relevant_urls}
    url_recall, mrr, ndcg = _rank_metrics(urls, relevant_urls)
    domains = {hostname(result.url or "") for result in results}
    domain_coverage = _ratio(len(domains & set(expected.relevant_domains)), len(expected.relevant_domains))
    result_text = _normalized_text(
        "\n".join(f"{result.url or ''} {result.title} {result.excerpt}" for result in results)
    )
    identifier_coverage = _ratio(
        sum(_term_present(result_text, value) for value in expected.required_identifiers),
        len(expected.required_identifiers),
    )
    assertions = [
        _assert("status.completed", observation.status == "completed", actual=observation.status),
        _assert("urls.recall", url_recall == 1, actual=url_recall, expected=1.0),
        _assert("domains.coverage", domain_coverage == 1, actual=domain_coverage, expected=1.0),
        _assert("identifiers.coverage", identifier_coverage == 1, actual=identifier_coverage, expected=1.0),
    ]
    return _finalize(
        case,
        observation,
        assertions,
        {
            "url_recall_at_k": url_recall,
            "mrr": mrr,
            "ndcg": ndcg,
            "domain_coverage": domain_coverage,
            "identifier_coverage": identifier_coverage,
        },
    )


def _score_research(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, ResearchExpected)
    payload = observation.payload if isinstance(observation.payload, ResearchPayload) else None
    actual = {claim.claim_id: claim for claim in (payload.claims if payload else [])}
    assertions = [_assert("status.completed", observation.status == "completed", actual=observation.status)]
    value_matches = 0
    evidence_matches = 0
    excerpt_matches = 0
    for claim in expected.claims:
        candidate = actual.get(claim.claim_id)
        value_ok = bool(candidate and candidate.value == claim.value)
        evidence_ok = bool(candidate and set(claim.evidence_ids).issubset(candidate.evidence_ids))
        excerpt_text = _normalized_text("\n".join(candidate.excerpts if candidate else []))
        excerpt_ok = all(_term_present(excerpt_text, term) for term in claim.required_excerpt_terms)
        value_matches += value_ok
        evidence_matches += evidence_ok
        excerpt_matches += excerpt_ok
        assertions.extend(
            [
                _assert(f"claim.value:{claim.claim_id}", value_ok),
                _assert(f"claim.evidence:{claim.claim_id}", evidence_ok),
                _assert(f"claim.excerpts:{claim.claim_id}", excerpt_ok),
            ]
        )
    total = len(expected.claims)
    return _finalize(
        case,
        observation,
        assertions,
        {
            "claim_accuracy": _ratio(value_matches, total),
            "citation_completeness": _ratio(evidence_matches, total),
            "excerpt_coverage": _ratio(excerpt_matches, total),
        },
    )


def _payload_text(observation: RunObservation) -> str:
    if isinstance(observation.payload, (ContentPayload, PackPayload)):
        return "\n".join(record.content for record in observation.payload.records)
    if isinstance(observation.payload, CheckPayload):
        return str(observation.payload.details)
    return str(observation.payload.model_dump() if observation.payload else "")


def _score_policy(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    expected = case.expected
    assert isinstance(expected, PolicyExpected)
    error_text = _normalized_text(observation.error or "")
    output_text = _normalized_text(_payload_text(observation))
    assertions = [
        _assert(
            "status.expected",
            observation.status == expected.expected_status,
            actual=observation.status,
            expected=expected.expected_status,
        ),
    ]
    assertions.extend(
        _assert(f"error.required:{term}", _term_present(error_text, term))
        for term in expected.required_error_terms
    )
    assertions.extend(
        _assert(f"output.forbidden:{term}", _normalized_text(term) not in output_text)
        for term in expected.forbidden_output_terms
    )
    if expected.maximum_request_count is not None:
        count = observation.request_count or 0
        assertions.append(
            _assert(
                "requests.maximum",
                count <= expected.maximum_request_count,
                actual=count,
                expected=expected.maximum_request_count,
            )
        )
    return _finalize(case, observation, assertions, {"request_count": observation.request_count or 0})


def score_observation(case: BenchmarkCase, observation: RunObservation) -> CaseScore:
    """Score observable output once, without inspecting proprietary execution traces."""
    if case.input.lane in {Lane.EXTRACT, Lane.CRAWL}:
        return _score_extract_or_crawl(case, observation)
    if case.input.lane is Lane.PARSE:
        return _score_parse(case, observation)
    if case.input.lane is Lane.PACK:
        return _score_pack(case, observation)
    if case.input.lane is Lane.STRUCTURED:
        return _score_structured(case, observation)
    if case.input.lane is Lane.LIFECYCLE:
        return _score_lifecycle(case, observation)
    if case.input.lane is Lane.CHANGE:
        return _score_change(case, observation)
    if case.input.lane is Lane.RETRIEVAL:
        return _score_retrieval(case, observation)
    if case.input.lane is Lane.SEARCH:
        return _score_search(case, observation)
    if case.input.lane is Lane.RESEARCH:
        return _score_research(case, observation)
    if case.input.lane is Lane.POLICY:
        return _score_policy(case, observation)
    raise AssertionError(f"unhandled lane: {case.input.lane}")
