# Claim readiness: NOT READY

Suite: `docpull-live-search`
Suite SHA-256: `13acf083e57757ed0e012f5df12e9bde7a4ba785503efe70b1ce036e30736910`
Policy: `public-comparative-claim-v1`

| Gate | Status | Detail |
| --- | --- | --- |
| reports.minimum_systems | pass | observed=4 minimum=2 |
| reports.unique_systems | pass | systems=exa-search,firecrawl-search,parallel-search,tavily-search |
| reports.suite_hash | pass | expected=13acf083e57757ed0e012f5df12e9bde7a4ba785503efe70b1ce036e30736910 |
| reports.protocol_hash | pass | distinct=1 |
| coverage.exa-search | pass | observed=30 expected=30 |
| trials.exa-search | FAIL | observed=2 minimum=5 |
| operations.exa-search | pass | observed=1.000 minimum=0.950 |
| repository_clean.exa-search | FAIL | git_dirty=True |
| coverage.firecrawl-search | pass | observed=30 expected=30 |
| trials.firecrawl-search | FAIL | observed=2 minimum=5 |
| operations.firecrawl-search | pass | observed=0.983 minimum=0.950 |
| repository_clean.firecrawl-search | FAIL | git_dirty=True |
| coverage.parallel-search | pass | observed=30 expected=30 |
| trials.parallel-search | FAIL | observed=2 minimum=5 |
| operations.parallel-search | pass | observed=1.000 minimum=0.950 |
| repository_clean.parallel-search | FAIL | git_dirty=True |
| coverage.tavily-search | pass | observed=30 expected=30 |
| trials.tavily-search | FAIL | observed=2 minimum=5 |
| operations.tavily-search | pass | observed=1.000 minimum=0.950 |
| repository_clean.tavily-search | FAIL | git_dirty=True |
| paired_information.search.exa-search.firecrawl-search | FAIL | discordant=10 minimum=20 |
| paired_information.search.exa-search.parallel-search | FAIL | discordant=5 minimum=20 |
| paired_information.search.exa-search.tavily-search | FAIL | discordant=6 minimum=20 |
| paired_information.search.firecrawl-search.parallel-search | FAIL | discordant=11 minimum=20 |
| paired_information.search.firecrawl-search.tavily-search | FAIL | discordant=6 minimum=20 |
| paired_information.search.parallel-search.tavily-search | FAIL | discordant=7 minimum=20 |
| sample_size.search | FAIL | observed=30 minimum=100 |
| holdout_size.search | FAIL | observed=15 minimum=30 |
| families.search | pass | observed=5 minimum=5 |
| family_balance.search | pass | largest_share=0.200 maximum=0.250 |
| unique_inputs.search | pass | observed=1.000 minimum=0.950 |
| domain_diversity.search | pass | observed=25 minimum=10 |
| gold.freshness | pass | stale_cases=0 |
| holdout.sealed | FAIL | valid unopened never-published seal required |
| gold.independent_review | FAIL | reviewers=0 organizations=0 minimum=2 |
| protocol.exa-search | FAIL | matching signed first-party protocol attestation required |
| protocol.firecrawl-search | FAIL | matching signed first-party protocol attestation required |
| protocol.parallel-search | FAIL | matching signed first-party protocol attestation required |
| protocol.tavily-search | FAIL | matching signed first-party protocol attestation required |
| billing.exa-search | FAIL | signed provider total required |
| billing.firecrawl-search | FAIL | signed provider total required |
| billing.parallel-search | FAIL | signed provider total required |
| billing.tavily-search | FAIL | signed provider total required |

A passing gate permits human review of a lane-local claim; it does not generate or approve claim language.
