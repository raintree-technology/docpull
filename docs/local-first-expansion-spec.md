# Local-First Expansion Spec

This spec defines ten product expansions that keep DocPull an open-source,
local CLI/SDK/MCP tool. The boundary is intentional: DocPull should not become
a hosted search engine, captcha bypass system, browser-farm service, or global
web index. It should make web evidence local, inspectable, refreshable,
diffable, and easy for agents to use.

## Product Principles

- Keep async HTTP extraction as the default path.
- Add browser rendering only as an explicit opt-in fallback.
- Keep all generated artifacts local unless the user opts into a provider.
- Preserve provenance for every URL, rendered page, provider result, chunk, and
  transformation.
- Make every paid or hosted provider call visible, bounded, and replayable.
- Prefer file-backed contracts over hidden process state.
- Expose the same core workflows through CLI, Python SDK, and MCP when the
  workflow is useful to agents.

## Shared Contracts

New features should write sidecar files into pack directories instead of
inventing unrelated output layouts:

- `corpus.manifest.json` remains the canonical record manifest.
- `AGENT_CONTEXT.md` remains the agent loading guide.
- `source_policy.json` records domain/path/provider/auth/render constraints.
- `candidate_sources.ndjson` records discovered URLs before final fetch.
- `refresh.report.json` and `refresh.report.md` record pack refresh results.
- `pack.audit.json` and `PACK_AUDIT.md` record quality checks.
- `rendered_pages.ndjson` records browser-rendered inputs before extraction.

All new JSON/NDJSON records should include `schema_version`, `generated_at`,
`url`, `source`, and enough non-secret run metadata to reproduce the result.

## Surface Contract Alignment

These expansions follow the existing surface-contract rule: align capabilities
across CLI, Python SDK, and MCP when the workflow is core, but do not force
operator-only workflows into every surface.

Status: this document is a product spec and parity backlog. The current
implemented local-first slice covers explicit rendering, local discovery pack
normalization/selection/fetch, refresh/diff/audit/answer/export/serve/share workflows,
policy validation/explanation, authenticated-source checks, and cron-friendly
local monitor runs. Provider-neutral `extract-pack`, `map`, `crawl-pack`,
`research-pack`, and `entities-pack` are implemented as local parity workflows;
zero-dollar benchmarks emit escalation suggestions with commands, estimated
paid request counts, and estimated paid costs. Global web search, hosted
research agents, proprietary entity indexes, and hosted scheduler/webhook
delivery remain provider-backed or out of scope.

| Expansion | CLI | Python SDK/API | MCP | Contract class |
| --- | --- | --- | --- | --- |
| Optional local rendering | `docpull <url> --render ...`, `docpull render ...` | Renderer protocol and fetch config | `render_url`; `fetch_url` stays browser-free | Core-aligned |
| Provider-neutral discovery packs | `docpull discover import/urls/sitemap/select/fetch` | Discovery adapters and record models | `discover_sources`; `fetch_discovered_sources` selects candidates | Core-aligned |
| Local refresh and diff | `docpull refresh`, `docpull pack diff` | Pack refresh/diff helpers | `refresh_pack`, `pack_diff` | Core-aligned |
| Local pack server and report sharing | `docpull serve`, `docpull share` | ASGI app factory; report HTTP server factory | Optional status/introspection only | Adapted |
| Stronger MCP tools | Existing and new tool schemas | Shared helper modules | Primary surface | MCP-focused |
| Better exports | `docpull export` | Exporter protocol | `export_pack` for agent-safe formats | Adapted |
| Pack quality scoring | `docpull pack audit` | Audit helper module | `audit_pack` | Core-aligned |
| Policy files | `docpull policy ...`, discovery `--policy` | Typed `PolicyConfig` | `validate_policy` | Core-aligned |
| Authenticated source mode | `--auth-policy`, `docpull auth check` | Auth policy config/helpers | Limited validation/fetch support | Adapted |
| Local monitors | `docpull monitor ...` | Monitor config/run helpers | Optional `refresh_pack` composition | CLI/operator-focused |

If this spec is implemented, `docs/surface-contract.md` must be updated in the
same change set as each delivered feature.

## Escalation UX

