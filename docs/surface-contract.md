# Surface Contract

DocPull exposes the same core workflows through CLI, Python SDK, and MCP, with each surface optimized for its user.

In this project, **API** means the Python SDK / library API. DocPull does not currently ship a hosted HTTP API. If a hosted API is added later, it should get its own contract instead of being implied by the SDK contract.

Hosted execution is a product boundary, not a hidden default. The OSS surfaces
own local evidence production, BYOK provider escalation, budget/accounting
policy, and benchmarks; hosted services, if any, should be documented
separately as managed execution, schedules, browser/proxy infrastructure,
profiles, queues, alerts, dashboards, collaboration, retention, SSO, audit logs,
SLAs, or bundled provider billing.

## Surface Roles

| Surface | Role | Should optimize for |
| --- | --- | --- |
| CLI | Full human/operator workflow surface | Explicit commands, diagnostics, file outputs, provider setup, benchmark and release-adjacent workflows |
| Python SDK/API | Stable programmatic core | Typed imports, fetch/scrape/config primitives, chunking and local search helpers |
| MCP | Curated agent-safe tool surface | Structured schemas, safe fetch/cache/search/read flows, source aliases, bounded pack actions |

## Parity Classes

| Class | Meaning |
| --- | --- |
| Core-aligned | The capability should have a clear path across CLI, SDK/API, and MCP, even when names and options differ. |
| Adapted | The capability exists across more than one surface, but each surface exposes the form that fits its user. |
| Surface-specific | The capability intentionally belongs to one surface and should not be forced everywhere for symmetry. |

DocPull targets capability alignment, not 1:1 flag parity. MCP should not mirror every CLI flag, and the SDK should not grow convenience wrappers only to match MCP tool names.

## Capability Matrix

