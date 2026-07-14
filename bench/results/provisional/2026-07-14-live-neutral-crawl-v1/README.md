# live-neutral-crawl 1.0.0 results

These are three-trial live-web results for the same bounded documentation-crawl task, not mocked adapter outputs or a comparison of entire product surfaces.

| Lane | System | Pass all 3 (95% CI) | Quality | Median / p95 sec | Accounted USD |
| --- | --- | ---: | ---: | ---: | ---: |
| crawl | docpull | 87.5% (52.9%–97.8%) | 98.6% | 2.589 / 7.140 | $0.000000 |
| crawl | tavily-crawl-basic | 0.0% (0.0%–32.4%) | 19.4% | 0.497 / 3.004 | $0.576000 |
| crawl | tavily-crawl-guided-advanced | 0.0% (0.0%–32.4%) | 0.0% | 1.172 / 8.067 | $1.152000 |

DocPull passed 7/8 cases in all three trials at $0 provider cost; tavily-crawl-basic passed 0/8 and tavily-crawl-guided-advanced passed 0/8. The paired exact tests are tavily-crawl-basic p=0.0156, tavily-crawl-guided-advanced p=0.0156. This is a small, DocPull-authored suite, so the result supports a bounded documentation-crawl claim, not a universal crawler claim.

See [COMPARISON.md](COMPARISON.md) for every family and case, [METHODOLOGY.md](METHODOLOGY.md) for the protocol, and [suite.yaml](suite.yaml) for the frozen cases and gold checks.

## Unavailable systems

| System | Reason |
| --- | --- |
| context.dev | Comparable Crawl endpoint documented, but no credential was configured; no requests were made. |

No fixed-URL extraction, search, research, or cross-capability winner is claimed here.
