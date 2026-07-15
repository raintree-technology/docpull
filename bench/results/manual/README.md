# Manual live evaluations

These bundles are content-free, deterministic evaluation data for internal
product decisions. They do not compute a cross-lane score or name a global
winner. The July 14, 2026 run used two trials per case on the same frozen suite
and protocol within each published lane.

## July 14, 2026 decision note

| Lane | Result | Product implication |
| --- | --- | --- |
| Extract | DocPull 68.8%; Exa 92.2%; Firecrawl 93.8%; Parallel 93.8%; Tavily 90.6% trial pass | Prioritize DocPull PDF, managed-access, raw-text, long-form, and standards extraction gaps. |
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

## Evidence bundles

- [Extract](2026-07-14-live-neutral-extract-v2/README.md)
- [Crawl](2026-07-14-live-neutral-crawl-v2/README.md)
- [Search](2026-07-14-live-search-v2/README.md)

Each bundle includes the frozen suite, portable reports, a protocol-checked
comparison, methodology, and a SHA-256 publication manifest. Fetched response
bodies and credentials are excluded.
