# live-neutral-extract 1.0.0 results

These are real live-web results, not mocked adapter outputs. The broad extraction result does not show DocPull beating every hosted service.

| Lane | System | Pass all 3 (95% CI) | Quality | Median / p95 sec | Accounted USD |
| --- | --- | ---: | ---: | ---: | ---: |
| extract | parallel | 93.8% (79.9%–98.3%) | 96.4% | 0.357 / 3.185 | $0.096000 |
| extract | tavily-advanced | 93.8% (79.9%–98.3%) | 96.4% | 0.503 / 3.541 | $1.536000 |
| extract | tavily | 84.4% (68.2%–93.1%) | 90.1% | 0.591 / 11.182 | $0.768000 |
| extract | docpull | 68.8% (51.4%–82.0%) | 68.8% | 0.866 / 2.788 | $0.000000 |

The strongest supported DocPull claim in this run is narrower: it passed all 11/11 technical-documentation cases in all three trials with no paid provider route. It lost the broad suite on PDFs, managed access, several raw formats, robots-blocked sources, and one long standard.

See [COMPARISON.md](COMPARISON.md) for every family and case, [METHODOLOGY.md](METHODOLOGY.md) for the protocol, and [suite.yaml](suite.yaml) for the frozen cases and gold checks.

## Unavailable systems

| System | Reason |
| --- | --- |
| exa | Configured credential returned HTTP 402 during a one-case live probe; no scored matrix was run. |
| exa-full | Configured credential returned HTTP 402 during a one-case live probe; no scored matrix was run. |
| context.dev | No credential was configured; no requests were made. |

No cross-capability or end-to-end research winner is claimed.
