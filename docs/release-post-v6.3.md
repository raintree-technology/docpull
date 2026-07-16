# DocPull 6.3: faster local evidence workflows and a complete product surface

DocPull 6.3 preserves the 6.2 evidence/workflow contracts while reducing the
cost of using them. Public Python exports now load lazily, identical concurrent
GETs share one in-flight request, resumable frontiers use a compact journal,
and pack intelligence workflows reuse source, citation, entity, and graph
indexes instead of rereading the same artifacts.

This release also completes the common workflow boundary for core acquisition
and knowledge workflows. `fetch`, `crawl`, and `dataset-pack` now emit the same
run-scoped `WorkflowResult` envelope as pack builders, including current-run
manifests, typed retryable failures, budgets, hashes, and replay settings.
Remote HTTPS JSON and CSV datasets retain their original URL, query parameters,
snapshot hash, and provenance.

The new generic `relationship-pack` emits evidence-backed review candidates for
`owned_by`, `operated_by`, `acquired_by`, `franchised_by`, and `invested_in`.
Every input receives exactly one coverage result. Missing evidence is a
`coverage_gap`, never a negative ownership or independence claim, and all
candidates remain observations until a downstream human approves them.

The repository now includes a production-ready DocPull site with pricing,
privacy, terms, machine-readable `llms.txt`, metadata, robots, sitemap, icons,
and reusable launch assets. The complete Python, MCP, and web surface is covered
by locked dependencies, automatic CI/security triggers, lint, type checks,
audits, tests, and reproducible release builds.

No migration is required for existing packs or consumers. CLI, Python SDK,
MCP, JSON Schema, `intelligence.bundle.v1`, change-event, and
`company_brain.bundle.json` compatibility remains intact. Strict CLI exit
behavior is now explicitly run-scoped; consumers that intentionally accept
partial current-run output can opt into `--exit-policy usable-output`.
