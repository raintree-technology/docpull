# live-neutral-extract comparison

Suite: `efb2d4094f7070ed59221123bee2e9245f8c11ad76fb12dba036ef80771293c3`
Protocol: `0f5b6368b239333fe10af4b639e330f363a969101aff1f640b1d86bfe4110e48`
Scorer: `v3-format-tolerant-concept-matching`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Ops | Quality (completed) | Strict trial pass | pass@k | pass^k | Trial agreement | Checks | p50/p95 s | Provider spend |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| extract (all) | docpull | 32 | 90.6% | 96.6% | 87.5% | 87.5% | 87.5% | 100.0% (k=2) | 90.6% | 0.969/3.839 (not comparable) | $0.000000 |
| extract (all) | exa-full | 32 | 98.4% | 96.8% | 95.3% | 96.9% | 93.8% | 96.9% (k=2) | 98.2% | 1.739/2.948 (not comparable) | $0.063000 |
| extract (all) | parallel | 32 | 100.0% | 96.9% | 96.9% | 96.9% | 96.9% | 100.0% (k=2) | 99.6% | 0.570/12.219 (not comparable) | $0.064000 |
| extract (all) | tavily | 32 | 96.9% | 96.8% | 93.8% | 93.8% | 93.8% | 100.0% (k=2) | 96.4% | 0.562/3.335 (not comparable) | $0.512000 |
| extract (boundary) | docpull | 4 | 25.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% (k=2) | 24.6% | 0.459/0.681 (not comparable) | $0.000000 |
| extract (boundary) | exa-full | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 1.845/2.204 (not comparable) | $0.008000 |
| extract (boundary) | parallel | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 0.755/1.223 (not comparable) | $0.008000 |
| extract (boundary) | tavily | 4 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 0.648/4.833 (not comparable) | $0.064000 |
| extract (core) | docpull | 28 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 1.109/5.125 (not comparable) | $0.000000 |
| extract (core) | exa-full | 28 | 98.2% | 96.4% | 94.6% | 96.4% | 92.9% | 96.4% (k=2) | 98.0% | 1.739/3.281 (not comparable) | $0.055000 |
| extract (core) | parallel | 28 | 100.0% | 96.4% | 96.4% | 96.4% | 96.4% | 100.0% (k=2) | 99.5% | 0.548/14.166 (not comparable) | $0.056000 |
| extract (core) | tavily | 28 | 96.4% | 96.3% | 92.9% | 92.9% | 92.9% | 100.0% (k=2) | 95.9% | 0.537/3.335 (not comparable) | $0.448000 |

Quality (completed) is conditional on successful acquisition and must not be read as quality on failed or unsupported inputs. Trial agreement can include consistently incorrect outcomes and is weak evidence when k is small.

Provider spend excludes local compute, operator time, and maintenance. Latency marked not comparable is descriptive only and must not be ranked.
Pairs below 95% operational completion are labeled insufficient operational conformance; their failures are diagnostics, not successful-output quality evidence.
Core slices exclude managed-access fixtures and any case where at least one compared system recorded a robots-policy block. Boundary outcomes remain reported separately; the evaluator never bypasses robots or access controls.

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

Holm correction is scoped to the compared systems within each declared slice; exploratory family slices do not dilute the overall hypothesis family.

| Lane | A | B | Cases | Delta (95% paired bootstrap CI) | Discordant | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| extract | docpull | exa-full | 32 | -6.2% (-21.9% to +9.4%) | 6 | 0.6875 | 1.0000 | insufficient_operational_conformance |
| extract | docpull | parallel | 32 | -9.4% (-21.9% to +3.1%) | 5 | 0.3750 | 1.0000 | insufficient_operational_conformance |
| extract | docpull | tavily | 32 | -6.2% (-21.9% to +9.4%) | 6 | 0.6875 | 1.0000 | insufficient_operational_conformance |
| extract | exa-full | parallel | 32 | -3.1% (-9.4% to +0.0%) | 1 | 1.0000 | 1.0000 | no_significant_difference |
| extract | exa-full | tavily | 32 | +0.0% (-12.5% to +12.5%) | 4 | 1.0000 | 1.0000 | no_significant_difference |
| extract | parallel | tavily | 32 | +3.1% (-6.2% to +12.5%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | docpull | exa-full | 28 | +7.1% (+0.0% to +17.9%) | 2 | 0.5000 | 1.0000 | no_significant_difference |
| extract (core) | docpull | parallel | 28 | +3.6% (+0.0% to +10.7%) | 1 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | docpull | tavily | 28 | +7.1% (+0.0% to +17.9%) | 2 | 0.5000 | 1.0000 | no_significant_difference |
| extract (core) | exa-full | parallel | 28 | -3.6% (-10.7% to +0.0%) | 1 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | exa-full | tavily | 28 | +0.0% (-14.3% to +14.3%) | 4 | 1.0000 | 1.0000 | no_significant_difference |
| extract (core) | parallel | tavily | 28 | +3.6% (-7.1% to +14.3%) | 3 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | docpull | exa-full | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 0.7500 | insufficient_operational_conformance |
| extract (boundary) | docpull | parallel | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 0.7500 | insufficient_operational_conformance |
| extract (boundary) | docpull | tavily | 4 | -100.0% (-100.0% to -100.0%) | 4 | 0.1250 | 0.7500 | insufficient_operational_conformance |
| extract (boundary) | exa-full | parallel | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | exa-full | tavily | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |
| extract (boundary) | parallel | tavily | 4 | +0.0% (+0.0% to +0.0%) | 0 | 1.0000 | 1.0000 | no_significant_difference |

## Boundary cases

- `dev.access.pypi-pydantic`: managed-access fixture outside default product boundary
- `dev.long.wikipedia-grace-hopper`: robots policy blocked acquisition
- `dev.standard.wcag-22`: robots policy blocked acquisition
- `test.docs.node-filesystem`: robots policy blocked acquisition
