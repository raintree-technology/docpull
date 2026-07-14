# live-neutral-crawl comparison

Suite version: `1.0.0`  
Suite SHA-256: `86955db1e0e681def7926a36c4e72af5b2be3205f8caba7a3bdc67e9f7b42582`

Rows are separated by capability lane. Accounted cost can combine provider-reported actual cost and documented upper bounds; the cost-kind columns make that distinction explicit.

A pass requires every declared deterministic check, not merely a non-empty response.

## Overall

| Lane | System | Cases | Trials | Complete | Pass all (95% CI) | Macro family | Stability | Quality | Median / p95 sec | Accounted USD | USD / pass |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| crawl | docpull | 8 | 24 | 100.0% | 87.5% (52.9%–97.8%) | 87.5% | 100.0% | 98.6% | 2.589 / 7.140 | $0.000000 | $0.000000 |
| crawl | tavily-crawl-basic | 8 | 24 | 37.5% | 0.0% (0.0%–32.4%) | 0.0% | 100.0% | 19.4% | 0.497 / 3.004 | $0.576000 | n/a |
| crawl | tavily-crawl-guided-advanced | 8 | 24 | 0.0% | 0.0% (0.0%–32.4%) | 0.0% | 100.0% | 0.0% | 1.172 / 8.067 | $1.152000 | n/a |

## Split and family slices

| Lane | Slice | System | Cases | Trials | Complete | Pass all | Quality | Mean seconds |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| crawl | split:dev | docpull | 4 | 12 | 100.0% | 75.0% | 97.2% | 2.428 |
| crawl | split:dev | tavily-crawl-basic | 4 | 12 | 25.0% | 0.0% | 13.9% | 0.950 |
| crawl | split:dev | tavily-crawl-guided-advanced | 4 | 12 | 0.0% | 0.0% | 0.0% | 1.702 |
| crawl | split:test | docpull | 4 | 12 | 100.0% | 100.0% | 100.0% | 3.530 |
| crawl | split:test | tavily-crawl-basic | 4 | 12 | 50.0% | 0.0% | 25.0% | 4.755 |
| crawl | split:test | tavily-crawl-guided-advanced | 4 | 12 | 0.0% | 0.0% | 0.0% | 3.276 |
| crawl | family:framework-tutorial-crawl | docpull | 4 | 12 | 100.0% | 100.0% | 100.0% | 2.159 |
| crawl | family:framework-tutorial-crawl | tavily-crawl-basic | 4 | 12 | 25.0% | 0.0% | 13.0% | 4.483 |
| crawl | family:framework-tutorial-crawl | tavily-crawl-guided-advanced | 4 | 12 | 0.0% | 0.0% | 0.0% | 2.328 |
| crawl | family:language-reference-crawl | docpull | 4 | 12 | 100.0% | 75.0% | 97.2% | 3.799 |
| crawl | family:language-reference-crawl | tavily-crawl-basic | 4 | 12 | 50.0% | 0.0% | 25.9% | 1.222 |
| crawl | family:language-reference-crawl | tavily-crawl-guided-advanced | 4 | 12 | 0.0% | 0.0% | 0.0% | 2.650 |

## Per-case results

| Case | Split | Family | System | Complete | Passed | Quality | Mean seconds | Accounted USD |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| `crawl.docker-get-started` | dev | framework-tutorial-crawl | docpull | 3/3 | 3/3 | 100.0% | 1.723 | $0.000000 |
| `crawl.docker-get-started` | dev | framework-tutorial-crawl | tavily-crawl-basic | 0/3 | 0/3 | 0.0% | 0.496 | $0.072000 |
| `crawl.docker-get-started` | dev | framework-tutorial-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 0.498 | $0.144000 |
| `crawl.fastapi-tutorial` | test | framework-tutorial-crawl | docpull | 3/3 | 3/3 | 100.0% | 1.454 | $0.000000 |
| `crawl.fastapi-tutorial` | test | framework-tutorial-crawl | tavily-crawl-basic | 0/3 | 0/3 | 0.0% | 0.495 | $0.072000 |
| `crawl.fastapi-tutorial` | test | framework-tutorial-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 0.527 | $0.144000 |
| `crawl.go-docs` | test | language-reference-crawl | docpull | 3/3 | 3/3 | 100.0% | 7.206 | $0.000000 |
| `crawl.go-docs` | test | language-reference-crawl | tavily-crawl-basic | 3/3 | 0/3 | 48.1% | 1.585 | $0.072000 |
| `crawl.go-docs` | test | language-reference-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 4.290 | $0.144000 |
| `crawl.kubernetes-concepts` | test | framework-tutorial-crawl | docpull | 3/3 | 3/3 | 100.0% | 2.900 | $0.000000 |
| `crawl.kubernetes-concepts` | test | framework-tutorial-crawl | tavily-crawl-basic | 3/3 | 0/3 | 51.9% | 16.496 | $0.072000 |
| `crawl.kubernetes-concepts` | test | framework-tutorial-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 7.725 | $0.144000 |
| `crawl.mdn-http` | dev | language-reference-crawl | docpull | 3/3 | 3/3 | 100.0% | 2.559 | $0.000000 |
| `crawl.mdn-http` | dev | language-reference-crawl | tavily-crawl-basic | 0/3 | 0/3 | 0.0% | 0.478 | $0.072000 |
| `crawl.mdn-http` | dev | language-reference-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 1.178 | $0.144000 |
| `crawl.python-asyncio` | dev | language-reference-crawl | docpull | 3/3 | 3/3 | 100.0% | 1.542 | $0.000000 |
| `crawl.python-asyncio` | dev | language-reference-crawl | tavily-crawl-basic | 0/3 | 0/3 | 0.0% | 0.493 | $0.072000 |
| `crawl.python-asyncio` | dev | language-reference-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 1.164 | $0.144000 |
| `crawl.rust-book` | test | framework-tutorial-crawl | docpull | 3/3 | 3/3 | 100.0% | 2.559 | $0.000000 |
| `crawl.rust-book` | test | framework-tutorial-crawl | tavily-crawl-basic | 0/3 | 0/3 | 0.0% | 0.445 | $0.072000 |
| `crawl.rust-book` | test | framework-tutorial-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 0.563 | $0.144000 |
| `crawl.sqlite-language` | dev | language-reference-crawl | docpull | 3/3 | 0/3 | 88.9% | 3.888 | $0.000000 |
| `crawl.sqlite-language` | dev | language-reference-crawl | tavily-crawl-basic | 3/3 | 0/3 | 55.6% | 2.332 | $0.072000 |
| `crawl.sqlite-language` | dev | language-reference-crawl | tavily-crawl-guided-advanced | 0/3 | 0/3 | 0.0% | 3.969 | $0.144000 |

## Paired head-to-head results

McNemar's exact test uses only discordant case outcomes. A significant verdict requires `p < 0.05`; absence of significance is not evidence of equivalence.

| Lane | Slice | A | B | Cases | Both | A only | B only | Neither | Delta | p | Verdict |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| crawl | overall:all | docpull | tavily-crawl-basic | 8 | 0 | 7 | 0 | 1 | +87.5% | 0.0156 | a_better |
| crawl | overall:all | docpull | tavily-crawl-guided-advanced | 8 | 0 | 7 | 0 | 1 | +87.5% | 0.0156 | a_better |
| crawl | overall:all | tavily-crawl-basic | tavily-crawl-guided-advanced | 8 | 0 | 0 | 0 | 8 | +0.0% | 1.0000 | no_significant_difference |

No cross-lane rank or single winner is computed.