| Capability | CLI | Python SDK/API | MCP | Contract |
| --- | --- | --- | --- | --- |
| Fetch one URL | `docpull <url> --single` or default crawl entry | `fetch_one`, `fetch_blocking`, `Fetcher` | `fetch_url` | Core-aligned |
| Optional rendering | `docpull <url> --render ...`, `docpull render ... --runtime local\|vercel\|e2b`, cloud controls for live smoke, budget caps, prebuilt agent-browser templates, and E2B templates | `RenderConfig`, `Renderer`, `AgentBrowserRenderer`, `VercelSandboxRenderer`, `E2BSandboxRenderer`, `estimate_cloud_render_cost_usd`, `render_url`, `render_url_to_directory`, fetch config | `render_url` with runtime controls; `fetch_url` stays browser-free | Core-aligned |
| Crawl public web/source | `docpull <url>` with crawl/output flags | `Fetcher`, `Scraper`, config models | `ensure_docs` for named aliases | Adapted |
| Output Markdown / NDJSON / SQLite / OKF | CLI output flags | Pipeline/config primitives | Indirect through fetched Markdown and pack tools | Adapted |
| List configured sources | Not a primary CLI command | `docpull.mcp.sources` internals, not public SDK | `list_sources` | MCP-focused |
| List cached/indexed sources | Not a primary CLI command | Local filesystem/search helpers | `list_indexed` | MCP-focused |
| Search cached sources | SQLite/local search helpers where applicable | `search_sqlite_documents` | `grep_docs` | Adapted |
| Read cached source by path/range | Filesystem responsibility | Filesystem responsibility | `read_doc` | MCP-specific |
| Add/remove source aliases | Plugin/MCP workflow, not core CLI | Source internals, not public SDK | `add_source`, `remove_source` | MCP-specific |
| Refresh/score/diff/audit context packs | `docpull refresh`, `docpull pack score`, `docpull pack sources`, `docpull pack diff`, `docpull pack audit` | Pack helper modules, `refresh_pack`, `audit_pack` | `refresh_pack`, `pack_score`, `pack_diff`, `audit_pack` | Core-aligned |
| Build pack citations/entities/search/briefs | `docpull pack citations`, `docpull pack entities`, `docpull pack search`, `docpull pack brief` | `build_citation_map`, `extract_pack_entities`, `search_pack`, `build_research_brief` | `pack_citations`, `pack_entities`, `pack_search`, `pack_brief` | Core-aligned |
| Prepare full pack intelligence bundle | `docpull pack prepare` | `prepare_pack` in `docpull.pack_tools` | `pack_prepare` | Core-aligned |
| Build/query local source graphs | `docpull graph build`, `docpull graph status`, `docpull graph query`, `docpull graph neighbors`, `docpull graph refresh` | `build_graph`, `load_graph`, `graph_status`, `query_graph`, `graph_neighbors`, `refresh_graph` | `graph_build`, `graph_status`, `graph_query`, `graph_neighbors`, `graph_refresh` | Core-aligned |
| Provider-neutral discovery packs | `docpull discover scan`, `docpull discover import`, `docpull discover urls`, `docpull discover sitemap`, `docpull discover select`, `docpull discover fetch` | `CandidateSourceRecord`, `records_from_site_scan`, discovery pack helpers in `docpull.discovery` | `discover_sources` creates candidate packs; `fetch_discovered_sources` selects candidates for the CLI/operator fetch path | Core-aligned |
| Provider-neutral parity packs | `docpull extract-pack`, `docpull map`, `docpull crawl-pack`, `docpull research-pack`, `docpull entities-pack` | `extract_pack`, `map_sources`, `crawl_pack`, `research_pack`, `entities_pack`, `validate_structured_output` in `docpull.parity` | `extract_pack`, `map_sources`, `crawl_pack`, `research_pack`, `entities_pack` | Core-aligned |
| Source policy files | `docpull policy validate`, `docpull policy explain`, `--policy` on discovery commands | `PolicyConfig` in `docpull.policy` | `validate_policy` over the same typed config | Core-aligned |
| Budget/accounting policy | `--budget`, `--explain-route`, `run.accounting.json`, `docpull benchmark quick --zero-dollar --target-set zero-dollar`, policy `budget.maximum_paid_cost_usd` | `BudgetConfig`, accounting helpers, `PolicyConfig.budget`, zero-dollar benchmark classification | `budget` on paid-capable write tools | Core-aligned |
| Exports, local pack server, and report sharing | `docpull export` for JSONL, Sheets CSV/TSV, n8n JSON, Vercel AI JSON, CrewAI JSON, warehouse NDJSON, Parquet, and agent references; `docpull serve`; `docpull share` for Markdown/HTML report URLs | `export_pack`, `create_pack_app`, `create_report_server`, `render_report_document`, `load_pack` | `export_pack`, `serve_pack_status` | Adapted |
| Local answers and monitors | `docpull answer-pack`, `docpull monitor init/run/trigger/pause/unpause/list/report/scheduler-snippet` | `answer_pack`, monitor helpers | `answer_pack`; monitor runs remain CLI/operator-owned | Adapted |
| Parallel context/API packs | `docpull parallel ...` | Parallel workflow modules | `parallel_context_pack`, `parallel_api_pack` | Adapted |
| Provider-backed packs and capabilities | `docpull providers capabilities`, `docpull providers context-pack`, `docpull providers extract-pack`, `docpull tavily map-pack`, `docpull tavily ...`, `docpull exa ...` | `provider_adapters` | Not exposed | CLI/operator-specific |
| Provider auth/init/status | `docpull provider ...`, `docpull parallel init/auth` | Provider helper modules | Not exposed | CLI/operator-specific |
| Doctor diagnostics | `docpull --doctor`, `docpull render --check` | Diagnostic module, `check_agent_browser_availability` | Not exposed | CLI/operator-specific |
| Benchmarks and comparison reports | `docpull benchmark ...` | Benchmark modules | Not exposed | CLI/operator-specific |
| Evidence packs | `docpull evidence-pack ...` | Evidence pack module | Not exposed | CLI/operator-specific |

## Stability Rules

- Runtime behavior should not change only to make surfaces look symmetric.
- New core workflows should first define the durable capability, then choose the right form for each surface.
- Public docs should say "CLI, Python SDK/API, and MCP" when describing surfaces, and should not imply a hosted API.
- MCP tool additions should be agent-safe, schema-first, and narrower than the CLI unless there is a clear agent use case.
