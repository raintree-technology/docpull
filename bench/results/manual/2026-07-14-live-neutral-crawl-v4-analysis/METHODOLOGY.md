# Methodology

This bundle is generated benchmark data and methodology, not a marketing claim.
Gold expectations were retained by the harness and were not sent to adapters.
One deterministic canonical scorer per lane produced the stored assertion vectors.
No LLM judge or cross-lane composite was used.

Suite version: `1.0.0`
Protocol SHA-256: `fbde4f4a0782d9e8a085e7b12cba464a4bbbfdd7a0b8daded026212977c33c40`
Analysis version: `v3-ops-quality-slice-holm-paired-bootstrap`
Lanes: crawl

| System | Version | Revision | Dirty | Environment | Network | Cache | Retry | Trials |
| --- | --- | --- | --- | --- | --- | --- | --- | ---: |
| docpull | `6.0.1` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | disabled | docpull_public_defaults | 2 |
| firecrawl-crawl | `v2-crawl-bounded-v1` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |
| tavily-crawl-basic | `crawl-basic-v2` | `fcac51d1b6b670466303a45524bedf8ff157892f` | True | local-macos-live-2026-07-14 | open | provider_managed | max_attempts=1 | 2 |

Portable reports contain URLs after query sanitization, hashes, lengths, timings, usage, cost classifications, statuses, and score vectors. Fetched bodies are excluded.

WARNING: This is a migrated historical fixture. It is not current evidence and is not approved for marketing use.
