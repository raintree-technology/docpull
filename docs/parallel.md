# Parallel Integration

This page maps Parallel products to docpull's optional context-pack workflows.
The goal is to make Parallel web intelligence outputs durable, local,
inspectable, and easy for agents to reuse.

Parallel sources checked on 2026-06-08. Workflow coverage last reviewed for
docpull 4.4.0.

- https://docs.parallel.ai/llms.txt
- https://docs.parallel.ai/public-openapi.json
- https://docs.parallel.ai/api-reference/search/search
- https://docs.parallel.ai/search/search-quickstart
- https://docs.parallel.ai/search/modes
- https://docs.parallel.ai/extract/extract-quickstart
- https://docs.parallel.ai/task-api/task-quickstart
- https://docs.parallel.ai/integrations/mcp/quickstart

## API Key Flow

Live Parallel workflows are bring-your-own-key. docpull can read the key from
`PARALLEL_API_KEY`, a project `.env.local`, or the user-level
`~/.config/docpull/secrets.env` file created by `docpull parallel init`. It
passes the key to the Parallel SDK for live API calls and never echoes the
secret or writes it into generated pack artifacts.

```bash
pip install 'docpull[parallel]'

# Recommended local setup. Prompts securely and stores the key with 0600 permissions.
docpull parallel init

# Project-local setup for agent worktrees. Writes .env.local and updates .gitignore.
docpull parallel init --project

# CI can still use a normal environment secret.
export PARALLEL_API_KEY="<your-parallel-api-key>"

# Agent/CI-friendly local configuration check. The key value is not included in JSON.
docpull parallel auth --json
```

Start new workflows with a dry run and an explicit cost guard:

```bash
docpull parallel context-pack "Build a Parallel docs context pack" \
  --query "Parallel Search API docs" \
  --include-domain docs.parallel.ai \
  --extract-limit 3 \
  --max-estimated-cost 0.05 \
  --dry-run
```

Once the plan looks right, remove `--dry-run`. The generated pack records the
source policy, request options, usage metadata, selected URLs, and local cost
estimate, but not `PARALLEL_API_KEY`.
`docpull parallel auth` reports the key source (`env`, `project_env`,
`user_config`, or `missing`) but does not make a live Parallel call or prove the
key is valid; live workflows still fail fast if Parallel rejects the configured
key.

## Implemented Workflows

| Parallel product | Parallel shape | docpull surface | Why it fits |
| --- | --- | --- | --- |
| Search API | Natural-language objective plus 2-3 keyword queries returns ranked excerpts. Modes are `turbo`, `basic`, and `advanced`; `advanced` is the default. | `docpull parallel context-pack ...`, `search-pack`, or `discover-docs` | Discovers candidate URLs, writes source scores and crawl commands, and keeps Search ID, session ID, source policy, usage, warnings, and errors in pack metadata. |
| Extract API | Known URLs become excerpts and optional full Markdown content. Up to 20 URLs per request. Same `session_id` can be reused after Search. | `context-pack` extracts selected Search URLs; `extract-pack URL ...` extracts known URLs; `fallback-pack URL ...` tries core docpull first and calls Parallel only for misses. | Converts web intelligence into docpull records: `documents.ndjson`, `corpus.manifest.json`, source Markdown files, `sources.md`, and `AGENT_CONTEXT.md`. |
| Task API | Asynchronous research and enrichment with output schemas, citations, reasoning, events, previous interactions, MCP servers, and webhooks. | `--task-brief`, `task-pack`, `task-result`, `task-events`, and `diff-brief` | Produces optional cited briefs, structured Task result packs, event snapshots, and pack-change summaries while preserving run IDs, usage, basis metadata, and non-secret request metadata. |
| Task Groups | Batch Task execution over many independent inputs. | `docpull parallel taskgroup-pack ./rows.ndjson --wait` | Creates a group, adds runs, polls group status until inactive, then snapshots inputs/outputs into a pack. |
| FindAll | Entity discovery, ingest, schema, enrichment, extension, cancellation, events, and result snapshots. | `findall-pack` plus `findall-ingest-pack/result-pack/schema-pack/enrich-pack/extend-pack/cancel-pack/events-pack` | Preserves candidate records, inferred schemas, enrichment specs, run status, event pages, and lifecycle action metadata. |
| Monitor API | Event-stream and snapshot monitors with event retrieval and lifecycle actions. | `docpull parallel monitor-pack create/list/retrieve/update/cancel/trigger/events ...` | Saves monitor metadata, event pages, event-group summaries, webhooks, source policy, location, metadata, and lifecycle action results as local packs. |
| API docs/spec packs | `llms.txt` indexes and OpenAPI specs. | `docpull parallel api-pack ...` and `docpull parallel run docs/examples/parallel-*.yaml` | Builds durable API context packs without a Parallel account. |
| MCP tools | Agent-facing MCP calls for pack creation and pack inspection. | `parallel_context_pack`, `parallel_api_pack`, `pack_score`, `pack_diff`, `pack_citations`, `pack_entities`, `pack_search`, `pack_brief`, `pack_prepare` | Lets MCP-aware clients build, inspect, search, and prepare packs without shelling out. The CLI also adds `pack sources` for deterministic local source ranking. |
| Offline/demo fixtures | Saved Search, Extract, and Task-shaped JSON can be replayed locally. | `docpull parallel import fixture.json` and `docpull parallel demo` | Makes the integration testable, demoable, and usable without a Parallel account. The demo fixture is packaged in the wheel. |