Partial local capture should produce a concrete ladder, not vague advice:

- Local discovery gaps: suggest `docpull discover scan`, URL files, sitemap
  imports, and `discover fetch` before paid providers.
- JS-rendered public pages: suggest trusted-target local `--render fallback`
  before cloud rendering.
- Search-to-evidence gaps: suggest a BYOK provider dry run first, with provider
  names, estimated paid request count, and estimated cost guard.
- Cloud rendering: suggest only after local rendering or local infrastructure is
  insufficient, and include the per-render cost guard.
- Policy blocks: suggest policy/robots/source-boundary review instead of paid
  escalation.

The benchmark zero-dollar report is the first implemented UX: it writes
`escalation_suggestions` into `benchmark.report.json` and renders those commands
in `benchmark.summary.md`.

## Hosted Product Boundary

OSS DocPull keeps local fetching, local rendering adapter support, discovery
adapters, extraction, indexing, packs, diffs, monitors, MCP, BYOK providers,
budget policy, accounting, and benchmarks.

Hosted DocPull, if offered, sells managed execution and operations: always-on
schedules, browser/proxy infrastructure, persistent auth profiles, queues,
alerts, dashboards, collaboration, retention, SSO, audit logs, SLAs, and
bundled provider billing. Hosted positioning must keep the OSS boundary honest:
no CAPTCHA bypass, no stealth scraping, no automatic paid calls, and no
proprietary web-scale-index claim.

## Official Competitor Parity Targets

This section translates official Tavily, Parallel, and Exa docs into DocPull
parity targets. "Parity" means matching the user workflow and artifact shape as
closely as a local open-source CLI/SDK/MCP tool can. When a competitor feature
depends on a proprietary hosted index, hosted scheduler, or hosted research
agent, DocPull should either wrap it as an optional provider-backed workflow or
offer a local approximation with explicit limits.

Official docs reviewed for this matrix:

