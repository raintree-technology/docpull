# Surface Contract

DocPull aligns core workflows across CLI, Python SDK, and MCP, with each surface optimized for its user.

In this project, **API** means the Python SDK / library API. Hosted or remote
HTTP APIs are outside the OSS release surface until separately promoted into a
public contract.

Hosted execution is a product boundary, not a hidden default. The OSS surfaces
own local evidence production, v3 pack contracts, budget/accounting policy, and
agent-ready exports. Managed execution, schedules, browser/proxy
infrastructure, profiles, queues, alerts, dashboards, collaboration, retention,
SSO, audit logs, and SLAs remain future hosted concerns, not this release
contract.

## Surface Roles

| Surface | Role | Should optimize for |
| --- | --- | --- |
| CLI | Full human/operator workflow surface | Explicit commands, diagnostics, file outputs, validation, export, and release workflows |
| Python SDK/API | Stable programmatic core | Typed imports, fetch/config primitives, chunking, pack loading, and local search helpers |
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
| Fetch one URL | `docpull <url> --single` or `workflow_run(fetch)` | `fetch_one`, `fetch_blocking`, `Fetcher`, `run_workflow` | `fetch_url`, `workflow_run` | Core-aligned |
| Optional rendering | `docpull <url> --render ...`, `docpull render ... --runtime local\|vercel\|e2b`, trusted-target acknowledgement, cloud controls for live smoke, budget caps, prebuilt agent-browser templates, and E2B templates | `RenderConfig`, `Renderer`, `AgentBrowserRenderer`, `VercelSandboxRenderer`, `E2BSandboxRenderer`, `estimate_cloud_render_cost_usd`, `render_url`, `render_url_to_directory`, fetch config | `render_url` with runtime controls; `fetch_url` stays browser-free | Core-aligned |
| Crawl public web/source | `docpull <url>` with crawl/output flags and run-scoped result | `Fetcher`, config models, `run_workflow(crawl)` | `ensure_docs`, `workflow_run` | Core-aligned |
| Output Markdown / NDJSON / SQLite / OKF | CLI output flags | Pipeline/config primitives | Indirect through fetched Markdown and pack tools | Adapted |
| List configured sources | Not a primary CLI command | `docpull.mcp.sources` internals, not public SDK | `list_sources` | MCP-focused |
| List cached/indexed sources | Not a primary CLI command | Local filesystem/search helpers | `list_indexed` | MCP-focused |
| Project lifecycle | `docpull init`, `add`, `install`, `deps`, `sources`, `sync`, `diff`, `status`, `history`, `review`, `release context-pack`, `watch` with explicit crawl bounds | `docpull.project` helpers | Not exposed yet | Core-aligned |
| Search cached sources | SQLite/local search helpers where applicable | `search_sqlite_documents` | `grep_docs` | Adapted |
| Read cached source by path/range | Filesystem responsibility | Filesystem responsibility | `read_doc` | MCP-specific |
| Add/remove source aliases | Plugin/MCP workflow, not core CLI | Source internals, not public SDK | `add_source`, `remove_source` | MCP-specific |
| Refresh/score/diff/audit context packs | `docpull refresh`, `docpull pack score`, `docpull pack sources`, `docpull pack diff`, `docpull pack audit` | Pack helper modules, `refresh_pack`, `audit_pack` | `refresh_pack`, `pack_score`, `pack_diff`, `audit_pack` | Core-aligned |
| Build pack citations/entities/search/briefs | `docpull pack citations`, `docpull pack entities`, `docpull pack search`, `docpull pack brief` | `build_citation_map`, `extract_pack_entities`, `search_pack`, `build_research_brief` | `pack_citations`, `pack_entities`, `pack_search`, `pack_brief` | Core-aligned |
| Prepare full pack intelligence bundle | `docpull pack prepare` | `prepare_pack` in `docpull.pack_tools` | `pack_prepare` | Core-aligned |
| Evidence-pack workflow protocol | `brand-pack`, `product-pack`, `styleguide-pack`, `image-pack`, `screenshot-pack`, `policy-pack`, `relationship-pack`, `dataset-pack` | concrete `build_*_pack` builders plus `WorkflowRequest`, `run_workflow`, `async_run_workflow` | `workflow_run` plus dedicated evidence tools | Core-aligned |
| Tracker import bundle | `docpull pack intelligence-bundle` (`company-brain` alias) | `build_intelligence_bundle` (`build_company_brain_bundle` alias) | `intelligence_bundle` | Core-aligned |
| Context CI and eval-grade context | `docpull ci`, `docpull pack prepare --eval-grade`, `docpull pack validate --level eval` | `run_context_ci`, `validate_pack_contract`, pack preparation helpers | Not exposed yet | Core-aligned |
| Build/query local source graphs | `docpull graph build`, `docpull graph status`, `docpull graph query`, `docpull graph neighbors`, `docpull graph refresh` | `build_graph`, `load_graph`, `graph_status`, `query_graph`, `graph_neighbors`, `refresh_graph` | `graph_build`, `graph_status`, `graph_query`, `graph_neighbors`, `graph_refresh` | Core-aligned |
| Local document/API/feed/typed ingestion | `docpull parse`, `docpull openapi-pack`, `docpull feed-pack`, `docpull paper-pack`, `docpull repo-pack`, `docpull package-pack`, `docpull standards-pack`, `docpull dataset-pack`, `docpull transcript-pack`, `docpull wiki-pack` | `parse_documents`, `parse_one_document`, `build_openapi_pack`, `build_feed_pack`, typed `build_*_pack` and `async_build_*_pack` helpers | Evidence workflow lanes are exposed; other typed lanes remain SDK/CLI adapted | Adapted |
| Source policy files | `docpull policy validate`, `docpull policy explain`, and policy-aware core workflows | `PolicyConfig` in `docpull.policy` | `validate_policy` over the same typed config | Core-aligned |
| Budget/accounting policy | `--budget`, `--explain-route`, `run.accounting.json`, policy `budget.maximum_paid_cost_usd` | `BudgetConfig`, accounting helpers, `PolicyConfig.budget` | `budget` on explicit cloud rendering tools | Core-aligned |
| Exports, local pack server, and report sharing | `docpull export` for JSONL, Sheets CSV/TSV, n8n JSON, Vercel AI JSON, CrewAI JSON, warehouse NDJSON, Parquet, and agent references; `docpull serve`; `docpull share` for Markdown/HTML report URLs | `export_pack`, `create_pack_app`, `create_report_server`, `render_report_document`, `load_pack` | `export_pack`, `serve_pack_status` | Adapted |
| Monitors | `docpull monitor init/run/trigger/pause/unpause/list/report/scheduler-snippet` | monitor helpers | Not exposed; monitor runs remain CLI/operator-owned | Surface-specific |
| Doctor diagnostics | `docpull --doctor`, `docpull render --check` | Diagnostic module, `check_agent_browser_availability` | Not exposed | CLI/operator-specific |
| Real-data acceptance smoke | `python scripts/release_a_plus_check.py --strict`; `python scripts/real_feature_smoke.py --json --full-mcp --strict-ci --auth-matrix --monitor-soak-minutes 10` with optional `--include-cloud` | Not public SDK | Not exposed | CLI/operator-specific |

## Stability Rules

- Runtime behavior should not change only to make surfaces look symmetric.
- New core workflows should first define the durable capability, then choose the right form for each surface.
- Legacy provider, parity, and benchmark modules are private experiments unless they are listed in this contract.
- Public docs should say "CLI, Python SDK/API, and MCP" when describing local OSS surfaces.
- MCP tool additions should be agent-safe, schema-first, and narrower than the CLI unless there is a clear agent use case.
