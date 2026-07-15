# Manual live evaluations

These bundles are content-free, deterministic evaluation data for internal
product decisions. They do not compute a cross-lane score or name a global
winner. The July 14, 2026 run used two trials per case on the same frozen suite
and protocol within each published lane.

## Current scorer-bound extract rerun

The v3 scorer-bound rerun compares DocPull with newly acquired Exa Full,
Parallel, and Tavily output. Firecrawl was unavailable because no credential was
configured, so no Firecrawl request was made.

| Scope | DocPull | Exa Full | Parallel | Tavily |
| --- | ---: | ---: | ---: | ---: |
| All-case strict trial pass | 87.5% | 95.3% | 96.9% | 93.8% |
| 28-case core strict trial pass | 100.0% | 94.6% | 96.4% | 92.9% |
| All-case operational completion | 90.6% | 98.4% | 100.0% | 96.9% |

DocPull's four boundary cases are one managed-access target and three
robots-policy blocks. Pairwise tests found no statistically significant core
difference; the sample remains too small for public comparative claims. The
rerun accounted for $0.639 in provider spend: $0.063 actual Exa cost and $0.576
in conservative Parallel/Tavily upper bounds, against a $5 authorization.

## Historical July 14, 2026 decision note

| Lane | Result | Product implication |
| --- | --- | --- |
| Extract | Historical all-case pass: DocPull 68.8%; Exa 92.2%; Firecrawl 93.8%; Parallel 93.8%; Tavily 90.6%. Historical 28-case core slice: DocPull 78.6%. | PDF, raw-text, and RFC remediation must be measured in a fresh scorer-bound run; managed-access and robots cases are reported as boundaries, not bypass targets. |
| Crawl | DocPull 87.5%; Tavily 0%; Firecrawl 0% trial pass | Preserve bounded crawl as a DocPull strength. Firecrawl's result is an operational failure on the tested account/protocol (HTTP 400 followed by 429), not evidence that its successful crawl output is low quality. |
| Search | Exa 83.3%; Parallel 81.7%; Tavily 70.0%; Firecrawl 58.3% trial pass | If native search becomes a product requirement, Exa and Parallel are the strongest candidates to investigate on this suite. DocPull remains unsupported in this lane. |

The published reports account for $4.575 in actual or conservative provider
cost. Including the excluded Firecrawl corrective run, two bounded diagnostics,
and the extra amount reserved for Exa, the conservative all-provider ceiling was
$4.972. No Context.dev credential was used and no Context.dev request was made.

The result is directional: suites are DocPull-authored, live-web state can
change, and Holm-corrected pairwise tests did not establish statistical
significance. Effect sizes and failure slices should guide follow-up work; a
non-significant result is not evidence of equivalence.

The v4 analysis bundles preserve the original report scores under the explicit
`v2-unversioned` scorer label, separate operational completion from conditional
quality, and report managed-access/robots cases as boundary slices. They do not
mix those historical provider reports with later DocPull runs produced by a
different scorer.

## Evidence bundles

- [Current scorer-bound extract](2026-07-14-live-neutral-extract-v5-current-v3/README.md)
- [Extract](2026-07-14-live-neutral-extract-v4-analysis/README.md)
- [Crawl](2026-07-14-live-neutral-crawl-v4-analysis/README.md)
- [Search](2026-07-14-live-search-v4-analysis/README.md)

Each bundle includes the frozen suite, portable reports, a protocol-checked
comparison, methodology, and a SHA-256 publication manifest. Fetched response
bodies and credentials are excluded.
