"""Benchmark harness tests."""

from __future__ import annotations

import asyncio
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import docpull.benchmark as benchmark
from docpull.benchmark import BenchmarkError, run_benchmark_cli, run_quick_benchmark
from docpull.models.events import EventType, SkipReason
from tests.pack_fixtures import write_context_pack

pytestmark = pytest.mark.internal_legacy


def main(argv: list[str]) -> int:
    command, *rest = argv
    if command == "benchmark":
        return run_benchmark_cli(rest)
    raise AssertionError(f"Unexpected benchmark CLI command: {command}")


async def _fake_core_case(**kwargs: Any) -> dict[str, Any]:
    output_dir = kwargs["output_dir"]
    assert isinstance(output_dir, Path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return {
        "name": kwargs["name"],
        "workflow": "core-llm",
        "output_dir": str(output_dir),
        "wall_seconds": 0.01,
        "rss_baseline_mb": 10.0,
        "rss_peak_mb": 10.0,
        "rss_delta_mb": 0.0,
        "stats": {
            "urls_discovered": 1,
            "pages_fetched": 1,
            "pages_skipped": 0,
            "pages_failed": 0,
            "duration_seconds": 0.01,
            "success_rate": 100.0,
        },
        "skip_counts": {},
        "artifact_size_bytes": 123,
        "cache_size_bytes": 456,
        "pack_score": {
            "score": 92,
            "grade": "excellent",
            "summary": {"record_count": 1},
            "issues": [],
            "warnings": [],
        },
        "source_score_count": 1,
    }


def test_benchmark_quick_cli_writes_reports(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)
    output_dir = tmp_path / "bench"

    assert (
        main(
            [
                "benchmark",
                "quick",
                "--target-url",
                "https://docs.parallel.ai",
                "--output-dir",
                str(output_dir),
                "--max-pages",
                "1",
                "--no-cached-pass",
            ]
        )
        == 0
    )

    report = json.loads((output_dir / "benchmark.report.json").read_text(encoding="utf-8"))
    assert report["summary"]["case_count"] == 1
    assert report["summary"]["target_count"] == 1
    assert report["summary"]["best_pack_score"] == 92
    assert report["trace"]["provider"] == "none"
    assert report["targets"][0]["id"] == "docs-parallel-ai"
    assert report["cases"][0]["target_id"] == "docs-parallel-ai"
    assert report["artifacts"]["config"].endswith("benchmark.config.json")
    assert (output_dir / "benchmark.config.json").exists()
    assert (output_dir / "benchmark.summary.md").exists()


def test_benchmark_quick_cli_runs_selected_provider_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")

    def fake_tavily_case(**kwargs: Any) -> dict[str, Any]:
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "name": "tavily-search-extract",
            "workflow": "tavily-search-extract-pack",
            "output_dir": str(output_dir),
            "wall_seconds": 0.02,
            "pack_score": {"score": 90, "summary": {"record_count": 1}},
        }

    def fake_exa_case(**kwargs: Any) -> dict[str, Any]:
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "name": "exa-search-contents",
            "workflow": "exa-search-contents-pack",
            "output_dir": str(output_dir),
            "wall_seconds": 0.03,
            "estimated_cost_usd": 0.002,
            "pack_score": {"score": 95, "summary": {"record_count": 1}},
        }

    monkeypatch.setattr(benchmark, "_run_tavily_case", fake_tavily_case)
    monkeypatch.setattr(benchmark, "_run_exa_case", fake_exa_case)
    output_dir = tmp_path / "bench"

    assert (
        main(
            [
                "benchmark",
                "quick",
                "--target-url",
                "https://docs.parallel.ai",
                "--output-dir",
                str(output_dir),
                "--max-pages",
                "1",
                "--no-cached-pass",
                "--tavily",
                "--provider",
                "exa",
            ]
        )
        == 0
    )

    report = json.loads((output_dir / "benchmark.report.json").read_text(encoding="utf-8"))
    assert report["providers"] == ["core", "tavily", "exa"]
    assert report["requested_providers"] == ["tavily", "exa"]
    assert report["skipped_providers"] == []
    assert [case["name"] for case in report["cases"]] == [
        "core-llm",
        "tavily-search-extract",
        "exa-search-contents",
    ]
    assert report["summary"]["total_estimated_live_cost_usd"] == 0.002