- Tavily: [Welcome](https://docs.tavily.com/welcome),
  [API introduction](https://docs.tavily.com/documentation/api-reference/introduction),
  [Search](https://docs.tavily.com/documentation/api-reference/endpoint/search),
  [Extract](https://docs.tavily.com/documentation/api-reference/endpoint/extract),
  [Crawl](https://docs.tavily.com/documentation/api-reference/endpoint/crawl),
  [Map](https://docs.tavily.com/documentation/api-reference/endpoint/map),
  [Research](https://docs.tavily.com/documentation/api-reference/endpoint/research),
  and [MCP](https://docs.tavily.com/documentation/mcp).
- Parallel: [Overview](https://docs.parallel.ai/getting-started/overview),
  [Search](https://docs.parallel.ai/api-reference/search/search),
  [Extract](https://docs.parallel.ai/api-reference/extract/extract),
  [Task](https://docs.parallel.ai/task-api/task-quickstart),
  [FindAll](https://docs.parallel.ai/findall-api/findall-quickstart),
  [Entity Search](https://docs.parallel.ai/findall-api/entity-search), and
  [Monitor](https://docs.parallel.ai/monitor-api/monitor-quickstart).
- Exa: [Search agent reference](https://exa.ai/docs/reference/search-api-guide-for-coding-agents),
  [Contents agent reference](https://exa.ai/docs/reference/contents-api-guide-for-coding-agents),
  [Agent](https://exa.ai/docs/reference/agent-api-guide),
  [Monitors](https://exa.ai/docs/reference/monitors-api-guide),
  [Context / Exa Code](https://exa.ai/docs/reference/context), and
  [People vertical](https://exa.ai/docs/reference/verticals/people-for-coding-agents).

| Competitor workflow | Official shape | DocPull parity target | Local-first limit |
| --- | --- | --- | --- |
| Web search | Tavily `/search`; Parallel `/v1/search`; Exa `/search` | Current local discovery: `docpull discover scan` writes ranked `candidate_sources.ndjson` from `llms.txt`, RSS/Atom feeds, OpenAPI specs, sitemap indexes, and public GitHub docs trees. Provider-backed top-level search should use the same discovery-pack contract when live search is explicitly selected. | Native DocPull cannot search a global web index. Use provider-backed discovery or local pack/site indexes. |
| Search tuning | Tavily `search_depth`, topics, date filters, domains, country; Parallel mode and excerpt budget; Exa search `type`, category, domains, dates, location | Add provider-neutral fields: `mode`, `topic`, `category`, `time_range`, `date_range`, `include_domains`, `exclude_domains`, `location`, `max_results`, `excerpt_budget`, `freshness`. Preserve provider-specific raw options under `provider_options`. | Some fields are provider-specific; unsupported options must fail validation or be recorded as ignored, not silently dropped. |
| Extract / contents | Tavily `/extract`; Parallel `/v1/extract`; Exa `/contents` | Current: provider-neutral `docpull extract-pack` accepts known URL files and emits Markdown sources, `documents.ndjson`, manifest, source policy, per-URL errors/skips, lifecycle artifacts, and `sources.md`. Parallel-specific `docpull parallel extract-pack` exists for provider-backed extraction. | Base DocPull extracts static/SSR HTML; JS/PDF/complex layout parity requires optional `agent-browser` or provider-backed extraction. |
| Crawl / map | Tavily `/crawl` and `/map` with instructions, depth, breadth, limits, include/exclude rules; Exa Contents supports subpage crawling; Parallel has crawler/source-policy resources | Current: `docpull map` outputs URL-only `candidate_sources.ndjson` from URL files or sitemaps, and `docpull crawl-pack` selects candidates and fetches local pack artifacts. | Natural-language crawl instructions require provider support or a local heuristic layer; native DocPull remains deterministic and policy-bound. |
| Research / task / agent | Tavily `/research`; Parallel Task; Exa Agent and deep search | Current: `docpull research-pack` produces `research.report.md`, `research.result.json`, citations, basis excerpts, local structured output validation, and lifecycle artifacts from existing pack evidence. Parallel-specific task packs remain under `docpull parallel`. | Local provider-free research can summarize existing pack content only; web-scale multi-hop research needs provider-backed workflows. |
| Structured output | Tavily `output_schema`; Parallel Task output schemas and basis; Exa `outputSchema` and structured output | Current: `docpull research-pack --schema schema.json` validates a dependency-free JSON Schema subset against locally grounded answer fields and reports validation errors. | Local validation can enforce shape, but cannot invent provider-grade field-level grounding without cited evidence. |
| Streaming and async lifecycle | Tavily research SSE/status; Parallel Task/FindAll/Monitor events and webhooks; Exa Agent SSE/polling, Monitor webhooks | Current: parity workflows write `events.ndjson`, `status.json`, `webhook.sample.json`, and `poll.report.json`; `research-pack` and `entities-pack` accept `--wait`, `--timeout`, `--poll-interval`, and `--stream-events` compatibility flags. | DocPull does not host public webhook receivers. Local workflows generate sample webhook payloads and single-poll reports unless a provider workflow supplies remote lifecycle state. |
| Entity/list building | Parallel FindAll and Entity Search; Exa Agent/Websets/people/company categories | Current: `docpull entities-pack` writes entity/list artifacts, citations, lifecycle state, and local evidence basis over existing pack content. | Native DocPull can extract entities from existing packs, not discover verified people/company datasets at web scale. |
| Vertical indexes | Exa category filters for people, company, research paper, news, personal site, financial report; Exa Context over code/docs/repos | Add vertical-aware discovery modes: `--category people|company|paper|news|code|financial-report|docs`. For local packs, implement category labels and specialized scoring. For provider workflows, pass through category-specific constraints and capture limitations. | Local DocPull cannot replicate proprietary vertical indexes; provider-backed only for broad people/company/code/news search. |
| Answer generation | Tavily search answer/research report; Exa Answer/deep/Agent text; Parallel Task reports | `docpull answer-pack` should answer from a local pack or provider result with citations, refusal when evidence is insufficient, and a reproducible prompt/config record. | Provider-free answers are limited to local pack evidence and should not claim live-web completeness. |
| Freshness / live crawl | Tavily date filters; Exa `maxAgeHours` and livecrawl fallback; Parallel fetch/source policies | Represent freshness uniformly in `source_policy.json`: max age, force-live, cache-allowed, date filters, refresh time, and stale-source warnings. | Native freshness is based on refetching selected URLs, not global recrawl freshness. |
| Monitors | Parallel Monitor and Exa Monitors schedule recurring searches and webhook updates; Tavily has research/status flows but not the same monitor API | Current `docpull monitor` supports config, run-once refresh/diff/audit/report, list, report, dry run, GitHub issue-file output, pause/unpause, manual trigger aliases, dedupe labels, and cron/launchd/GitHub Actions scheduler snippets. Direct Slack posting remains out of scope; `--slack-webhook` records intent without persisting the URL. | No hosted scheduler or always-on cloud watcher. User runs cron/launchd/GitHub Actions or another scheduler. |
| MCP | Tavily and Exa expose remote MCP; Parallel has MCP tools and docs | DocPull MCP exposes safe local equivalents for render, discovery-pack creation/selection, extract/map/crawl/research/entities packs, refresh, audit, answer, pack search/read/cite/export/status, plus provider-backed Parallel context/API packs. | Remote hosted MCP is out of scope unless separately deployed; default remains local stdio MCP. |
| SDK and integrations | Tavily Python/JS SDK and many integrations; Parallel Python/TS SDK, CLI, MCP, platform/data integrations; Exa Python/JS SDK, MCP, LangChain/CrewAI/LlamaIndex/OpenAI-compatible paths | Current: Python SDK parity, local MCP, and `docpull export` formats for OpenAI vector JSONL, LangChain, LlamaIndex, DSPy, CrewAI, Vercel AI SDK JSON, n8n workflow JSON, Google Sheets CSV/TSV, warehouse NDJSON, optional Parquet, and agent skill/rule references. | Do not build every hosted integration first-party. Emit stable files and examples that integration ecosystems can consume. |
| Usage, cost, attribution | Tavily usage credits, project/session headers; Parallel usage/cost metadata and source policy; Exa cost breakdown | Every provider-backed pack must capture non-secret request IDs, session IDs, project labels, usage, estimated cost, actual cost when returned, provider status, warnings, and rate-limit metadata. | Native DocPull has no hosted billing; local runs should record wall time, byte counts, page counts, and skipped/fetched counts. |

## Parity Backlog

The local-first expansion layer now implements the CLI/SDK/MCP workflows below
where a local tool can do so honestly. Remaining backlog items are provider-
backed global search/research/entity discovery, proprietary vertical indexes,
and hosted delivery behaviors. The target order for future provider-backed work
is:

1. Provider-neutral request/result contracts for search, extract, crawl/map,
   research, entities, and monitor runs.
2. Policy files that cover source policy, freshness, cost, provider options,
   render behavior, auth, redaction, and output schema.
3. Provider-backed `discover`, `extract-pack`, `map`, `crawl-pack`,
   `research-pack`, and `entities-pack` commands that write the shared
   artifacts when hosted indexes or agents are selected.
4. Hosted push integrations beyond local file exports, such as writing directly
   into SaaS accounts or managed warehouse destinations.
5. MCP parity tools over any new provider-backed modules.
6. Optional `agent-browser` rendering for local extraction parity where provider
   extraction is not desired.
7. Local monitors that approximate hosted monitors with cron-friendly state,
   dedupe, trigger, pause/unpause, and webhook/file outputs.

## 1. Optional Local Rendering

### Goal

Handle public JavaScript-rendered pages when useful content is not available in
static HTML, without changing the default browser-free posture.

### Proposed Surface

```bash
docpull URL --render off
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull URL --render agent-browser
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull URL --render fallback --render-runtime local
docpull render --check
docpull render doctor
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render URL --runtime local --output-dir ./rendered
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render URL --runtime local --agent-browser-bin /path/to/agent-browser
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render URL --runtime vercel --output-dir ./rendered-vercel
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render URL --runtime e2b --template docpull-agent-browser --output-dir ./rendered-e2b
```

Python SDK:

```python
from docpull import fetch_one

ctx = fetch_one("https://example.com", render="agent-browser")
```

MCP:

- `render_url(url, runtime="local"|"vercel"|"e2b", allowed_domains=[...])`
- `fetch_url` stays browser-free; use `render_url` for explicit local browser
  rendering.

### Implementation

- Add `RenderConfig` with `backend`, `timeout_seconds`, `wait_for`,
  `allowed_domains`, `action_policy`, `viewport`, and `max_html_bytes`.
- Add a `Renderer` protocol returning rendered HTML plus diagnostics.
- Implement `AgentBrowserRenderer` by shelling out to `agent-browser --json`.
- Treat `agent-browser` as an optional external executable, not a base package
  dependency. Check it with `docpull render --check`, `docpull --doctor`, or the
  SDK `check_agent_browser_availability()`.
- Implement `VercelSandboxRenderer` as an optional cloud microVM backend through
  the Vercel `sandbox` CLI. It requires Vercel auth and is checked with
  `docpull render --check --runtime vercel`.
- Implement `E2BSandboxRenderer` as an optional cloud microVM backend through
  the E2B Python SDK. It requires `docpull[e2b]` or `pip install e2b` plus
  `E2B_API_KEY`.
- Use `agent-browser get html html` after navigation and load wait.
- Cloud renderers run the same `agent-browser --json` command inside the
  sandbox and return only the rendered HTML payload plus diagnostics. They write
  a sandbox-local result artifact and use E2B file transport when available
  instead of relying only on stdout.
- Add cloud debt controls: `--live-smoke` for explicit real-provider checks,
  `--cloud-max-estimated-cost` for local per-render budget caps,
  `--cloud-agent-browser-install skip` for prebuilt templates, and `--template`
  / `DOCPULL_E2B_TEMPLATE` for reusable E2B sandbox environments.
- Store rendered HTML only when the selected output mode needs durable source
  snapshots; otherwise store hashes, byte counts, and diagnostics in
  `rendered_pages.ndjson`.
- Pass rendered HTML through the existing conversion, chunking, save, and
  manifest pipeline.
- Record render metadata in `DocumentRecord.metadata.render`.

### Safety

- Default `--render off`.
- Require explicit allowed domains; derive a narrow default from the start URL.
- Require HTTPS render URLs except localhost/loopback HTTP for local tests.
- Deny `eval`, upload, download, clipboard, broad profile reuse, and arbitrary
  proxy use by enforcing the default restrictive action policy.
- Never solve captchas, bypass bot challenges, or enable stealth behavior.
- Mark rendered records as `rendered=true` in metadata.

### Tests

- Unit test renderer command construction.
- Stub `agent-browser` JSON responses for deterministic pipeline tests.
- Verify rendered HTML enters the same extractor path as fetched HTML.
- Verify blocked domains and missing binary produce actionable errors.
- Add a live backend smoke that skips unless the `agent-browser` executable is
  installed.
- Verify browser-free behavior is unchanged when `--render` is omitted.

## 2. Provider-Neutral Discovery Packs

### Goal

Normalize discovery outputs from Parallel, Tavily, Exa, Brave, local sitemaps,
and future providers into one candidate-source contract.

### Proposed Surface

```bash
docpull discover import ./provider-response.json --provider exa \
  --include-domain docs.example.com \
  --output-dir ./packs/discovery

docpull discover scan https://docs.example.com --source all -o ./packs/site-discovery
docpull discover scan https://github.com/owner/repo --source github -o ./packs/github-docs
docpull discover urls ./urls.txt --include-domain docs.example.com
docpull discover sitemap ./sitemap.xml --base-url https://docs.example.com
docpull discover select ./packs/discovery --select top:10 -o ./packs/selected
docpull discover fetch ./packs/discovery --select top:10 -o ./pack
```

MCP:

- `discover_sources(urls, objective?, query?, include_domains?, output_dir?)`
- `fetch_discovered_sources(discovery_pack_dir, selectors?)` selects candidates
  and writes selection artifacts; CLI/operator workflows do the actual fetch.

### Implementation

- Define `CandidateSourceRecord`:
  `url`, `title`, `snippet`, `provider`, `score`, `rank`, `query`,
  `discovered_at`, `raw_ref`, `metadata`.
- Add provider adapters that only produce candidate records.
- Keep provider-specific pack builders as wrappers over this contract.
- Add selection policies: `top:N`, `domain:N`, `score>=X`, `manual-file`.
- Write `candidate_sources.ndjson`, `source_policy.json`, and
  `DISCOVERY.md`.

### Safety

- Provider calls require explicit opt-in and configured keys.
- Dry runs should show planned providers, queries, domains, and estimated cost.
- No provider key may appear in artifacts.

### Tests

- Golden fixtures for Parallel, Tavily, and Exa normalization.
- Provider absence and missing-key tests.
- Selection-policy tests.
- Round-trip test from discovery pack to normal DocPull pack.

## 3. Local Refresh And Diff

### Goal

Refresh an existing pack and produce clear change reports without needing a
hosted monitor.

### Proposed Surface

```bash
docpull refresh ./pack
docpull refresh ./pack --changed-only
docpull refresh ./pack --dry-run
docpull refresh ./pack --markdown ./pack/refresh.report.md
docpull pack diff ./old-pack ./new-pack --markdown ./changes.md
```

MCP:

- `refresh_pack(pack_dir, changed_only=false)`
- `diff_pack(old_pack_dir, new_pack_dir)`

### Implementation

- Read URLs and crawl options from `corpus.manifest.json` plus
  `source_policy.json`.
- Re-fetch with conditional GET where possible.
- Preserve old and new content hashes.
- Write `refresh.report.json`, `refresh.report.md`, and optional
  `changes.ndjson`.
- Reuse existing pack diff logic and expand it to include title/path changes.

### Safety

- Do not refresh private/authenticated packs unless the original auth policy is
  explicit and current credentials are supplied.
- Respect original domain/path allowlists.
- Never silently broaden the crawl.

### Tests

- Local server tests for unchanged, changed, removed, and newly discovered URLs.
- Manifest compatibility tests.
- Markdown report golden tests.
- MCP refresh tool tests with temporary pack fixtures.

## 4. Local Pack Server and Report Sharing

### Goal

Expose generated packs over a localhost-only HTTP API so local apps, agents,
and scripts can search, read, and cite pack content. Expose generated Markdown
or HTML reports over a simple local URL for human review.

### Proposed Surface

```bash
docpull serve ./pack --host 127.0.0.1 --port 8765
docpull serve ./pack --readonly
docpull share ./pack/research.report.md
docpull share ./pack/PACK_AUDIT.md --open
```

Pack server endpoints:

- `GET /health`
- `GET /manifest`
- `GET /documents?limit=...`
- `GET /documents/{document_id}`
- `GET /search?q=...`
- `GET /citations`
- `GET /sources`

Report share endpoints:

- `GET /`
- `GET /report`
- `GET /health`
- `GET /source`

Python SDK:

```python
from docpull.server import create_pack_app
from docpull.share import create_report_server, render_report_document
```

### Implementation

- Build a small ASGI app over pack files.
- Use SQLite FTS when available; fallback to NDJSON scan for small packs.
- Return JSON only; leave HTML UI out of scope for the first release.
- Bind to `127.0.0.1` by default.
- Document this as a local development API, not a hosted DocPull API. If a
  hosted API is ever added, it needs a separate contract.

### Safety

- Refuse non-localhost bind unless `--allow-network-bind` is supplied.
- Read-only by default.
- Do not expose secret-bearing source policy fields.

### Tests

- ASGI route tests.
- FTS and NDJSON fallback search tests.
- Non-localhost bind rejection test.
- Large pack pagination tests.

## 5. Stronger MCP Tools

### Goal

Make the MCP server the primary agent surface for refresh, discovery, rendering,
pack search, citations, and local briefs.

### Proposed Tools

- `refresh_pack`
- `discover_sources`
- `fetch_discovered_sources`
- `render_url`
- `serve_pack_status`
- `audit_pack`
- `export_pack`
- `validate_policy`

### Implementation

- Keep tool outputs both human-readable and structured.
- Use existing pack helpers rather than duplicating logic in MCP handlers.
- Run slow workflows in worker threads and forward progress notifications.
- Add tool schemas to `src/docpull/mcp/server.py` and logic in reusable modules.

### Safety

- Rendering and provider tools must surface dry-run options.
- Tool arguments must require domain constraints for risky workflows.
- Structured output must omit secrets.

### Tests

- Tool-list contract tests.
- MCP call tests for each new tool.
- Progress-notification tests for long-running refresh/discovery workflows.
- Validation tests for unsafe arguments.

## 6. Better Exports

### Goal

Let users move DocPull packs into common agent and RAG ecosystems without
hand-written conversion scripts.

### Proposed Surface

```bash
docpull export ./pack --format openai-vector-jsonl -o ./openai.jsonl
docpull export ./pack --format langchain-jsonl -o ./langchain.jsonl
docpull export ./pack --format llamaindex-jsonl -o ./llamaindex.jsonl
docpull export ./pack --format dspy-jsonl -o ./dspy.jsonl
docpull export ./pack --format sheets-csv -o ./sheets.csv
docpull export ./pack --format sheets-tsv -o ./sheets.tsv
docpull export ./pack --format n8n-json -o ./n8n.workflow.json
docpull export ./pack --format vercel-ai-json -o ./vercel-ai.json
docpull export ./pack --format crewai-json -o ./crewai.json
docpull export ./pack --format warehouse-ndjson -o ./warehouse.ndjson
docpull export ./pack --format parquet -o ./warehouse.parquet
docpull export ./pack --format codex-skill -o ./skills/example
docpull export ./pack --format claude-skill -o ./.claude/skills/example
docpull export ./pack --format cursor-rules -o ./.cursor/rules/example.mdc
```

Python SDK:

```python
from docpull.exports import export_pack
```

### Implementation

- Add exporter protocol with deterministic input from `DocumentRecord`.
- Preserve source URL, title, document ID, chunk ID, content hash, and citation
  metadata in every export format that can carry metadata.
- Add file-native downstream exporters for spreadsheet rows, workflow JSON,
  agent-framework JSON, warehouse NDJSON, and optional Parquet.
- Reuse existing skill export code for agent-specific exports.

### Safety

- Refuse exports that would drop provenance unless `--allow-provenance-drop` is
  supplied.
- Never include auth headers, cookies, provider keys, or raw request metadata.

### Tests

- Golden JSONL tests for each export.
- Provenance retention tests.
- Agent skill/rule structure tests.

## 7. Pack Quality Scoring

### Goal

Expand pack scoring into an actionable audit report for agents and humans.

### Proposed Surface

```bash
docpull pack audit ./pack
docpull pack audit ./pack --markdown PACK_AUDIT.md
docpull pack audit ./pack --fail-under 0.85
```

MCP:

- `audit_pack(pack_dir, required_domains, fail_under)`

### Audit Dimensions

- Source diversity.
- Freshness.
- Duplicate rate.
- Citation coverage.
- Chunk size distribution.
- Broken links or fetch skips.
- JS-skip rate.
- Robots and security skips.
- Provider cost estimate.
- Required-domain coverage.

### Implementation

- Extend existing pack scoring with weighted dimensions.
- Write `pack.audit.json` and optional `PACK_AUDIT.md`.
- Keep scoring deterministic and explain every penalty.

### Safety

- Audit must not trigger network calls unless `--check-links-live` is supplied.
- Live checks must respect source policy.

### Tests

- Golden audit reports.
- Threshold exit-code tests.
- No-network-by-default tests.
- Edge cases: empty packs, single-source packs, duplicate-heavy packs.

## 8. Policy Files

### Goal

Give teams a reusable, reviewable crawl and provider policy that controls
domains, paths, auth, rendering, cost, freshness, redaction, and output shape.

### Proposed Surface

```bash
docpull policy validate docpull.policy.yml
docpull policy explain docpull.policy.yml
docpull discover scan https://docs.example.com --policy docpull.policy.yml
docpull discover urls ./urls.txt --policy docpull.policy.yml
```

Example:

```yaml
schema_version: 1
allowed_domains:
  - docs.example.com
denied_paths:
  - /admin/*
max_pages: 200
render:
  backend: agent-browser
  mode: fallback
  timeout_seconds: 20
providers:
  max_estimated_cost_usd: 0.10
  allowed:
    - parallel
    - exa
auth:
  allow_authenticated_sources: false
redaction:
  enabled: true
  patterns:
    - name: email
      regex: "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+"
```

### Implementation

- Add typed `PolicyConfig`.
- Merge discovery CLI arguments over policy file values with a clear precedence rule.
- Persist effective policy to `source_policy.json`.
- Add policy validation and explanation commands.

### Safety

- Invalid or ambiguous policy should fail before network calls.
- Auth and provider use require explicit policy fields.
- Redaction policy must be applied before writing artifacts when enabled.

### Tests

- Policy parse and validation tests.
- CLI precedence tests.
- Effective-policy artifact tests.
- Redaction fixture tests.

## 9. Authenticated Source Mode

### Goal

Make authenticated public-ish source fetching safer and more ergonomic for
teams that need private docs, customer portals with stable document URLs, or
paid docs, while making the privacy boundary explicit. Interactive dashboards
that require browser state, clicking, or workflow execution remain out of scope
unless the user also opts into the rendering policy from section 1.

### Proposed Surface

```bash
docpull URL --auth-policy explicit-private \
  --auth-bearer "$TOKEN" \

docpull auth check URL \
  --auth-policy explicit-private \
  --auth-bearer "$TOKEN"
```

### Implementation

- Add `auth_policy` with values `none`, `explicit-private`,
  `public-token-only`.
- Backlog: add private-pack labeling in manifests and `AGENT_CONTEXT.md`.
- Backlog: add redaction hooks before save/export.
- Add `docpull auth check` to validate credentials without writing content.
- Write an audit log of auth mode, domains, redirects, and redaction status, but
  never write credential values.

### Safety

- Authenticated runs must be opt-in and visually labeled.
- No credentials in cache keys, manifests, logs, reports, or exports.
- Auth headers must still be stripped on cross-origin redirects.
- Backlog: exports from private packs require `--allow-private-export`.

### Tests

- Header secrecy tests.
- Cross-origin redirect tests.
- Backlog: private-pack export guard tests.
- Auth check tests using a local server.

## 10. Local Monitors Without Hosting

### Goal

Provide cron-friendly monitoring that writes local state and reports, without
running a cloud service.

### Proposed Surface

```bash
docpull monitor --state-dir .docpull/monitors init ./pack --name vendor-docs
docpull monitor --state-dir .docpull/monitors run vendor-docs --once
docpull monitor --state-dir .docpull/monitors run vendor-docs --once --json
docpull monitor --state-dir .docpull/monitors list
docpull monitor --state-dir .docpull/monitors report vendor-docs
```

Optional notification outputs:

```bash
docpull monitor run vendor-docs --slack-webhook "$WEBHOOK"
docpull monitor run vendor-docs --github-issue-file ./issue.md
```

### Implementation

- Store monitor configs under `.docpull/monitors/` or a user-selected state dir.
- Each monitor points at a pack path and effective policy.
- `run --once` performs refresh, diff, audit, and report generation.
- Backlog: generate shell/launchd/cron snippets and pause/unpause controls.
- `--slack-webhook` records that a webhook was supplied for the run; direct
  Slack delivery is left to the caller's scheduler/notification wrapper.

### Safety

- Monitors inherit pack source policy.
- Notification outputs must redact private pack labels and sensitive metadata.
- Webhooks are user-supplied and never persisted unless explicitly requested.

### Tests

- Monitor config lifecycle tests.
- `run --once` tests over local changing server fixtures.
- Notification file rendering tests.
- Private-pack redaction tests.

## Delivery Plan

1. Policy files and provider-neutral discovery packs.
2. Refresh/diff and expanded pack audit.
3. Stronger MCP tools over the new reusable modules.
4. Better exports.
5. Local pack server.
6. Optional agent-browser rendering.
7. Authenticated source mode.
8. Local monitor workflows.

This order builds the shared contracts first, then layers agent surfaces and
riskier browser/auth features on top of reviewed policy machinery.

## Release Gates

Each feature must ship with:

- CLI help and README recipe.
- Python SDK surface or documented reason it is CLI-only.
- MCP tool when useful for agent workflows.
- Unit tests and at least one local end-to-end fixture.
- Golden artifact tests for new file formats.
- Security tests for secrets, redirects, domain policy, and private pack labels
  when applicable.
- Changelog entry.
