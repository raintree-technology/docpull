# Methodology

## Scope

This is a fixed-URL `extract` benchmark with 32 public live-web cases. It does not score open-web search, crawling, research synthesis, citation correctness, or DocPull's local pack/lock/provenance features.

Suite version: `1.0.0`  
Suite SHA-256: `02b0ae9e23cb938b27082e9f27e0a40468672bfab6491fa8c80a15efdc4a1786`

## Corpus

The suite has 16 development cases and 16 domain-disjoint test cases. Gold terms were manually checked against first-party sources on the date recorded per case.

| Family | Cases |
| --- | ---: |
| long-form | 2 |
| long-reference | 4 |
| managed-access | 1 |
| modern-web | 2 |
| pdf | 2 |
| raw-text | 3 |
| standards | 7 |
| technical-docs | 11 |

The managed-access case is reported as its own family because a managed service and a robots-respecting local HTTP client have materially different infrastructure and policy.

## Systems and procedure

Every system received only the fixed URL, never the gold terms. Each case ran three times with concurrency 1. Every attempt, error, and partial result remains in the reports.

| System | Adapter version | Trials | Concurrency | Run time (UTC) |
| --- | --- | ---: | ---: | --- |
| docpull | `6.0.1` | 3 | 1 | 2026-07-14T20:51:45.350647+00:00 |
| parallel | `v1-live-full-2026-07-14` | 3 | 1 | 2026-07-14T20:57:16.704435+00:00 |
| tavily | `extract-basic-2026-07-14` | 3 | 1 | 2026-07-14T20:54:28.102624+00:00 |
| tavily-advanced | `extract-advanced-2026-07-14` | 3 | 1 | 2026-07-14T20:55:57.640735+00:00 |

Configurations:

- DocPull 6.0.1 used its public CLI, single-page mode, no browser, no cache flag, no paid/cloud route, and `--budget 0`.
- Tavily used official `/extract` Markdown output with either `basic` or `advanced` depth, images disabled, usage enabled, and the case timeout capped at 60 seconds.
- Parallel used official `/v1/extract`, full content enabled, a 600-second maximum cache age, live-fetch timeout capped at 60 seconds, and cache fallback enabled.

## Scoring

A trial completes only when the adapter returns the minimum record and character counts. A trial passes only when it also contains every required term, contains no forbidden term, and keeps output URLs within allowed domains. `Pass all` is the percentage of cases that passed all three trials. Quality is the mean of the declared deterministic axes, with failed or undersized outputs forced to zero.

No LLM judge is used in this lane. The metrics favor auditability over semantic nuance and should not be interpreted as a universal extraction-quality score.

## Cost accounting

DocPull's observed provider cost is $0 because no paid route was enabled. Hosted totals are conservative list-price upper bounds unless the provider returned a dollar total. Free-tier credits, subscription allocations, and volume pricing can make an invoice lower.

## Calibration disclosure

Before the measured runs, one DocPull-only calibration pass was used to replace formatting-fragile gold phrases and one PostgreSQL table-of-contents URL with a substantive page. No failing capability category or access failure was removed. All measured systems then ran the frozen suite hash above.

## Publication and rights

Published reports contain URLs, scores, timings, costs, character counts, and SHA-256 content hashes. They do not contain fetched page bodies, credentials, or private URLs. Rights metadata and redistribution conditions are recorded in `suite.yaml`.

## Limitations

- This is a 32-case first release, not a census of the web.
- Live sites and hosted implementations can change after the recorded run.
- Some families contain only one or two cases; family results are diagnostic, not broad claims.
- The suite tests fixed-URL extraction only; WANDR and other agent-research suites belong to a separate end-to-end lane.
- Latency is wall time from one machine and includes network variance.
