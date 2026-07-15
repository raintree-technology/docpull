# docpull-live-search comparison

Suite: `13acf083e57757ed0e012f5df12e9bde7a4ba785503efe70b1ce036e30736910`
Protocol: `daf74d9c3b6f1649c1beae1fef8e7e81ad6ea5c406a257619247996932698552`
Scorer: `v2-unversioned`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Ops | Quality (completed) | Strict trial pass | pass@k | pass^k | Trial agreement | Checks | p50/p95 s | Provider spend |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| search | exa-search | 30 | 100.0% | 83.3% | 83.3% | 83.3% | 83.3% | 100.0% (k=2) | 95.8% | 0.497/1.424 | $0.420000 |
| search | firecrawl-search | 30 | 98.3% | 59.3% | 58.3% | 60.0% | 56.7% | 96.7% (k=2) | 85.8% | 1.658/2.720 | $0.720000 |
| search | parallel-search | 30 | 100.0% | 81.7% | 81.7% | 83.3% | 80.0% | 96.7% (k=2) | 95.4% | 2.462/4.995 | $0.300000 |
| search | tavily-search | 30 | 100.0% | 70.0% | 70.0% | 70.0% | 70.0% | 100.0% (k=2) | 91.7% | 2.202/4.813 | $0.960000 |

Quality (completed) is conditional on successful acquisition and must not be read as quality on failed or unsupported inputs. Trial agreement can include consistently incorrect outcomes and is weak evidence when k is small.

Provider spend excludes local compute, operator time, and maintenance. Latency marked not comparable is descriptive only and must not be ranked.
Pairs below 95% operational completion are labeled insufficient operational conformance; their failures are diagnostics, not successful-output quality evidence.
Core slices exclude managed-access fixtures and any case where at least one compared system recorded a robots-policy block. Boundary outcomes remain reported separately; the evaluator never bypasses robots or access controls.

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

Holm correction is scoped to the compared systems within each declared slice; exploratory family slices do not dilute the overall hypothesis family.

| Lane | A | B | Cases | Delta (95% paired bootstrap CI) | Discordant | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| search | exa-search | firecrawl-search | 30 | +26.7% (+10.0% to +43.3%) | 10 | 0.0215 | 0.1289 | no_significant_difference |
| search | exa-search | parallel-search | 30 | +3.3% (-10.0% to +16.7%) | 5 | 1.0000 | 1.0000 | no_significant_difference |
| search | exa-search | tavily-search | 30 | +13.3% (+0.0% to +30.0%) | 6 | 0.2188 | 0.8750 | no_significant_difference |
| search | firecrawl-search | parallel-search | 30 | -23.3% (-43.3% to -3.3%) | 11 | 0.0654 | 0.3271 | no_significant_difference |
| search | firecrawl-search | tavily-search | 30 | -13.3% (-30.0% to +0.0%) | 6 | 0.2188 | 0.8750 | no_significant_difference |
| search | parallel-search | tavily-search | 30 | +10.0% (-6.7% to +26.7%) | 7 | 0.4531 | 0.9062 | no_significant_difference |
