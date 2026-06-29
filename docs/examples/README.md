# CLI Recipes

Use these recipes after the 30-second README example when you need a specific
output format, crawl policy, agent skill, or context-pack workflow.

These examples use the current `docpull` CLI. The old multi-source YAML runner
and options such as `--sources-file`, `language`, `keep_variant`, TOON output,
and `create_index` are not part of the current CLI surface.

## Single Page

```bash
docpull https://www.python.org/blogs/ --single -o ./web/python-news
```

## Small Crawl

```bash
docpull https://www.python.org/blogs/ --max-pages 50 --max-depth 2 -o ./web/python-news
```

## LLM Chunks

```bash
docpull https://www.python.org/blogs/ \
  --profile llm \
  --stream \
  | jq .
```

## Selective Crawl

```bash
docpull https://example.com \
  --include-paths "/blog/*" "/resources/*" \
  --exclude-paths "/tags/*" "/author/*" \
  -o ./web/example-resources
```

## Incremental Mirror

```bash
docpull https://example.com \
  --profile mirror \
  --cache \
  --cache-dir .docpull-cache \
  -o ./web/example-mirror
```

## Zero-Dollar Runs

```bash
docpull https://example.com/docs --budget 0 -o ./web/example-docs
docpull https://example.com/docs --budget 0 --explain-route
docpull discover scan https://example.com/docs -o ./packs/example-discovery
docpull providers context-pack "Find official docs" --provider all --dry-run --budget 0 --json
docpull benchmark quick --zero-dollar --target-set zero-dollar --provider all
```

`--budget 0` allows local cache, direct HTTP, sitemap/static-link discovery,
local extraction, local indexing, local pack intelligence, and local
`agent-browser` rendering. It blocks live Tavily, Exa, Parallel, Vercel
Sandbox, and E2B calls before execution and records non-secret accounting in
`run.accounting.json` when an artifact directory is involved.

`--target-set zero-dollar` is the Phase 2 measurement matrix. It includes the
current docs/provider targets plus JS-heavy docs, pricing pages, filings, feeds,
sitemaps, and search-to-evidence tasks, then classifies each target by the
lowest-cost route that appears viable.

`docpull discover scan URL` is the Phase 3 local discovery producer. It reads
provider-free site hints such as `llms.txt`, RSS/Atom feeds, OpenAPI specs,
sitemap indexes, and public GitHub docs trees, then writes the standard
`candidate_sources.ndjson` pack for `discover select` or `discover fetch`.

When a target is partial, the zero-dollar benchmark adds Phase 4 escalation
suggestions to `benchmark.report.json` and `benchmark.summary.md`. The order is:
retry trusted-target local rendering with `--render fallback`, improve local
discovery with `docpull discover scan`, review a BYOK provider dry run with
estimated requests/cost, and reserve cloud rendering for local infrastructure
gaps.

## Output Formats

```bash
docpull https://www.python.org/blogs/ --format markdown -o ./out/markdown
docpull https://www.python.org/blogs/ --format json -o ./out/json
docpull https://www.python.org/blogs/ --format ndjson -o ./out/ndjson
docpull https://www.python.org/blogs/ --format sqlite -o ./out/sqlite
docpull https://www.python.org/blogs/ --format okf -o ./out/okf
```

OKF means Open Knowledge Format: a portable Markdown bundle with generated
indexes, manifests, hashes, metadata, and source-preserving concept files.

## Agent Skills and Rules

```bash
docpull https://sdk.vercel.ai \
  --skill vercel-ai \
  --skill-agent all \
  --skill-description "Vercel AI SDK source reference"
```

This creates agent-ready context from the same scrape:

- Claude Code: `.claude/skills/vercel-ai/SKILL.md`
- Codex: `.agents/skills/vercel-ai/SKILL.md` plus `agents/openai.yaml`
- Cursor: `.cursor/rules/vercel-ai.mdc`

Scraped pages are stored under the generated skill's `references/` directory.
With explicit `--skill-agent` targets, the shared corpus defaults to
`.docpull/skills/vercel-ai/references`, while Claude Code, Codex, and Cursor
wrappers point at that corpus. With `--output-dir ./out`, DocPull stages the
corpus at `./out/vercel-ai` and still writes the requested active wrappers.

## Parallel Context Pack

```bash
docpull parallel demo --output-dir ./packs/demo
```

From a source checkout, you can import the checked-in fixture directly:

