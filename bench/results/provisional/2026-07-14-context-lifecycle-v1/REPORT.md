# context-lifecycle 1.0.0

System: `docpull` `6.0.1`  
Result: **10/10 (100.0%)**

This is a controlled, network-free artifact-lifecycle benchmark. It is not a hosted-provider leaderboard; another system can enter by producing and operating on the same published contract.

| Check | Category | Result | Seconds | Evidence |
| --- | --- | --- | ---: | --- |
| `raw-contract` | contract | pass | 0.216 | records=3, required_artifacts=3, status=pass |
| `eval-grade-contract` | provenance | pass | 0.445 | citation_entries=3, required_artifacts=13, status=pass |
| `stable-identities` | reproducibility | pass | 0.223 | stable_citations=3, stable_documents=3 |
| `exact-diff` | diff | pass | 0.226 | added=1, changed=1, removed=1, unchanged=1 |
| `offline-cited-search` | offline | pass | 0.228 | network=disabled, result_count=1, top_record_citation=S3.1 |
| `agent-exports` | export | pass | 0.483 | network=disabled, skill_files=35, vector_records=3 |
| `context-ci` | ci | pass | 0.222 | gate_count=11, network=disabled, status=None, warning_count=1 |
| `lockfile-drift` | policy | pass | 0.932 | drift_rejected=True, initial_lock=True, network=disabled |
| `credential-non-persistence` | policy | pass | 0.673 | environment_reference=True, network=disabled, secret_persisted=False |
| `zero-budget-block` | policy | pass | 0.232 | blocked=True, budget_usd=0.0, rendered_pages=0 |

All fixture content is synthetic and redistribution-safe. Reports contain no fixture bodies, credentials, or temporary filesystem paths.