## Not Implemented Yet

| Parallel product | Product fit | Planned docpull workflow |
| --- | --- | --- |
| Chat API | OpenAI-compatible live web chat and JSON responses. | Out of scope because it is an interactive answer surface, not a context-pack source. |
| Data integrations | BigQuery, Snowflake, DuckDB, Spark, Polars, Supabase, Sheets. | Out of scope unless docpull later emits warehouse-friendly pack manifests or imports table rows as source candidates. |

## Additional Pack Workflows

These workflows turn the rest of Parallel's product surface into local artifacts
instead of one-off API responses:

```bash
# Search-only result snapshots.
docpull parallel search-pack "Parallel Search API docs" \
  --query "Parallel Search API" \
  --include-domain docs.parallel.ai \
  --output-dir ./packs/parallel-search

# Search-seeded docs discovery with next-step core docpull crawl commands.
docpull parallel discover-docs "Find canonical Parallel API docs" \
  --query "Parallel Search API docs" \
  --include-domain docs.parallel.ai \
  --crawl-profile mirror \
  --output-dir ./packs/parallel-discovery

# Extract known URLs directly.
docpull parallel extract-pack https://docs.parallel.ai/api-reference/search/search \
  --objective "Extract the Parallel Search API reference" \
  --output-dir ./packs/parallel-search-reference

# Try core docpull first, then use Parallel Extract only for URLs docpull cannot fetch.
docpull parallel fallback-pack https://docs.parallel.ai/api-reference/search/search \
  --profile rag \
  --output-dir ./packs/parallel-fallback

# Single Task run with a structured output schema.
docpull parallel task-pack "Summarize Parallel Search API request controls" \
  --source-include-domain docs.parallel.ai \
  --output-schema-json '{"type":"object","properties":{"summary":{"type":"string"}},"required":["summary"]}' \
  --output-dir ./packs/parallel-task

docpull parallel task-result run_123 --output-dir ./packs/parallel-task-result
docpull parallel task-events run_123 --limit 50 --output-dir ./packs/parallel-task-events

# Summarize a refreshed context pack diff with Parallel Task.
docpull parallel diff-brief ./packs/old ./packs/new \
  --output-dir ./packs/diff-brief \
  --max-estimated-cost 0.05

# Fast people/company candidate dossiers.
docpull parallel entity-pack "AI developer infrastructure companies" \
  --entity-type companies \
  --match-limit 25 \
  --output-dir ./packs/entities \
  --max-estimated-cost 0.01

# Larger verified entity discovery. Preview is the default cost-bounded generator.
docpull parallel findall-pack "AI developer infrastructure companies" \
  --condition "devtool=Company must sell developer infrastructure" \
  --generator preview \
  --match-limit 5 \
  --wait \
  --output-dir ./packs/findall \
  --max-estimated-cost 0.10

docpull parallel findall-ingest-pack "Find AI companies with public API docs" \
  --output-dir ./packs/findall-ingest

docpull parallel findall-enrich-pack findall_123 \
  --output-schema ./schema.json \
  --output-dir ./packs/findall-enrich

docpull parallel findall-result-pack findall_123 --output-dir ./packs/findall-result
docpull parallel findall-schema-pack findall_123 --output-dir ./packs/findall-schema
docpull parallel findall-extend-pack findall_123 --additional-match-limit 10
docpull parallel findall-events-pack findall_123 --limit 100
docpull parallel findall-cancel-pack findall_123

# Batch research over JSON or NDJSON rows. `--wait` polls until the group is inactive.
docpull parallel taskgroup-pack ./companies.json \
  --prompt-template "Research {company} for agent infrastructure relevance" \
  --processor lite \
  --wait \
  --poll-interval 10 \
  --timeout 900 \
  --output-dir ./packs/research-batch \
  --max-estimated-cost 0.05

# Always-on monitoring, then event snapshots as packs.
docpull parallel monitor-pack create "New Parallel Web Systems product releases" \
  --frequency 1d \
  --processor lite \
  --include-domain docs.parallel.ai \
  --location us \
  --output-dir ./packs/parallel-monitor

docpull parallel monitor-pack create \
  --type snapshot \
  --task-run-id run_123 \
  --frequency 1d \
  --output-dir ./packs/parallel-snapshot-monitor

docpull parallel monitor-pack events monitor_123 \
  --limit 20 \
  --cursor next_cursor \
  --output-dir ./packs/parallel-monitor-events

docpull parallel monitor-pack list --status active --output-dir ./packs/parallel-monitors
docpull parallel monitor-pack retrieve monitor_123 --output-dir ./packs/parallel-monitor
docpull parallel monitor-pack update monitor_123 --frequency 6h --output-dir ./packs/parallel-monitor
docpull parallel monitor-pack trigger monitor_123 --output-dir ./packs/parallel-monitor-trigger
docpull parallel monitor-pack cancel monitor_123 --output-dir ./packs/parallel-monitor-cancel

# API docs/spec packs from llms.txt or OpenAPI.
docpull parallel api-pack https://docs.parallel.ai/llms.txt \
  --output-dir ./packs/parallel-docs-index

docpull parallel api-pack https://docs.parallel.ai/public-openapi.json \
  --output-dir ./packs/parallel-openapi

docpull parallel run ./docs/examples/parallel-llms-api-pack.yaml
docpull parallel run ./docs/examples/parallel-openapi-api-pack.yaml
```

