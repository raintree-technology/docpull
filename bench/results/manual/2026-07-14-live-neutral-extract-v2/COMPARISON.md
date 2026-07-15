# live-neutral-extract comparison

Suite: `efb2d4094f7070ed59221123bee2e9245f8c11ad76fb12dba036ef80771293c3`
Protocol: `8e8af82f7ee7ee8d891af143ac877ef9944615f6f4b53bd9de64d2788910808d`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Trial pass | pass@k | pass^k | Stability | Checks | p50/p95 s | Cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| extract | docpull | 32 | 68.8% | 68.8% | 68.8% | 100.0% | 75.4% | 1.069/2.959 (not comparable) | $0.000000 |
| extract | exa-full | 32 | 92.2% | 93.8% | 90.6% | 96.9% | 97.8% | 1.208/3.144 (not comparable) | $0.063000 |
| extract | firecrawl | 32 | 93.8% | 93.8% | 93.8% | 100.0% | 99.1% | 1.664/2.857 (not comparable) | $0.384000 |
| extract | parallel | 32 | 93.8% | 93.8% | 93.8% | 100.0% | 99.1% | 0.488/3.388 (not comparable) | $0.064000 |
| extract | tavily | 32 | 90.6% | 90.6% | 90.6% | 100.0% | 96.0% | 0.549/6.669 (not comparable) | $0.512000 |

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

| Lane | A | B | Cases | Delta | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| extract | docpull | exa-full | 32 | -21.9% | 0.0391 | 1.0000 | no_significant_difference |
| extract | docpull | firecrawl | 32 | -25.0% | 0.0215 | 1.0000 | no_significant_difference |
| extract | docpull | parallel | 32 | -25.0% | 0.0215 | 1.0000 | no_significant_difference |
| extract | docpull | tavily | 32 | -21.9% | 0.0391 | 1.0000 | no_significant_difference |
| extract | exa-full | firecrawl | 32 | -3.1% | 1.0000 | 1.0000 | no_significant_difference |
| extract | exa-full | parallel | 32 | -3.1% | 1.0000 | 1.0000 | no_significant_difference |
| extract | exa-full | tavily | 32 | +0.0% | 1.0000 | 1.0000 | no_significant_difference |
| extract | firecrawl | parallel | 32 | +0.0% | 1.0000 | 1.0000 | no_significant_difference |
| extract | firecrawl | tavily | 32 | +3.1% | 1.0000 | 1.0000 | no_significant_difference |
| extract | parallel | tavily | 32 | +3.1% | 1.0000 | 1.0000 | no_significant_difference |