def test_benchmark_zero_dollar_blocks_provider_cases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")

    def fail_tavily_case(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("provider benchmark should not run")

    monkeypatch.setattr(benchmark, "_run_tavily_case", fail_tavily_case)

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        target_set="single",
        output_dir=tmp_path / "bench",
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=False,
        parallel=False,
        parallel_objective=None,
        parallel_queries=[],
        include_domains=[],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
        tavily=True,
        budget_limit=0,
    )

    assert report["zero_dollar"] is True
    assert report["providers"] == ["core"]
    assert report["skipped_providers"] == [{"provider": "tavily", "reason": "blocked_by_budget"}]
    assert report["zero_dollar_completion"]["counts"] == {"complete_for_0": 1}
    assert (tmp_path / "bench" / "run.accounting.json").exists()


def test_benchmark_phase2_zero_dollar_target_set_classifies_routes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        target_set="phase2",
        output_dir=tmp_path / "bench",
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=False,
        parallel=False,
        parallel_objective=None,
        parallel_queries=[],
        include_domains=[],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
        zero_dollar=True,
    )

    assert report["target_set"] == "zero-dollar"
    assert report["summary"]["target_count"] == 12
    target_kinds = {target["kind"] for target in report["targets"]}
    assert {"filing", "feed", "sitemap", "search_to_evidence"} <= target_kinds
    completion = report["zero_dollar_completion"]
    assert completion["target_count"] == 12
    assert completion["counts"] == {
        "complete_for_0": 4,
        "complete_with_local_browser": 1,
        "partial_for_0": 6,
        "requires_provider": 1,
    }
    assert completion["next_actions"]["try_local_browser"] == ["nextjs_docs_spa"]
    assert completion["next_actions"]["try_provider_discovery"] == ["packaging_search_to_evidence"]
    suggestions = {item["target_id"]: item for item in completion["escalation_suggestions"]}
    assert suggestions["nextjs_docs_spa"]["action"] == "try_local_browser"
    assert suggestions["nextjs_docs_spa"]["estimated_paid_cost_usd"] == 0.0
    assert "--render fallback" in suggestions["nextjs_docs_spa"]["commands"][0]
    assert suggestions["packaging_search_to_evidence"]["action"] == "try_provider_discovery"
    assert suggestions["packaging_search_to_evidence"]["estimated_paid_request_count"] == 3
    assert suggestions["packaging_search_to_evidence"]["estimated_paid_cost_usd"] > 0
    assert "--dry-run" in suggestions["packaging_search_to_evidence"]["commands"][0]
    markdown = (tmp_path / "bench" / "benchmark.summary.md").read_text(encoding="utf-8")
    assert "Zero-Dollar Completion" in markdown
    assert "Escalation suggestions" in markdown


def test_zero_dollar_completion_classifies_policy_and_cloud_browser() -> None:
    cases = [
        {
            "name": "core",
            "provider": "docpull",
            "target_id": "policy_target",
            "status": "failed",
            "error": {"message": "blocked by policy"},
            "target": {
                "id": "policy_target",
                "label": "Policy target",
                "url": "https://example.com/private",
                "kind": "policy",
                "min_expected_records": 1,
                "zero_dollar_route": "direct_http",
            },
        },
        {
            "name": "core",
            "provider": "docpull",
            "target_id": "cloud_target",
            "pack_score": {"summary": {"record_count": 0}, "score": 0},
            "target": {
                "id": "cloud_target",
                "label": "Cloud target",
                "url": "https://example.com/app",
                "kind": "cloud_browser",
                "min_expected_records": 1,
                "zero_dollar_route": "cloud_browser",
            },
        },
    ]

    completion = benchmark._zero_dollar_completion(cases)

    by_id = {target["target_id"]: target for target in completion["targets"]}
    assert by_id["policy_target"]["completion_class"] == "blocked_by_policy"
    assert by_id["cloud_target"]["completion_class"] == "requires_cloud_browser"
    assert completion["next_actions"]["review_policy"] == ["policy_target"]
    assert completion["next_actions"]["consider_cloud_browser"] == ["cloud_target"]
    suggestions = {item["target_id"]: item for item in completion["escalation_suggestions"]}
    assert suggestions["policy_target"]["action"] == "review_policy"
    assert suggestions["policy_target"]["estimated_paid_request_count"] == 0
    assert suggestions["cloud_target"]["action"] == "consider_cloud_browser"
    assert suggestions["cloud_target"]["estimated_paid_request_count"] == 1
    assert suggestions["cloud_target"]["estimated_paid_cost_usd"] > 0
    assert "--runtime vercel" in suggestions["cloud_target"]["commands"][1]