Most live Parallel creation and enrichment workflows support `--dry-run`,
conservative cost guards, or both. Monitor event snapshots fetch an existing
monitor's event list and use `--limit` to bound output size. The asynchronous
workflows (`findall-pack`, `taskgroup-pack`) avoid waiting by default; pass
`--wait` only when you want docpull to fetch completed results. `docpull
parallel run` supports the same pack workflows through YAML/JSON recipes, with
relative file paths resolved from the recipe location.

## MCP and CLI Boundary

Parallel's MCP servers are a separate adoption path:

- Search MCP is free for exploration and light use without an API key.
- Task MCP requires auth and is useful for deep research and enrichment in MCP-aware clients.
- Parallel CLI is a direct user/agent tool for searching, extracting, enriching, and monitoring.

docpull intentionally uses the Python SDK with `docpull[parallel]` and a
bring-your-own-key model. It does not proxy requests or install Parallel MCP
servers. When users opt in with `docpull parallel init`, the key is stored in
local machine/project configuration, never in generated packs. That keeps the
package suitable for open source release while making the outputs durable enough
for agents, RAG, audits, and demos.

## Controls That Make Packs Useful

The live Search result set is only as useful as its source policy. For human
review and agent reuse, prefer explicit controls:

```bash
docpull parallel context-pack "Track Parallel Web Systems API docs" \
  --query "Parallel Search API docs" \
  --query "Parallel Extract API docs" \
  --include-domain parallel.ai \
  --include-domain docs.parallel.ai \
  --exclude-domain onparallel.com \
  --mode turbo \
  --extract-limit 3 \
  --max-estimated-cost 0.05 \
  --dry-run
```

