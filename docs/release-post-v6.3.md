# DocPull 6.3: faster local evidence workflows and a complete product surface

DocPull 6.3 preserves the 6.2 evidence/workflow contracts while reducing the
cost of using them. Public Python exports now load lazily, identical concurrent
GETs share one in-flight request, resumable frontiers use a compact journal,
and pack intelligence workflows reuse source, citation, entity, and graph
indexes instead of rereading the same artifacts.

The repository now includes a production-ready DocPull site with pricing,
privacy, terms, machine-readable `llms.txt`, metadata, robots, sitemap, icons,
and reusable launch assets. The complete Python, MCP, and web surface is covered
by locked dependencies, automatic CI/security triggers, lint, type checks,
audits, tests, and reproducible release builds.

No migration is required for existing packs or consumers. CLI, Python SDK,
MCP, JSON Schema, `intelligence.bundle.v1`, change-event, and
`company_brain.bundle.json` compatibility remains intact.