def test_benchmark_matrix_runs_core_once_per_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)
    output_dir = tmp_path / "bench"

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        target_set="tool-docs",
        output_dir=output_dir,
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=None,
        parallel=False,
        parallel_objective=None,
        parallel_queries=[],
        include_domains=[],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
    )

    assert report["target_set"] == "tool-docs"
    assert report["summary"]["target_count"] == 5
    assert report["summary"]["case_count"] == 5
    assert report["summary"]["cache_only_case_count"] == 0
    assert [case["target_id"] for case in report["cases"]] == [
        "parallel_docs",
        "exa_docs",
        "tavily_docs",
        "raindrop_docs",
        "docpull_docs",
    ]
    assert report["cases"][0]["name"] == "parallel_docs/core-llm"
    assert "Provider x Target Heatmap" in (output_dir / "benchmark.summary.md").read_text(encoding="utf-8")


def test_provider_matrix_target_set_has_legacy_v2_alias(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        target_set="v2",
        output_dir=tmp_path / "bench",
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=None,
        parallel=False,
        parallel_objective=None,
        parallel_queries=[],
        include_domains=[],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
    )

    assert report["target_set"] == "provider-matrix"
    assert report["summary"]["target_count"] == 8
    assert report["summary"]["case_count"] == 8


def test_benchmark_records_provider_failure_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")

    def fake_tavily_case(**_kwargs: Any) -> dict[str, Any]:
        raise BenchmarkError("Tavily Search returned no extractable URLs.")

    monkeypatch.setattr(benchmark, "_run_tavily_case", fake_tavily_case)

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        output_dir=tmp_path / "bench",
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=False,
        parallel=False,
        parallel_objective="Build a pack",
        parallel_queries=["Parallel API docs"],
        include_domains=["docs.parallel.ai"],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
        live_providers=["tavily"],
    )

    assert report["summary"]["case_count"] == 2
    assert report["summary"]["failed_case_count"] == 1
    failed = report["cases"][1]
    assert failed["name"] == "tavily-search-extract"
    assert failed["status"] == "failed"
    assert failed["error"]["type"] == "BenchmarkError"
    assert failed["benchmark_score"] is None


def test_parallel_cost_guard_runs_before_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_core_case(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(benchmark, "_run_core_case", fake_core_case)
    monkeypatch.setenv("PARALLEL_API_KEY", "test-parallel-key")
    monkeypatch.setattr(benchmark, "_parallel_sdk_installed", lambda: True)

    with pytest.raises(BenchmarkError):
        run_quick_benchmark(
            target_url="https://docs.parallel.ai",
            output_dir=tmp_path / "bench",
            max_pages=1,
            max_depth=1,
            max_concurrent=1,
            per_host_concurrent=1,
            cache_enabled=True,
            cached_pass=False,
            parallel=True,
            parallel_objective="Build a pack",
            parallel_queries=["Parallel API docs"],
            include_domains=["docs.parallel.ai"],
            mode="advanced",
            max_search_results=8,
            extract_limit=3,
            max_estimated_cost=0.001,
        )

    assert called is False


def test_benchmark_all_providers_skips_missing_keys_and_runs_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_run_core_case", _fake_core_case)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    monkeypatch.setenv("EXA_API_KEY", "test-exa-key")

    def fake_exa_case(**kwargs: Any) -> dict[str, Any]:
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "name": "exa-search-contents",
            "workflow": "exa-search-contents-pack",
            "output_dir": str(output_dir),
            "wall_seconds": 0.03,
            "estimated_cost_usd": 0.002,
            "pack_score": {"score": 95, "summary": {"record_count": 1}},
        }

    monkeypatch.setattr(benchmark, "_run_exa_case", fake_exa_case)

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        output_dir=tmp_path / "bench",
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=False,
        parallel=False,
        parallel_objective="Build a pack",
        parallel_queries=["Parallel API docs"],
        include_domains=["docs.parallel.ai"],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
        live_providers=["all"],
    )

    assert report["requested_providers"] == ["parallel", "tavily", "exa"]
    assert report["providers"] == ["core", "exa"]
    assert [case["name"] for case in report["cases"]] == ["core-llm", "exa-search-contents"]
    assert {item["provider"] for item in report["skipped_providers"]} == {"parallel", "tavily"}