Useful controls:

- `--dry-run` prints the request plan and estimated cost without spending credits.
- `--max-estimated-cost` stops live calls before they exceed a local cost budget.
- `--include-domain`, `--exclude-domain`, and `--after-date` make source selection auditable.
- `--max-search-results`, `--extract-limit`, and `--no-full-content` control pack size and latency.
- `--extract-limit` is capped at 20 URLs to match Parallel Extract's per-request limit.
- `--fetch-max-age-seconds`, `--fetch-timeout-seconds`, and `--disable-cache-fallback` expose Parallel fetch policy.
- `--excerpt-chars-per-result` controls Search/Extract excerpt size.
- `--location` passes a country code to geo-target Search results.
- `--client-model` lets Parallel tailor excerpts for the model consuming the pack.

## Pack Contract

Every successful context pack writes:

- `AGENT_CONTEXT.md` - agent load plan with source order, source scores, pack signals, warnings, and artifact map.
- `documents.ndjson` - chunked `DocumentRecord` JSON lines for agents and RAG.
- `corpus.manifest.json` - existing docpull corpus manifest.
- `parallel.pack.json` - Parallel workflow metadata, selected URLs, IDs, errors, usage, and Task basis if returned.
- `parallel.pack.json` also includes the request options, warning objects, and local cost estimate.
- `sources.md` - human-readable source index.
- `sources/*.md` - extracted Markdown for each successful URL.
- Local post-processing sidecars such as `pack.score.json`,
  `source.scores.json`, `citations.json`, `entities.json`, `SEARCH.md`,
  `research.brief.json`, `RESEARCH_BRIEF.md`, and `pack.prepare.json` when
  `docpull pack prepare` is run. Provider comparison runs write these
  automatically for successful Parallel, Tavily, and Exa packs.
- `brief.md` - only when Task output is requested or imported from a fixture.

`PARALLEL_API_KEY` is never echoed by docpull and is never persisted in pack
artifacts. `docpull parallel init` can persist it only in local configuration:
`~/.config/docpull/secrets.env` by default, or `.env.local` with `--project`.
Pack artifacts can contain source content, selected URLs, workflow metadata,
Task input/output, and other user-provided data, so treat them as research
artifacts rather than secret stores.

## Pack Inspection

Parallel packs can be scored and diffed without any Parallel account:

```bash
docpull pack score ./packs/parallel-openapi --require-domain docs.parallel.ai
docpull pack sources ./packs/parallel-openapi --require-domain docs.parallel.ai
docpull pack citations ./packs/parallel-openapi --markdown ./packs/parallel-openapi/CITATIONS.md
docpull pack entities ./packs/parallel-openapi --markdown ./packs/parallel-openapi/ENTITIES.md
docpull pack search ./packs/parallel-openapi "task webhooks" --markdown ./packs/parallel-openapi/SEARCH.md
docpull pack brief ./packs/parallel-openapi --objective "Review Parallel API docs"
docpull pack prepare ./packs/parallel-openapi --objective "Review Parallel API docs"
docpull pack diff ./packs/old ./packs/new --markdown ./packs/changes.md
docpull parallel diff-brief ./packs/old ./packs/new --dry-run
```

`score` flags empty records, extract errors, missing manifests, mismatched record
counts, missing declared artifacts/source files, duplicate content, off-domain
sources, and missing Task basis metadata. `sources` ranks source URLs with a
local docs/API/reference heuristic. `citations` rolls pack records up into a
stable URL-level source map. `entities` extracts cited local signals such as
emails, dates, money amounts, versions, organizations, and API/SDK terms.
`search` returns ranked local hits with citation IDs and query-centered
excerpts. `brief` writes `RESEARCH_BRIEF.md`, `research.brief.json`,
`citations.json`, and `entities.json` from local pack content without a
provider account. `prepare` runs the full local inspection/search/brief pipeline
and records the generated sidecars in `pack.prepare.json`. `diff` compares
record URLs and content hashes so agents can identify changed context before
loading a refreshed pack. `diff-brief` sends that diff through Parallel Task and
persists `CHANGE_SUMMARY.md` plus `pack.diff.json`.
