# live-neutral-extract comparison

Suite: `efb2d4094f7070ed59221123bee2e9245f8c11ad76fb12dba036ef80771293c3`
Protocol: `8e8af82f7ee7ee8d891af143ac877ef9944615f6f4b53bd9de64d2788910808d`
Scorer: `v2-unversioned`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Ops | Quality (completed) | Strict trial pass | pass@k | pass^k | Trial agreement | Checks | p50/p95 s | Provider spend |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| extract (all) | docpull | 32 | 75.0% | 91.7% | 68.8% | 68.8% | 68.8% | 100.0% (k=2) | 75.4% | 1.069/2.959 (not comparable) | $0.000000 |
| extract (all) | exa-full | 32 | 98.4% | 93.7% | 92.2% | 93.8% | 90.6% | 96.9% (k=2) | 97.8% | 1.208/3.144 (not comparable) | $0.063000 |
| extract (all) | firecrawl | 32 | 100.0% | 93.8% | 93.8% | 93.8% | 93.8% | 100.0% (k=2) | 99.1% | 1.664/2.857 (not comparable) | $0.384000 |
| extract (all) | parallel | 32 | 100.0% | 93.8% | 93.8% | 93.8% | 93.8% | 100.0% (k=2) | 99.1% | 0.488/3.388 (not comparable) | $0.064000 |
| extract (all) | tavily | 32 | 96.9% | 93.5% | 90.6% | 90.6% | 90.6% | 100.0% (k=2) | 96.0% | 0.549/6.669 (not comparable) | $0.512000 |
| extract (boundary) | docpull | 4 | 25.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% (k=2) | 24.6% | 0.445/0.671 (not comparable) | $0.000000 |
| extract (boundary) | exa-full | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 1.443/2.741 (not comparable) | $0.008000 |
| extract (boundary) | firecrawl | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 2.120/4.112 (not comparable) | $0.048000 |
| extract (boundary) | parallel | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 0.509/1.029 (not comparable) | $0.008000 |
| extract (boundary) | tavily | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 0.606/3.816 (not comparable) | $0.064000 |
| extract (core) | docpull | 28 | 82.1% | 95.7% | 78.6% | 78.6% | 78.6% | 100.0% (k=2) | 82.7% | 1.145/3.036 (not comparable) | $0.000000 |
| extract (core) | exa-full | 28 | 98.2% | 92.7% | 91.1% | 92.9% | 89.3% | 96.4% (k=2) | 97.4% | 1.179/3.458 (not comparable) | $0.055000 |
| extract (core) | firecrawl | 28 | 100.0% | 92.9% | 92.9% | 92.9% | 92.9% | 100.0% (k=2) | 99.0% | 1.571/2.857 (not comparable) | $0.336000 |
| extract (core) | parallel | 28 | 100.0% | 92.9% | 92.9% | 92.9% | 92.9% | 100.0% (k=2) | 99.0% | 0.488/4.024 (not comparable) | $0.056000 |
| extract (core) | tavily | 28 | 96.4% | 92.6% | 89.3% | 89.3% | 89.3% | 100.0% (k=2) | 95.4% | 0.484/7.850 (not comparable) | $0.448000 |

Quality (completed) is conditional on successful acquisition and must not be read as quality on failed or unsupported inputs. Trial agreement can include consistently incorrect outcomes and is weak evidence when k is small.

Provider spend excludes local compute, operator time, and maintenance. Latency marked not comparable is descriptive only and must not be ranked.
Pairs below 95% operational completion are labeled insufficient operational conformance; their failures are diagnostics, not successful-output quality evidence.
Core slices exclude managed-access fixtures and any case where at least one compared system recorded a robots-policy block. Boundary outcomes remain reported separately; the evaluator never bypasses robots or access controls.

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

Holm correction is scoped to the compared systems within each declared slice; exploratory family slices do not dilute the overall hypothesis family.

| Lane | A | B | Cases | Delta (95% paired bootstrap CI) | Discordant | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| extract | docpull | exa-full | 32 | -21.9% (-40.6% to -6.2%) | 9 | 0.0391 | 0.3125 | insufficient_operational_conformance |
| extract | docpull | firecrawl | 32 | -25.0% (-43.8% to -6.2%) | 10 | 0.0215 | 0.2148 | insufficient_operational_conformance |
| extract | docpull | parallel | 32 | -25.0% (-43.8% to -6.2%) | 10 | 0.0215 | 0.2148 | insufficient_operational_conformance |
| extract | docpull | tavily | 32 | -21.9% (-37.5% to -6.2%) | 9 | 0.0391 | 0.3125 | insufficient_operational_conformance |
| extract | exa-full | firecrawl | 32 | -3.1% (-15.6% to +6.2%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract | exa-full | parallel | 32 | -3.1% (-9.4% to +0.0%) | 1 | 1.0000 | 1.0000 | no_significant_difference |
| extract | exa-full | tavily | 32 | +0.0% (-12.5% to +12.5%) | 4 | 1.0000 | 1.0000 | no_significant_difference |
| extract | firecrawl | parallel | 32 | +0.0% (-9.4% to +9.4%) | 2 | 1.0000 | 1.0000 | no_significant_difference |
| extract | firecrawl | tavily | 32 | +3.1% (-6.2% to +12.5%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract | parallel | tavily | 32 | +3.1% (-6.2% to +12.5%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | docpull | exa-full | 28 | -10.7% (-25.0% to +3.6%) | 5 | 0.3750 | 1.0000 | insufficient_operational_conformance |
| extract (core) | docpull | firecrawl | 28 | -14.3% (-32.1% to +0.0%) | 6 | 0.2188 | 1.0000 | insufficient_operational_conformance |
| extract (core) | docpull | parallel | 28 | -14.3% (-32.1% to +0.0%) | 6 | 0.2188 | 1.0000 | insufficient_operational_conformance |
| extract (core) | docpull | tavily | 28 | -10.7% (-25.0% to +3.6%) | 5 | 0.3750 | 1.0000 | insufficient_operational_conformance |
| extract (core) | exa-full | firecrawl | 28 | -3.6% (-14.3% to +7.1%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | exa-full | parallel | 28 | -3.6% (-10.7% to +0.0%) | 1 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | exa-full | tavily | 28 | +0.0% (-14.3% to +14.3%) | 4 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | firecrawl | parallel | 28 | +0.0% (-10.7% to +10.7%) | 2 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | firecrawl | tavily | 28 | +3.6% (-7.1% to +14.3%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | parallel | tavily | 28 | +3.6% (-7.1% to +14.3%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | docpull | exa-full | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 1.0000 | insufficient_operational_conformance |
| extract (boundary) | docpull | firecrawl | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 1.0000 | insufficient_operational_conformance |
| extract (boundary) | docpull | parallel | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 1.0000 | insufficient_operational_conformance |
| extract (boundary) | docpull | tavily | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 1.0000 | insufficient_operational_conformance |
| extract (boundary) | exa-full | firecrawl | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | exa-full | parallel | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | exa-full | tavily | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | firecrawl | parallel | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | firecrawl | tavily | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | parallel | tavily | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |

## Boundary cases

- `dev.access.pypi-pydantic`: managed-access fixture outside default product boundary
- `dev.long.wikipedia-grace-hopper`: robots policy blocked acquisition
- `dev.standard.wcag-22`: robots policy blocked acquisition
- `test.docs.node-filesystem`: robots policy blocked acquisition
