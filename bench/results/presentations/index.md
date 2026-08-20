# Historical benchmark presentations

These v4 presentation pages summarize committed publication bundles without changing
their evidence. Each presentation manifest records the SHA-256 of its source
publication manifest. A presentation is not a new experiment or a new claim source.

| Source bundle | Current presentation |
| --- | --- |
| `manual/2026-07-14-live-neutral-crawl-v2` | [Summary](2026-07-14-live-neutral-crawl-v2/SUMMARY.md) |
| `manual/2026-07-14-live-neutral-crawl-v4-analysis` | [Summary](2026-07-14-live-neutral-crawl-v4-analysis/SUMMARY.md) |
| `manual/2026-07-14-live-neutral-extract-v2` | [Summary](2026-07-14-live-neutral-extract-v2/SUMMARY.md) |
| `manual/2026-07-14-live-neutral-extract-v4-analysis` | [Summary](2026-07-14-live-neutral-extract-v4-analysis/SUMMARY.md) |
| `manual/2026-07-14-live-neutral-extract-v5-current-v3` | [Summary](2026-07-14-live-neutral-extract-v5-current-v3/SUMMARY.md) |
| `manual/2026-07-14-live-search-v2` | [Summary](2026-07-14-live-search-v2/SUMMARY.md) |
| `manual/2026-07-14-live-search-v4-analysis` | [Summary](2026-07-14-live-search-v4-analysis/SUMMARY.md) |
| `provisional/2026-07-14-context-lifecycle-v1` | [Summary](2026-07-14-context-lifecycle-v1/SUMMARY.md) |
| `provisional/2026-07-14-live-neutral-crawl-v1` | [Summary](2026-07-14-live-neutral-crawl-v1/SUMMARY.md) |
| `provisional/2026-07-14-live-neutral-v1-statistical` | [Summary](2026-07-14-live-neutral-v1-statistical/SUMMARY.md) |
| `provisional/2026-07-14-live-neutral-v1` | [Summary](2026-07-14-live-neutral-v1/SUMMARY.md) |

Verify any row with:

```bash
uv run --project bench --locked docpull-bench presentation verify \
  bench/results/presentations/<bundle-id>
```