def test_core_and_parallel_case_runners_attach_pack_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStats:
        pages_fetched = 1

        def to_dict(self) -> dict[str, object]:
            return {
                "urls_discovered": 2,
                "pages_fetched": 1,
                "pages_skipped": 1,
                "pages_failed": 0,
                "duration_seconds": 0.1,
                "success_rate": 100.0,
            }

    class FakeFetcher:
        def __init__(self, _config: object) -> None:
            self.stats = FakeStats()

        async def __aenter__(self) -> FakeFetcher:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def run(self):
            yield SimpleNamespace(type=EventType.FETCH_SKIPPED, skip_reason=SkipReason.HTTP_ERROR)

    def fake_search_pack(**kwargs: object) -> None:
        write_context_pack(Path(str(kwargs["output_dir"])), provider="search")

    def fake_context_pack(**kwargs: object) -> None:
        write_context_pack(Path(str(kwargs["output_dir"])), provider="parallel")

    monkeypatch.setattr(benchmark, "Fetcher", FakeFetcher)
    monkeypatch.setattr(benchmark, "run_search_pack", fake_search_pack)
    monkeypatch.setattr(benchmark, "run_live_context_pack", fake_context_pack)

    target = benchmark._BenchmarkTarget(
        id="parallel_docs",
        label="Parallel docs",
        url="https://docs.parallel.ai",
        include_domains=("docs.parallel.ai",),
        objective="Build a pack",
        queries=("Parallel API docs",),
    )
    core = asyncio.run(
        benchmark._run_core_case(
            name="core-llm",
            target_url="https://docs.parallel.ai",
            output_dir=tmp_path / "core",
            cache_dir=tmp_path / "cache",
            cache_enabled=True,
            max_pages=1,
            max_depth=1,
            max_concurrent=1,
            per_host_concurrent=1,
            include_domains=["docs.parallel.ai"],
            target=target,
        )
    )
    assert core["skip_counts"] == {"http_error": 1}
    assert core["pack_score"] is None

    search = benchmark._run_parallel_search_case(
        objective="Build a pack",
        queries=["Parallel API docs"],
        output_dir=tmp_path / "search",
        include_domains=["docs.parallel.ai"],
        source_policy={"include_domains": ["docs.parallel.ai"]},
        mode="advanced",
        max_search_results=2,
        estimated_cost=0.001,
        target=target,
    )
    context = benchmark._run_parallel_context_case(
        objective="Build a pack",
        queries=["Parallel API docs"],
        output_dir=tmp_path / "context",
        include_domains=["docs.parallel.ai"],
        source_policy={"include_domains": ["docs.parallel.ai"]},
        mode="advanced",
        max_search_results=2,
        extract_limit=1,
        estimated_cost=0.002,
        target=target,
    )

    assert search["pack_metadata"]["workflow"] == "context-pack"
    assert search["pack_intelligence"]["summary"]["search_query_count"] == 1
    assert search["estimated_cost_usd"] == 0.001
    assert context["pack_metadata"]["provider"] == "parallel"
    assert context["benchmark_score"]["score"] > 0


