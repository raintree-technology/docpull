"""Benchmark harness tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import docpull.benchmark as benchmark
from docpull.benchmark import BenchmarkError, run_quick_benchmark
from docpull.cli import main


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
    assert (tmp_path / "tavily" / "documents.ndjson").exists()
    assert (tmp_path / "tavily" / "tavily.pack.json").exists()


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