```bash
docpull parallel import ./docs/examples/parallel-search-extract.json --output-dir ./packs/demo
```

Live Parallel workflows use your own API key:

```bash
pip install 'docpull[parallel]'
docpull parallel init
docpull parallel auth --json

# CI can still use a normal environment secret.
export PARALLEL_API_KEY="<your-parallel-api-key>"

docpull parallel context-pack "Compare AI web-search APIs for agents" \
  --query "AI web search API" \
  --query "agent web extraction API" \
  --include-domain parallel.ai \
  --exclude-domain onparallel.com \
  --output-dir ./packs/ai-web-search \
  --max-estimated-cost 0.05
```

Tavily and Exa use the shared provider adapter layer. They can be run through
`docpull providers ...` or through first-class provider aliases:

```bash
docpull providers init tavily
docpull providers init exa
docpull providers auth --json --require-ready --redact-paths
docpull providers probe --provider tavily --provider exa --json --require-verified --redact-paths
docpull providers capabilities

docpull tavily context-pack "Find current Tavily API docs" \
  --query "Tavily Search Extract API docs" \
  --include-domain docs.tavily.com \
  --output-dir ./packs/tavily-docs

docpull exa context-pack "Find current Exa API docs" \
  --query "Exa Search Contents API docs" \
  --include-domain docs.exa.ai \
  --output-dir ./packs/exa-docs

docpull exa extract-pack https://docs.exa.ai/reference/search \
  --objective "Extract the Exa Search API reference" \
  --output-dir ./packs/exa-search-reference

docpull tavily map-pack https://docs.tavily.com \
  --instructions "Find Search, Extract, Map, Crawl, and Research API reference pages" \
  --include-domain docs.tavily.com \
  --output-dir ./packs/tavily-map
```

Provider setup is intentionally equal: `auth`, `probe`, `init`,
`capabilities`, `context-pack`, and `extract-pack` use the same provider layer.
`auth` is offline and deterministic; `probe` is explicit live validation.
Tavily safe probes use the account usage endpoint, Exa safe probes use the
public team-info endpoint, and Parallel safe probes report configured local
readiness because Parallel does not expose a documented zero-cost data API key
probe. Use `docpull parallel probe --mode validation --json` for an opt-in
auth-gate check, or `--mode smoke --max-estimated-cost 0.01` for a real
minimal Search call. Advanced provider APIs are still exposed only when they
have a clean DocPull artifact shape; today Tavily Map writes a discovery pack,
while Tavily Crawl/Research and Exa Agent/Monitors are listed as planned
capabilities.

For agents and CI, prefer
`docpull <provider> auth --json --require-ready --redact-paths` before live
runs. Add `docpull <provider> probe --json --require-verified --redact-paths`
only when network validation is intended, then use `--dry-run --json` to
inspect the planned request and output path before spending credits.

## Local-First Pack Workflows

```bash
docpull policy validate ./docpull.policy.yml
docpull policy explain ./docpull.policy.yml
docpull discover scan https://docs.example.com --source all -o ./packs/site-discovery
docpull discover urls ./urls.txt --include-domain docs.example.com -o ./packs/discovery
docpull discover import ./provider-response.json --provider exa -o ./packs/provider-discovery
docpull discover sitemap ./sitemap.xml --base-url https://docs.example.com -o ./packs/sitemap-discovery
docpull discover select ./packs/discovery --select top:10 -o ./packs/selected
docpull discover fetch ./packs/discovery --select top:10 -o ./packs/fetched
docpull map urls ./urls.txt -o ./packs/map
docpull extract-pack ./urls.txt -o ./packs/extract
docpull crawl-pack ./packs/map --select top:10 -o ./packs/crawl
docpull refresh ./packs/current --dry-run
docpull refresh ./packs/current -o ./packs/current-refresh
docpull refresh ./packs/current --markdown ./packs/current/refresh.report.md
docpull pack audit ./packs/current-refresh --markdown ./packs/current-refresh/PACK_AUDIT.md
docpull answer-pack ./packs/current-refresh "What changed in the API docs?"
docpull research-pack ./packs/current-refresh \
  --objective "What changed in the API docs?" \
  --stream-events
docpull entities-pack ./packs/current-refresh --limit 100
docpull export ./packs/current-refresh --format openai-vector-jsonl -o ./openai.jsonl
docpull export ./packs/current-refresh --format sheets-csv -o ./sheets.csv
docpull export ./packs/current-refresh --format sheets-tsv -o ./sheets.tsv
docpull export ./packs/current-refresh --format n8n-json -o ./n8n.workflow.json
docpull export ./packs/current-refresh --format vercel-ai-json -o ./vercel-ai.json
docpull export ./packs/current-refresh --format crewai-json -o ./crewai.json
docpull export ./packs/current-refresh --format warehouse-ndjson -o ./warehouse.ndjson
docpull export ./packs/current-refresh --format parquet -o ./warehouse.parquet
docpull serve ./packs/current-refresh --host 127.0.0.1 --port 8765
docpull share ./packs/current-refresh/research.report.md
docpull share ./packs/current-refresh/PACK_AUDIT.md --open
```