def test_tavily_case_writes_scored_provider_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_lookup_benchmark_secret", lambda _env_var: "test-key")

    def fake_http_json_post(**kwargs: Any) -> dict[str, Any]:
        if kwargs["label"] == "Tavily Search":
            return {
                "results": [
                    {
                        "url": "https://docs.parallel.ai/getting-started/overview",
                        "title": "Parallel overview",
                        "content": "Overview snippet",
                        "score": 0.9,
                    }
                ],
                "usage": {"credits": 1},
                "request_id": "search_1",
                "response_time": 0.1,
            }
        return {
            "results": [
                {
                    "url": "https://docs.parallel.ai/getting-started/overview",
                    "raw_content": "# Parallel overview\n\nParallel API docs.",
                    "favicon": "https://docs.parallel.ai/favicon.ico",
                }
            ],
            "failed_results": [],
            "usage": {"credits": 1},
            "request_id": "extract_1",
            "response_time": 0.2,
        }

    monkeypatch.setattr(benchmark, "_http_json_post", fake_http_json_post)

    payload = benchmark._run_tavily_case(
        objective="Build a pack",
        queries=["Parallel API docs"],
        output_dir=tmp_path / "tavily",
        include_domains=["docs.parallel.ai"],
        max_search_results=5,
        extract_limit=1,
        tavily_credit_usd=0.002,
    )

    assert payload["name"] == "tavily-search-extract"
    assert payload["estimated_cost_usd"] == 0.004
    assert payload["cost_units"]["total_credits"] == 2
    assert payload["pack_score"]["score"] == 100
    assert payload["benchmark_score"]["dimensions"]["coverage"]["score"] == 100
    assert payload["pack_metadata"]["provider"] == "tavily"
    assert payload["pack_intelligence"]["summary"]["search_query_count"] == 1
    assert payload["pack_intelligence"]["artifacts"]["prepare"] == "pack.prepare.json"
    assert (tmp_path / "tavily" / "documents.ndjson").exists()
    assert (tmp_path / "tavily" / "tavily.pack.json").exists()
    assert (tmp_path / "tavily" / "pack.prepare.json").exists()
    assert (tmp_path / "tavily" / "RESEARCH_BRIEF.md").exists()


def test_exa_case_records_cost_and_pack_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_lookup_benchmark_secret", lambda _env_var: "test-key")

    def fake_http_json_post(**_kwargs: Any) -> dict[str, Any]:
        return {
            "requestId": "exa_1",
            "resolvedSearchType": "auto",
            "costDollars": {"total": 0.007, "search": {"neural": 0.007}},
            "results": [
                {
                    "id": "doc_1",
                    "url": "https://docs.parallel.ai/task-api/task-quickstart",
                    "title": "Task quickstart",
                    "text": "# Task quickstart\n\nUse the Task API.",
                    "highlights": ["Task API"],
                }
            ],
        }

    monkeypatch.setattr(benchmark, "_http_json_post", fake_http_json_post)

    payload = benchmark._run_exa_case(
        objective="Build a pack",
        queries=["Parallel API docs"],
        output_dir=tmp_path / "exa",
        include_domains=["docs.parallel.ai"],
        max_search_results=5,
    )

    assert payload["name"] == "exa-search-contents"
    assert payload["estimated_cost_usd"] == 0.007
    assert payload["pack_score"]["score"] == 100
    assert payload["pack_metadata"]["provider"] == "exa"
    assert payload["pack_intelligence"]["summary"]["search_query_count"] == 1
    assert (tmp_path / "exa" / "pack.prepare.json").exists()
    assert (tmp_path / "exa" / "SEARCH.md").exists()


