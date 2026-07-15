# live-neutral-crawl comparison

Suite: `897d2a54e8f4c3f9f5954ee3107c789a6445944d0f3049b64b455ca3d3e0ec2c`
Protocol: `fbde4f4a0782d9e8a085e7b12cba464a4bbbfdd7a0b8daded026212977c33c40`
Scorer: `v2-unversioned`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Ops | Quality (completed) | Strict trial pass | pass@k | pass^k | Trial agreement | Checks | p50/p95 s | Provider spend |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| crawl | docpull | 8 | 100.0% | 87.5% | 87.5% | 87.5% | 87.5% | 100.0% (k=2) | 98.9% | 3.303/7.413 (not comparable) | $0.000000 |
| crawl | firecrawl-crawl | 8 | 0.0% | N/A | 0.0% | 0.0% | 0.0% | 100.0% (k=2) | 18.2% | 0.375/13.509 (not comparable) | $0.768000 |
| crawl | tavily-crawl-basic | 8 | 62.5% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% (k=2) | 46.0% | 0.508/24.409 (not comparable) | $0.384000 |

Quality (completed) is conditional on successful acquisition and must not be read as quality on failed or unsupported inputs. Trial agreement can include consistently incorrect outcomes and is weak evidence when k is small.

Provider spend excludes local compute, operator time, and maintenance. Latency marked not comparable is descriptive only and must not be ranked.
Pairs below 95% operational completion are labeled insufficient operational conformance; their failures are diagnostics, not successful-output quality evidence.
Core slices exclude managed-access fixtures and any case where at least one compared system recorded a robots-policy block. Boundary outcomes remain reported separately; the evaluator never bypasses robots or access controls.

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

Holm correction is scoped to the compared systems within each declared slice; exploratory family slices do not dilute the overall hypothesis family.

| Lane | A | B | Cases | Delta (95% paired bootstrap CI) | Discordant | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| crawl | docpull | firecrawl-crawl | 8 | +87.5% (+62.5% to +100.0%) | 7 | 0.0156 | 0.0469 | insufficient_operational_conformance |
| crawl | docpull | tavily-crawl-basic | 8 | +87.5% (+62.5% to +100.0%) | 7 | 0.0156 | 0.0469 | insufficient_operational_conformance |
| crawl | firecrawl-crawl | tavily-crawl-basic | 8 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | insufficient_operational_conformance |
