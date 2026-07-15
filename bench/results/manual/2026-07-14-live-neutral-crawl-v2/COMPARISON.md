# live-neutral-crawl comparison

Suite: `897d2a54e8f4c3f9f5954ee3107c789a6445944d0f3049b64b455ca3d3e0ec2c`
Protocol: `fbde4f4a0782d9e8a085e7b12cba464a4bbbfdd7a0b8daded026212977c33c40`

Every pass requires all lane assertions. No cross-lane composite or winner is computed.

| Lane | System | Cases | Trial pass | pass@k | pass^k | Stability | Checks | p50/p95 s | Cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| crawl | docpull | 8 | 87.5% | 87.5% | 87.5% | 100.0% | 98.9% | 3.303/7.413 (not comparable) | $0.000000 |
| crawl | firecrawl-crawl | 8 | 0.0% | 0.0% | 0.0% | 100.0% | 18.2% | 0.375/13.509 (not comparable) | $0.768000 |
| crawl | tavily-crawl-basic | 8 | 0.0% | 0.0% | 0.0% | 100.0% | 46.0% | 0.508/24.409 (not comparable) | $0.384000 |

Paired tests use exact McNemar p-values with Holm correction. A non-significant result does not establish equivalence.

| Lane | A | B | Cases | Delta | Exact p | Holm p | Verdict |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| crawl | docpull | firecrawl-crawl | 8 | +87.5% | 0.0156 | 0.2344 | no_significant_difference |
| crawl | docpull | tavily-crawl-basic | 8 | +87.5% | 0.0156 | 0.2344 | no_significant_difference |
| crawl | firecrawl-crawl | tavily-crawl-basic | 8 | +0.0% | 1.0000 | 1.0000 | no_significant_difference |