Parquet export is optional; install `docpull[parquet]` or `pyarrow` before
using `--format parquet`.

`docpull share` serves one Markdown, HTML, or plain text report over loopback
HTTP and prints the URL. Non-localhost binds require `--allow-network-bind`.

Optional rendering remains explicit:

```bash
docpull render --check
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull https://example.com/app --single --render fallback -o ./packs/rendered
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render https://example.com/app -o ./rendered
docpull render --check --runtime vercel
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render https://example.com/app \
  --runtime vercel \
  --cloud-max-estimated-cost 0.20 \
  -o ./rendered-vercel
docpull render --check --runtime e2b
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render https://example.com/app \
  --runtime e2b \
  --cloud-result-transport file \
  --cloud-max-estimated-cost 0.20 \
  -o ./rendered-e2b

# Explicit provider smoke checks; these may consume cloud quota.
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render --live-smoke --runtime vercel --cloud-max-estimated-cost 0.20
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render --live-smoke --runtime e2b --cloud-max-estimated-cost 0.20

# Faster E2B cold starts when you have a template with agent-browser installed.
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render https://example.com/app \
  --runtime e2b \
  --template docpull-agent-browser \
  --cloud-agent-browser-install skip \
  -o ./rendered-e2b
```

Rendering requires an external `agent-browser` compatible executable. Put it on
`PATH`, set `DOCPULL_AGENT_BROWSER_BIN`, or pass `docpull render --agent-browser-bin`.
Set `DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1` only for trusted public render
targets; the browser backend cannot enforce redirect or subresource allow-lists.
Cloud rendering is opt-in: Vercel Sandbox requires the `sandbox` CLI plus Vercel
auth, and E2B requires `pip install 'docpull[e2b]'` plus `E2B_API_KEY`.
All cloud runtimes run `agent-browser --json`; use `docpull render init e2b` or
`docpull render init vercel` for template recipes.
Provider live tests are gated in the test suite; run them only when credentials
are configured:

```bash
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 DOCPULL_LIVE_CLOUD_RENDER=1 .venv/bin/python -m pytest tests/test_rendering.py -q
```

Authenticated source checks do not write fetched content:

```bash
docpull auth check https://docs.example.com/private \
  --auth-policy explicit-private \
  --auth-bearer "$DOCS_TOKEN" \
  --json
```

Local monitors are scheduler-friendly; run them from cron, launchd, or CI:

```bash
docpull monitor --state-dir .docpull/monitors init ./packs/current \
  --name vendor-docs \
  --schedule "0 */6 * * *" \
  --policy ./docpull.policy.yml
docpull monitor --state-dir .docpull/monitors run vendor-docs --once --dry-run --json
docpull monitor --state-dir .docpull/monitors run vendor-docs --once \
  --github-issue-file ./packs/vendor-docs-issue.md
docpull monitor --state-dir .docpull/monitors trigger vendor-docs --dry-run --json
docpull monitor --state-dir .docpull/monitors pause vendor-docs
docpull monitor --state-dir .docpull/monitors unpause vendor-docs
docpull monitor --state-dir .docpull/monitors scheduler-snippet vendor-docs --kind cron
docpull monitor --state-dir .docpull/monitors list --json
docpull monitor --state-dir .docpull/monitors report vendor-docs --json
```

`--slack-webhook` records that a webhook was supplied for the monitor run; it
does not post to Slack directly. Use the JSON or GitHub issue file output with
your scheduler/notification runner.

Preview a live workflow without spending credits:

