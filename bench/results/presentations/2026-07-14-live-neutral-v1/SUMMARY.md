# Historical benchmark presentation

**Presentation only — this is not new experimental evidence.**

This page presents the committed [source bundle](../../provisional/2026-07-14-live-neutral-v1/README.md) without changing it. Interpret results under the source bundle’s methodology, status, and limitations.

- Source publication schema: `1`
- Source status: `historical`
- Source manifest SHA-256: `b8ed7afcad9cb7d16aee0f44d3d670af73fff78c318dbfd230f7e614395899fa`

## Source bundle summary

## live-neutral-extract 1.0.0 results

These are real live-web results, not mocked adapter outputs. The broad extraction result does not show DocPull beating every hosted service.

| System | Pass all 3 | Quality | Mean seconds | Accounted USD |
| --- | ---: | ---: | ---: | ---: |
| parallel | 93.8% | 96.4% | 0.759 | $0.096000 |
| tavily-advanced | 93.8% | 96.4% | 0.864 | $1.536000 |
| tavily | 84.4% | 90.1% | 1.627 | $0.768000 |
| docpull | 68.8% | 68.8% | 1.184 | $0.000000 |

The strongest supported DocPull claim in this run is narrower: it passed all 11/11 technical-documentation cases in all three trials with no paid provider route. It lost the broad suite on PDFs, managed access, several raw formats, robots-blocked sources, and one long standard.

See [COMPARISON.md](../../provisional/2026-07-14-live-neutral-v1/COMPARISON.md) for every family and case, [METHODOLOGY.md](../../provisional/2026-07-14-live-neutral-v1/METHODOLOGY.md) for the protocol, and [suite.yaml](../../provisional/2026-07-14-live-neutral-v1/suite.yaml) for the frozen cases and gold checks.

### Unavailable systems

| System | Reason |
| --- | --- |
| exa | Configured credential returned HTTP 402 during a one-case live probe; no scored matrix was run. |
| exa-full | Configured credential returned HTTP 402 during a one-case live probe; no scored matrix was run. |
| context.dev | No credential was configured; no requests were made. |

No cross-capability or end-to-end research winner is claimed.
