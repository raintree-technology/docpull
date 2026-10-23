# Formal models

Each flow has a statechart of what users see and a model in `tests/` that
`tests/model_check.py` explores state by state, like TLC for TLA+. Every
invariant holds on the current code. Each fixed bug is re-enabled as a mutant
that the checker must catch.

| Flow | Statechart | Model | Regression tests |
| --- | --- | --- | --- |
| Fetch run lifecycle | [fetch-pipeline.md](fetch-pipeline.md) | `tests/test_model_fetch_pipeline.py` | `tests/test_fetch_lifecycle.py` |
| Crawl and politeness | [crawl-politeness.md](crawl-politeness.md) | `tests/test_model_crawl_politeness.py` | `tests/test_crawl_regressions.py` |
| Cache and lockfile | [cache-lock.md](cache-lock.md) | `tests/test_model_cache_lock.py` | `tests/test_cache_lock_regressions.py` |
| Jobs, monitor, MCP | [jobs-monitor-mcp.md](jobs-monitor-mcp.md) | `tests/test_model_jobs_monitor.py` | `tests/test_monitor_fetch_failures.py` |

Not modeled: conversion, context packs, and exports. They are pure transforms
with no state to explore.