```bash
docpull parallel context-pack "Compare AI web-search APIs for agents" \
  --query "AI web search API" \
  --query "agent web extraction API" \
  --include-domain parallel.ai \
  --fetch-max-age-seconds 3600 \
  --excerpt-chars-per-result 5000 \
  --dry-run
```

Build other Parallel-backed artifacts:

```bash
docpull parallel search-pack "Parallel Search API docs" \
  --query "Parallel Search API" \
  --include-domain docs.parallel.ai \
  --dry-run

docpull parallel discover-docs "Find canonical Parallel API docs" \
  --query "Parallel Search API docs" \
  --include-domain docs.parallel.ai \
  --crawl-profile mirror \
  --dry-run

docpull parallel extract-pack https://docs.parallel.ai/api-reference/search/search \
  --objective "Extract the Parallel Search API reference" \
  --dry-run

docpull parallel fallback-pack https://docs.parallel.ai/api-reference/search/search \
  --profile rag \
  --dry-run

docpull parallel task-pack "Research Parallel Search API changes" \
  --source-include-domain docs.parallel.ai \
  --output-schema-json '{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"]}' \
  --dry-run

docpull parallel entity-pack "AI developer infrastructure companies" \
  --entity-type companies \
  --match-limit 25 \
  --dry-run

docpull parallel findall-pack "AI developer infrastructure companies" \
  --condition "devtool=Company must sell developer infrastructure" \
  --generator preview \
  --match-limit 5 \
  --dry-run

docpull parallel taskgroup-pack ./companies.json \
  --prompt-template "Research {company}" \
  --processor lite \
  --wait \
  --dry-run

docpull parallel monitor-pack create "New vendor pricing changes" --dry-run
docpull parallel monitor-pack create --type snapshot --task-run-id run_123 --dry-run
docpull parallel monitor-pack list --status active --output-dir ./packs/monitors
docpull parallel monitor-pack events monitor_123 --cursor next_cursor --output-dir ./packs/monitor-events
docpull parallel api-pack https://docs.parallel.ai/llms.txt --output-dir ./packs/parallel-docs-index
```

Ready-made API-pack recipes are checked in:

```bash
docpull parallel run ./docs/examples/parallel-llms-api-pack.yaml --dry-run
docpull parallel run ./docs/examples/parallel-openapi-api-pack.yaml --dry-run
```

## SEC Filing Evidence Pack

Prepare filing rows as NDJSON with `primary_document_url` or `url`, then run:

```bash
docpull evidence-pack ./filings.ndjson \
  --profile sec-filing \
  --rules ./docs/examples/vendor-dependency-rules.yml \
  --sec-user-agent "YourOrg your-email@example.com" \
  --output-dir ./packs/dla-vendors
```

The checked-in `vendor-dependency-rules.yml` profile covers government
customer, customer concentration, segment revenue, and related-party signals.

Inspect packs locally:

```bash
docpull pack score ./packs/demo
docpull pack sources ./packs/demo --require-domain docs.parallel.ai
docpull pack citations ./packs/demo --markdown ./packs/demo/CITATIONS.md
docpull pack entities ./packs/demo --markdown ./packs/demo/ENTITIES.md
docpull pack search ./packs/demo "authentication webhooks" --markdown ./packs/demo/SEARCH.md
docpull pack brief ./packs/demo --objective "Summarize the API surface"
docpull pack prepare ./packs/demo --objective "Summarize the API surface"
docpull graph build ./packs/demo
docpull graph query ./packs/demo "authentication webhooks"
docpull graph neighbors ./packs/demo "Search API"
docpull pack diff ./packs/old ./packs/new --markdown ./packs/changes.md
docpull parallel diff-brief ./packs/old ./packs/new --dry-run
```

`docpull pack prepare` is the one-command local post-processor. It writes
`pack.score.json`, `source.scores.json`, `citations.json`, `entities.json`,
`pack.search.json` / `pack.searches.json`, `SEARCH.md`,
`research.brief.json`, `RESEARCH_BRIEF.md`, `graph.json`,
`graph.nodes.ndjson`, `graph.edges.ndjson`, `GRAPH.md`, and
`pack.prepare.json` without a provider account.

`docpull graph build` writes `graph.json`, `graph.nodes.ndjson`,
`graph.edges.ndjson`, and `GRAPH.md` from the same local pack evidence. It is a
cited source graph for inspecting entity/source relationships; it does not call
a hosted graph service or generate natural-language answers.

Run `docpull --help` for the full option list.
