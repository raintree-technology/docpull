# Methodology

This bundle is generated benchmark data and methodology, not a marketing claim.
Gold expectations were retained by the harness and were not sent to adapters.
One deterministic canonical scorer per lane produced the stored assertion vectors.
No LLM judge or cross-lane composite was used.

Suite version: `2.0.0`
Protocol SHA-256: `daf74d9c3b6f1649c1beae1fef8e7e81ad6ea5c406a257619247996932698552`
Lanes: search

| System | Version | Revision | Dirty | Environment | Network | Cache | Retry | Trials |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| exa-search | `search-auto-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| firecrawl-search | `v2-search-web-v1` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| parallel-search | `v1-search-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| tavily-search | `search-advanced-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |

Portable reports contain URLs after query sanitization, hashes, lengths, timings, usage, cost classifications, statuses, and score vectors. Fetched bodies are excluded.
