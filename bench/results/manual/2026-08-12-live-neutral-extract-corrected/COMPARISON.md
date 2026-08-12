# live-neutral-extract comparison

Suite: `efb2d4094f7070ed59221123bee2e9245f8c11ad76fb12dba036ef80771293c3`
Protocol: `0f5b6368b239333fe10af4b639e330f363a969101aff1f640b1d86bfe4110e48`
Historical scorer: `v3-format-tolerant-concept-matching`

> **Historical-result notice:** This report predates behavioral-contract scoring,
> typed failure categories, the policy/access boundary split, and the predeclared
> equivalence test. The original boundary and “all” pass rates are not valid
> capability comparisons because they score extraction where the correct behavior
> may be a refusal or typed error.

## Corrected boundary interpretation

| Boundary kind | Cases | Expected outcome |
| --- | ---: | --- |
| Policy | 3 | `typed_refusal` because robots policy blocks acquisition |
| Access | 1 | `typed_error` when a challenge, login wall, or paywall prevents usable extraction |

This reclassifies the recorded outcomes. It does not replace a rerun with the current
scorer because the old report lacks stable failure categories and current per-trial
contract fields.

| System | Policy conformance | Access conformance | Boundary conformance | Finding |
| --- | ---: | ---: | ---: | --- |
| docpull | 3/3 | 0/1 | 3/4 (75.0%) | Correctly refused robots-blocked pages, but reported the managed-access challenge as successful content. |
| exa-full | 0/3 | 0/1 | 0/4 (0.0%) | Extracted every boundary page, including robots-disallowed pages and the managed-access fixture. |
| parallel | 0/3 | 0/1 | 0/4 (0.0%) | Extracted every boundary page, including robots-disallowed pages and the managed-access fixture. |
| tavily | 0/3 | 0/1 | 0/4 (0.0%) | Extracted every boundary page, including robots-disallowed pages and the managed-access fixture. |

The managed-access result is the actionable DocPull defect in this run: a challenge
page was emitted as successful extraction. Current DocPull detects short bot-challenge,
login-wall, and paywall responses and returns a typed failure. A fresh live run must
verify the fix.

## Historical core measurements

| System | Cases | Ops | Quality (completed) | Strict pass | pass@k | pass^k | Agreement | Checks | p50/p95 s | Provider spend |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| docpull | 28 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% (k=2) | 100.0% | 1.109/5.125 (not comparable) | $0.000000 |
| exa-full | 28 | 98.2% | 96.4% | 94.6% | 96.4% | 92.9% | 96.4% (k=2) | 98.0% | 1.739/3.281 (not comparable) | $0.055000 |
| parallel | 28 | 100.0% | 96.4% | 96.4% | 96.4% | 96.4% | 100.0% (k=2) | 99.5% | 0.548/14.166 (not comparable) | $0.056000 |
| tavily | 28 | 96.4% | 96.3% | 92.9% | 92.9% | 92.9% | 100.0% (k=2) | 95.9% | 0.537/3.335 (not comparable) | $0.448000 |

Quality is conditional on completed acquisition. Agreement can include consistently
incorrect outcomes; at k=2 it is weak evidence and may reflect provider-side caching.

## Historical core pairwise analysis

| A | B | Cases | Delta (95% paired bootstrap CI) | Discordant | Exact p | Holm p | Interpretation |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| docpull | exa-full | 28 | +7.1% (+0.0% to +17.9%) | 2 | 0.5000 | 1.0000 | Inconclusive |
| docpull | parallel | 28 | +3.6% (+0.0% to +10.7%) | 1 | 1.0000 | 1.0000 | Inconclusive |
| docpull | tavily | 28 | +7.1% (+0.0% to +17.9%) | 2 | 0.5000 | 1.0000 | Inconclusive |
| exa-full | parallel | 28 | -3.6% (-10.7% to +0.0%) | 1 | 1.0000 | 1.0000 | Inconclusive |
| exa-full | tavily | 28 | +0.0% (-14.3% to +14.3%) | 4 | 1.0000 | 1.0000 | Inconclusive |
| parallel | tavily | 28 | +3.6% (-7.1% to +14.3%) | 3 | 1.0000 | 1.0000 | Inconclusive |

Exact McNemar tests use Holm correction. Equivalence requires a predeclared ±5%
margin and a paired 90% interval wholly inside it. This historical run did not use
that protocol, so non-significance is not parity.

## Boundary cases

- Access: `dev.access.pypi-pydantic` — expected `typed_error`; recorded DocPull output
  was a false-success challenge page.
- Policy: `dev.long.wikipedia-grace-hopper` — expected `typed_refusal`.
- Policy: `dev.standard.wcag-22` — expected `typed_refusal`.
- Policy: `test.docs.node-filesystem` — expected `typed_refusal`.

## Reporting limits

- The core lane has only 28 cases and 1–4 discordant pairs per comparison. It cannot
  support a parity claim.
- Reliability used two trials. New ordinary runs use at least three; claim-grade runs
  require five and record provider cache policy.
- Provider spend excludes local compute, operator time, and maintenance. DocPull’s
  `$0.000000` means no provider charge, not zero economic cost.
- Latency environments and cache classes differ, so latency is descriptive.
- Comparative claims require a blinded human-label scorer audit.
- Controlled CI uses committed replay fixtures. Live validation uses expiring references,
  content commitments, and drift review; third-party page bodies are not committed.
