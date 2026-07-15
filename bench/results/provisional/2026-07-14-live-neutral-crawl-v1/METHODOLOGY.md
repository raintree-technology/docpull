# Methodology

## Scope

This is a bounded documentation-site `crawl` benchmark with 8 public live-web cases. It scores recovery of a small declared documentation subtree under a fixed page, depth, domain, and path budget. It does not score open-web search, arbitrary website crawling, research synthesis, or a provider's complete product surface.

Suite version: `1.0.0`  
Suite SHA-256: `86955db1e0e681def7926a36c4e72af5b2be3205f8caba7a3bdc67e9f7b42582`

## Corpus

The suite has 4 development cases and 4 domain-disjoint test cases. Each target is a first-party documentation subtree. Gold URLs and terms were manually checked on the date recorded per case.

| Family | Cases |
| --- | ---: |
| framework-tutorial-crawl | 4 |
| language-reference-crawl | 4 |

## Systems and procedure

Every system received the same start URL, allowed domain and path prefixes, eight-page maximum, depth-one maximum, and timeout. Systems never received the gold URLs or gold terms. Each case ran three times at concurrency 1. Errors and undersized responses remain in the published reports.

| System | Adapter version | Trials | Concurrency | Run time (UTC) |
| --- | --- | ---: | ---: | --- |
| docpull | `6.0.1` | 3 | 1 | 2026-07-14T21:36:41.180842+00:00 |
| tavily-crawl-basic | `crawl-basic-2026-07-14` | 3 | 1 | 2026-07-14T21:41:28.165927+00:00 |
| tavily-crawl-guided-advanced | `crawl-guided-advanced-2026-07-14` | 3 | 1 | 2026-07-14T21:42:35.303303+00:00 |

Configurations:

- DocPull used its public CLI in direct HTTP mode, with browser and cache disabled, the case's path/page/depth limits, and `--budget 0`.
- Tavily Crawl Basic used official `/crawl`, regular mapping, basic extraction, the case's path filters and limits, external domains disabled, and usage enabled.
- Tavily Crawl Guided Advanced used the same endpoint and limits, advanced extraction, and one generic instruction asking it to stay inside the selected documentation path. The instruction contained no gold URL or gold term.

## Scoring

A benchmark trial completes only when the normalized response reaches the declared minimum record and character counts. An HTTP-successful provider response can therefore be benchmark-incomplete. A trial passes only when it also contains every required term, recovers every required URL, and keeps output URLs within allowed domains. `Pass all` is the percentage of cases passing all three trials.

The report includes Wilson 95% intervals, macro-family pass rate, trial stability, median and p95 latency, and exact paired McNemar tests. No LLM judge is used. These deterministic checks measure bounded documentation recovery, not general semantic usefulness.

## Cost accounting

DocPull's observed provider cost is $0 because no paid route was enabled. Tavily totals are conservative list-price upper bounds calculated from the maximum allowed mapping and extraction work. Provider-returned credit counts remain in each report. Actual invoices can be lower because a crawl may stop early or use subscription credits.

## Calibration disclosure

The suite is DocPull-authored. A one-pass DocPull discovery audit was used to choose source-checkable documentation subtrees and realistic bounds before the measured runs. No system, case, or failed category was removed after the three-system measurements began. This design can favor DocPull's traversal model, so the result is maintainer-run Tier B evidence and should be independently reproduced or extended before use as a broad claim.

## Publication and rights

Published reports contain URLs, scores, timings, costs, character counts, and SHA-256 content hashes. They do not contain fetched page bodies, credentials, or private URLs. Rights metadata and redistribution conditions are recorded in `suite.yaml`.

## Limitations

- This initial lane has 8 documentation targets; its confidence intervals are intentionally shown and are wide.
- It was run from one machine and region at one point in time.
- It does not test JavaScript-heavy sites, authenticated corpora, large recursive crawls, or open-web discovery.
- The same numeric bounds do not imply identical private traversal algorithms.
- Context.dev documents a comparable crawl API but was not entered because no credential was configured; Exa and Parallel were not scored in this lane.
- Live pages and hosted implementations can change after the recorded run.
