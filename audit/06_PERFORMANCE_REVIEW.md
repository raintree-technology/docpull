# Performance Review

## Benchmarks Found

- `tests/benchmarks/test_performance.py`: conversion, deduplication, pipeline, config, memory-oriented microbenchmarks.
- `tests/benchmarks/test_10k_pages.py`: synthetic localhost 10,000-page benchmark.
- `.github/workflows/benchmark.yml`: nightly/on-demand 10k benchmark, fails if peak RSS exceeds 200 MB or duplicate fraction is wrong.
- README references a synthetic 10,000-page localhost site around `README.md:286`.

## Benchmarks Run

No performance benchmark completed. `pytest` collection fails before benchmark execution due `FetchEvent` import error.

Commands attempted:
- `pytest -q`: 11 collection errors.
- `pytest --cov=src/docpull --cov-report=term-missing`: 11 collection errors.

## Static Performance Observations

- Async HTTP and streaming discovery are implemented at the architecture level.
- NDJSON writer flushes every record in `src/docpull/pipeline/steps/save_ndjson.py:63-69`, good for streaming but potentially write-heavy for large crawls.
- `grep_docs` reads each Markdown file into memory line lists in `src/docpull/mcp/tools.py:530-532`; acceptable for small docs, but not indexed and can be slow for large caches.
- `read_doc` refuses files over 1 MB before reading, reducing accidental huge reads.
- Sitemap and robots caps exist, but sitemap-specific cap is post-fetch.
- Root TypeScript MCP uses pgvector and batch inserts under PostgreSQL bind limits in `mcp/src/db.ts:24-29` and `197-231`.

## Scaling Concerns

- Large documentation sets need persistent searchable index beyond regex scan.
- NDJSON per-record flush can bottleneck on slow filesystems; batch flush option could improve throughput when not piping.
- `countMarkdownFiles` and `rglob("*.md")` scans are repeated in MCP cache/index operations.
- Streaming discovery/backpressure claims need runtime verification.
- Cache/resume/frontier code in dirty worktree is not validated.

## Recommended Performance Work

1. Restore runnable tests and run `DOCPULL_BENCHMARK_10K=1 pytest -v -s tests/benchmarks/test_10k_pages.py`.
2. Add a 100-page and 1,000-page local benchmark that runs by default or in CI quick mode.
3. Add MCP grep benchmark on synthetic large cache; compare regex scan vs SQLite FTS.
4. Add memory assertions for sitemap/robots/body caps.
5. Add optional batch flush for NDJSON file output while preserving stdout flush.
