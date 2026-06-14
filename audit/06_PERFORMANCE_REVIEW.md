# Performance Review

## Benchmarks Found

- `tests/benchmarks/test_performance.py`: conversion, deduplication, pipeline,
  config, and memory-oriented microbenchmarks.
- `tests/benchmarks/test_10k_pages.py`: synthetic localhost 10,000-page
  benchmark.
- `.github/workflows/benchmark.yml`: nightly/on-demand benchmark workflow.
- `docpull benchmark quick`: provider/core benchmark harness.

## Benchmarks / Gates Run In Current Pass

- `.venv/bin/pytest -q`: passed, 522 tests.
- `.venv/bin/mypy src/docpull`: passed, 73 source files.
- Targeted output/retrieval/framework tests passed after adding SQLite FTS and
  scraper API.
- Full 10,000-page benchmark was not run in this interactive pass.

## Static Performance Observations

- Async HTTP and streaming discovery are implemented.
- NDJSON flushes every record, which is good for pipes and agents but can be
  write-heavy for very large file-output runs.
- SQLite output now creates/backfills `documents_fts`, making local retrieval
  cheaper than file-by-file regex scans for SQLite users.
- MCP `grep_docs` still scans Markdown files line-by-line; it should eventually
  prefer an indexed store when available.
- `read_doc` has a large-file guard.
- Sitemap and robots caps exist, but sitemap-specific cap should be enforced
  earlier in the HTTP read path.

## Scaling Concerns

- Large documentation sets need a unified retrieval layer across Markdown,
  NDJSON, SQLite, and MCP.
- NDJSON per-record flush can bottleneck on slow filesystems; a batch flush
  option may help when not writing to stdout.
- Repeated `rglob("*.md")` scans in MCP cache/index operations can become slow
  on large caches.
- Streaming discovery/backpressure should be covered by a routine benchmark
  profile.

## Recommended Performance Work

1. Run `DOCPULL_BENCHMARK_10K=1 .venv/bin/pytest -v -s
   tests/benchmarks/test_10k_pages.py` before a performance-focused release.
2. Add a 100-page and 1,000-page benchmark that can run in CI quick mode.
3. Add MCP grep vs SQLite FTS benchmark on synthetic large cache.
4. Add memory assertions for sitemap/robots/body caps.
5. Add optional batch flush for NDJSON file output while preserving stdout
   flush semantics.
