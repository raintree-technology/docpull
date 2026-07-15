# Methodology

This bundle is generated benchmark data and methodology, not a marketing claim.
Gold expectations were retained by the harness and were not sent to adapters.
One deterministic canonical scorer per lane produced the stored assertion vectors.
No LLM judge or cross-lane composite was used.

Suite version: `1.0.0`
Protocol SHA-256: `8e8af82f7ee7ee8d891af143ac877ef9944615f6f4b53bd9de64d2788910808d`
Lanes: extract

| System | Version | Revision | Dirty | Environment | Network | Cache | Retry | Trials |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| docpull | `6.0.1` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | disabled | docpull_public_defaults | 2 |
| exa-full | `contents-live-full-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| firecrawl | `v2-scrape-main-v1` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| parallel | `v1-live-full-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| tavily | `extract-basic-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |

Portable reports contain URLs after query sanitization, hashes, lengths, timings, usage, cost classifications, statuses, and score vectors. Fetched bodies are excluded.
