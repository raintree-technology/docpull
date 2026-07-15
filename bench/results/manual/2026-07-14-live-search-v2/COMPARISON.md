# docpull-live-search comparison

Suite: `13acf083e57757ed0e012f5df12e9bde7a4ba785503efe70b1ce036e30736910`
Protocol: `daf74d9c3b6f1649c1beae1fef8e7e81ad6ea5c406a257619247996932698552`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Trial pass | pass@k | pass^k | Stability | Checks | p50/p95 s | Cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| search | exa-search | 30 | 83.3% | 83.3% | 83.3% | 100.0% | 95.8% | 0.497/1.424 | $0.420000 |
| search | firecrawl-search | 30 | 58.3% | 60.0% | 56.7% | 96.7% | 85.8% | 1.658/2.720 | $0.720000 |
| search | parallel-search | 30 | 81.7% | 83.3% | 80.0% | 96.7% | 95.4% | 2.462/4.995 | $0.300000 |
| search | tavily-search | 30 | 70.0% | 70.0% | 70.0% | 100.0% | 91.7% | 2.202/4.813 | $0.960000 |

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

| Lane | A | B | Cases | Delta | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| search | exa-search | firecrawl-search | 30 | +26.7% | 0.0215 | 1.0000 | no_significant_difference |
| search | exa-search | parallel-search | 30 | +3.3% | 1.0000 | 1.0000 | no_significant_difference |
| search | exa-search | tavily-search | 30 | +13.3% | 0.2188 | 1.0000 | no_significant_difference |
| search | firecrawl-search | parallel-search | 30 | -23.3% | 0.0654 | 1.0000 | no_significant_difference |
| search | firecrawl-search | tavily-search | 30 | -13.3% | 0.2188 | 1.0000 | no_significant_difference |
| search | parallel-search | tavily-search | 30 | +10.0% | 0.4531 | 1.0000 | no_significant_difference |