def test_pack_intelligence_failure_preserves_provider_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "provider"
    write_context_pack(output_dir, provider="tavily")
    payload: dict[str, Any] = {"artifact_size_bytes": 0}

    def fail_prepare(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("prepare exploded")

    monkeypatch.setattr(benchmark, "prepare_pack", fail_prepare)

    benchmark._attach_pack_intelligence(
        payload,
        output_dir,
        ["docs.parallel.ai"],
        objective="Build a pack",
        queries=["Parallel API docs"],
    )

    assert payload["pack_intelligence"] is None
    assert payload["pack_intelligence_error"]["type"] == "RuntimeError"
    assert payload["pack_score"]["score"] == 100
    assert payload["source_score_count"] == 1


def test_raindrop_trace_requires_write_key_before_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_core_case(**_kwargs: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.delenv("RAINDROP_WRITE_KEY", raising=False)
    monkeypatch.setattr(benchmark, "_run_core_case", fake_core_case)
    monkeypatch.setattr(benchmark, "_lookup_benchmark_secret", lambda _env_var: None)

    with pytest.raises(BenchmarkError, match="RAINDROP_WRITE_KEY"):
        run_quick_benchmark(
            target_url="https://docs.parallel.ai",
            output_dir=tmp_path / "bench",
            max_pages=1,
            max_depth=1,
            max_concurrent=1,
            per_host_concurrent=1,
            cache_enabled=True,
            cached_pass=False,
            parallel=False,
            parallel_objective="Build a pack",
            parallel_queries=["Parallel API docs"],
            include_domains=["docs.parallel.ai"],
            mode="advanced",
            max_search_results=8,
            extract_limit=3,
            max_estimated_cost=0.05,
            trace_backend="raindrop",
        )

    assert called is False


def test_raindrop_trace_records_event_id_and_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    class FakeInteraction:
        def __init__(self, event_id: str) -> None:
            self._event_id = event_id

        def track_tool(self, **kwargs: Any) -> None:
            events.append({"type": "tool", **kwargs})

        def set_properties(self, props: dict[str, Any]) -> None:
            events.append({"type": "properties", "props": props})

        def finish(self, **kwargs: Any) -> None:
            events.append({"type": "finish", **kwargs})

    fake_raindrop = types.ModuleType("raindrop.analytics")

    def fake_init(*_args: Any, **_kwargs: Any) -> None:
        events.append({"type": "init"})

    def fake_begin(**kwargs: Any) -> FakeInteraction:
        events.append({"type": "begin", **kwargs})
        return FakeInteraction(str(kwargs["event_id"]))

    def fake_track_signal(**kwargs: Any) -> None:
        signals.append(kwargs)

    fake_raindrop.init = fake_init  # type: ignore[attr-defined]
    fake_raindrop.begin = fake_begin  # type: ignore[attr-defined]
    fake_raindrop.track_signal = fake_track_signal  # type: ignore[attr-defined]
    fake_raindrop.flush = lambda: events.append({"type": "flush"})  # type: ignore[attr-defined]
    fake_raindrop.shutdown = lambda: events.append({"type": "shutdown"})  # type: ignore[attr-defined]
    fake_package = types.ModuleType("raindrop")
    fake_package.analytics = fake_raindrop  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "raindrop", fake_package)
    monkeypatch.setitem(sys.modules, "raindrop.analytics", fake_raindrop)
    monkeypatch.setattr(benchmark, "_lookup_benchmark_secret", lambda _env_var: "test-key")

    recorder = benchmark._RaindropTraceRecorder(
        target_url="https://docs.parallel.ai",
        targets=[
            benchmark._BenchmarkTarget(
                id="parallel_docs",
                label="Parallel docs",
                url="https://docs.parallel.ai",
                include_domains=("docs.parallel.ai",),
                objective="Build a pack",
                queries=("Parallel API docs",),
            )
        ],
        target_set="tool-docs",
        output_dir=tmp_path,
        parallel_enabled=True,
        max_estimated_cost=0.05,
    )
    recorder.record_case(
        {
            "name": "parallel_docs/tavily-search-extract",
            "provider": "tavily",
            "workflow": "tavily-search-extract-pack",
            "target_id": "parallel_docs",
            "target_url": "https://docs.parallel.ai",
            "target_kind": "docs",
            "status": "failed",
            "error": {"type": "BenchmarkError", "message": "No extractable URLs"},
            "wall_seconds": 0.2,
            "estimated_cost_usd": 0.0,
        }
    )
    recorder.record_case(
        {
            "name": "parallel_docs/exa-search-contents",
            "provider": "exa",
            "workflow": "exa-search-contents-pack",
            "target_id": "parallel_docs",
            "target_url": "https://docs.parallel.ai",
            "target_kind": "docs",
            "status": "ok",
            "wall_seconds": 11.2,
            "estimated_cost_usd": 0.012,
            "pack_score": {"score": 88, "summary": {"record_count": 2, "total_tokens": 1200}},
            "benchmark_score": {
                "score": 86,
                "dimensions": {
                    "coverage": {"score": 70, "signals": ["2/3 expected unique URLs"]},
                },
            },
            "pack_metadata": {
                "selected_urls": ["https://docs.parallel.ai/a"],
                "extract_error_count": 1,
            },
        }
    )
    recorder.finish({"summary": {"case_count": 2}, "artifacts": {"json": "benchmark.report.json"}})

    metadata = recorder.metadata()
    assert metadata["event_id"]
    assert metadata["case_count"] == 2
    assert metadata["signal_count"] == 5
    assert metadata["negative_signal_count"] == 5
    assert {signal["name"] for signal in signals} == {
        "benchmark_case_failed",
        "benchmark_low_score",
        "benchmark_slow_case",
        "benchmark_high_cost_case",
        "benchmark_dimension_signal",
    }
    assert all(signal["event_id"] == metadata["event_id"] for signal in signals)
    assert signals[0]["properties"]["content_policy"] == "metadata_only"
    assert any(event["type"] == "properties" and event["props"]["signal_count"] == 5 for event in events)


def test_benchmark_article_cli_writes_publishable_markdown(tmp_path: Path) -> None:
    report = {
        "schema_version": 1,
        "generated_at": "2026-06-08T00:00:00+00:00",
        "run_dir": str(tmp_path / "bench"),
        "target_url": "https://docs.parallel.ai",
        "parallel_enabled": True,
        "trace": {"provider": "raindrop", "enabled": True, "status": "recording"},
        "summary": {"total_estimated_parallel_cost_usd": 0.013},
        "artifacts": {
            "json": str(tmp_path / "benchmark.report.json"),
            "markdown": str(tmp_path / "benchmark.summary.md"),
        },
        "cases": [
            {
                "name": "core-llm",
                "workflow": "core-llm",
                "wall_seconds": 1.2,
                "pack_score": {"score": 100, "summary": {"record_count": 2}},
            },
            {
                "name": "parallel-context",
                "workflow": "parallel-context-pack",
                "wall_seconds": 0.9,
                "estimated_cost_usd": 0.008,
                "pack_score": {"score": 100, "summary": {"record_count": 3}},
            },
        ],
    }
    report_path = tmp_path / "benchmark.report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    assert main(["benchmark", "article", str(report_path)]) == 0

    article = (tmp_path / "benchmark.article.md").read_text(encoding="utf-8")
    assert "Benchmarking docpull, Parallel, Tavily, Exa, and Raindrop" in article
    assert "Raindrop tracing was enabled" in article
    assert "`parallel-context`" in article


def test_http_json_post_retries_transient_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Transient HTTPError on first attempt should retry and succeed on second."""
    attempts = {"count": 0}
    sleep_calls: list[float] = []

    def fake_once(**_kwargs: Any) -> dict[str, Any]:
        attempts["count"] += 1
        if attempts["count"] == 1:
            err = benchmark._TransientHTTPError("simulated 503", retry_after=None)
            raise err
        return {"ok": True, "attempt": attempts["count"]}

    monkeypatch.setattr(benchmark, "_http_json_post_once", fake_once)

    result = benchmark._http_json_post(
        label="Test",
        url="https://example.com/x",
        headers={},
        body={},
        timeout=10,
        max_attempts=3,
        sleep=sleep_calls.append,
    )

    assert result == {"ok": True, "attempt": 2}
    assert attempts["count"] == 2
    assert len(sleep_calls) == 1
    assert sleep_calls[0] > 0


def test_http_json_post_raises_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All attempts transient → BenchmarkError with the last error's message."""
    sleep_calls: list[float] = []

    def fake_once(**_kwargs: Any) -> dict[str, Any]:
        raise benchmark._TransientHTTPError("simulated 429", retry_after=0.0)

    monkeypatch.setattr(benchmark, "_http_json_post_once", fake_once)

    with pytest.raises(BenchmarkError, match="429"):
        benchmark._http_json_post(
            label="Test",
            url="https://example.com/x",
            headers={},
            body={},
            timeout=10,
            max_attempts=2,
            sleep=sleep_calls.append,
        )

    # 2 attempts total → 1 sleep between them.
    assert len(sleep_calls) == 1


def test_benchmark_score_floors_to_zero_on_empty_pack() -> None:
    """Empty pack should not arithmetic-average to 50 via vacuous high dims."""
    payload = {"pack_score": {"score": 65, "summary": {"record_count": 0}}}
    score = benchmark._benchmark_score(
        payload=payload,
        records=[],
        include_domains=["docs.parallel.ai"],
        target=None,
    )

    assert score["score"] == 0
    assert score["grade"] == "poor"
    assert set(score["dimensions"]) == {
        "coverage",
        "cleanliness",
        "source_fidelity",
        "freshness",
        "density",
    }
    for dim in score["dimensions"].values():
        assert dim["score"] == 0
        assert "empty pack" in dim["signals"]


def test_runs_n_aggregates_with_median_and_spread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--runs 3 should produce one aggregate case with median + min/max + raw runs."""
    call_count = {"i": 0}
    score_sequence = [88, 92, 95]
    wall_sequence = [1.0, 2.0, 3.0]

    async def fake(**kwargs: Any) -> dict[str, Any]:
        i = call_count["i"]
        call_count["i"] += 1
        output_dir = kwargs["output_dir"]
        output_dir.mkdir(parents=True, exist_ok=True)
        return {
            "name": kwargs["name"],
            "workflow": "core-llm",
            "output_dir": str(output_dir),
            "wall_seconds": wall_sequence[i],
            "rss_baseline_mb": 10.0,
            "rss_peak_mb": 11.0,
            "rss_delta_mb": 1.0,
            "stats": {
                "urls_discovered": 1,
                "pages_fetched": 1,
                "pages_skipped": 0,
                "pages_failed": 0,
                "duration_seconds": 0.01,
                "success_rate": 100.0,
            },
            "skip_counts": {},
            "artifact_size_bytes": 100,
            "cache_size_bytes": 200,
            "pack_score": {
                "score": score_sequence[i],
                "grade": "excellent",
                "summary": {"record_count": 1, "total_tokens": 100},
                "issues": [],
                "warnings": [],
            },
            "benchmark_score": {
                "schema_version": 2,
                "score": score_sequence[i],
                "grade": "excellent",
                "weights": {},
                "dimensions": {},
            },
            "source_score_count": 1,
        }

    monkeypatch.setattr(benchmark, "_run_core_case", fake)

    report = run_quick_benchmark(
        target_url="https://docs.parallel.ai",
        output_dir=tmp_path / "bench",
        max_pages=1,
        max_depth=1,
        max_concurrent=1,
        per_host_concurrent=1,
        cache_enabled=True,
        cached_pass=True,  # should be forced off by runs > 1
        parallel=False,
        parallel_objective=None,
        parallel_queries=[],
        include_domains=[],
        mode="advanced",
        max_search_results=8,
        extract_limit=3,
        max_estimated_cost=0.05,
        runs=3,
    )

    assert report["runs_per_case"] == 3
    # cached pass should have been forced off
    assert report["summary"]["case_count"] == 1
    assert call_count["i"] == 3

    case = report["cases"][0]
    assert case["runs_total"] == 3
    assert case["runs_succeeded"] == 3
    assert case["wall_seconds"] == 2.0
    assert case["wall_seconds_min"] == 1.0
    assert case["wall_seconds_max"] == 3.0
    assert case["wall_seconds_runs"] == [1.0, 2.0, 3.0]
    assert case["pack_score"]["score"] == 92
    assert case["pack_score"]["score_min"] == 88
    assert case["pack_score"]["score_max"] == 95
    assert case["pack_score"]["score_runs"] == [88, 92, 95]
    assert case["benchmark_score"]["score"] == 92
    assert case["benchmark_score"]["score_min"] == 88
    assert case["benchmark_score"]["score_max"] == 95
    assert len(case["runs"]) == 3
    assert case["estimated_cost_usd"] == 0.0
    # Per-run output dirs should exist as siblings under the case output dir.
    assert (tmp_path / "bench" / "core-llm" / "run-1").exists()
    assert (tmp_path / "bench" / "core-llm" / "run-2").exists()
    assert (tmp_path / "bench" / "core-llm" / "run-3").exists()

    summary = (tmp_path / "bench" / "benchmark.summary.md").read_text(encoding="utf-8")
    assert "1.0" in summary and "3.0" in summary  # spread is rendered
