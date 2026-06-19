# Surface Contract

DocPull exposes the same core workflows through CLI, Python SDK, and MCP, with each surface optimized for its user.

In this project, **API** means the Python SDK / library API. DocPull does not currently ship a hosted HTTP API. If a hosted API is added later, it should get its own contract instead of being implied by the SDK contract.

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
| Crawl public docs/site | `docpull <url>` with crawl/output flags | `Fetcher`, `Scraper`, config models | `ensure_docs` for named aliases | Adapted |
| Output Markdown / NDJSON / SQLite / OKF | CLI output flags | Pipeline/config primitives | Indirect through fetched Markdown and pack tools | Adapted |
| List configured sources | Not a primary CLI command | `docpull.mcp.sources` internals, not public SDK | `list_sources` | MCP-focused |
| List cached/indexed sources | Not a primary CLI command | Local filesystem/search helpers | `list_indexed` | MCP-focused |
| Search cached docs | SQLite/local search helpers where applicable | `search_sqlite_documents` | `grep_docs` | Adapted |
| Read cached docs by path/range | Filesystem responsibility | Filesystem responsibility | `read_doc` | MCP-specific |
| Add/remove source aliases | Plugin/MCP workflow, not core CLI | Source internals, not public SDK | `add_source`, `remove_source` | MCP-specific |
| Score/diff context packs | `docpull pack score`, `docpull pack diff` | Pack helper modules | `pack_score`, `pack_diff` | Core-aligned |
| Build pack citations/entities/search/briefs | `docpull pack citations`, `docpull pack entities`, `docpull pack search`, `docpull pack brief` | `build_citation_map`, `extract_pack_entities`, `search_pack`, `build_research_brief` | `pack_citations`, `pack_entities`, `pack_search`, `pack_brief` | Core-aligned |
| Parallel context/API packs | `docpull parallel ...` | Parallel workflow modules | `parallel_context_pack`, `parallel_api_pack` | Adapted |
| Provider auth/init/status | `docpull provider ...`, `docpull parallel init/auth` | Provider helper modules | Not exposed | CLI/operator-specific |
| Doctor diagnostics | `docpull --doctor` | Diagnostic module | Not exposed | CLI/operator-specific |
| Benchmarks and comparison reports | `docpull benchmark ...` | Benchmark modules | Not exposed | CLI/operator-specific |
| Evidence packs | `docpull evidence-pack ...` | Evidence pack module | Not exposed | CLI/operator-specific |

## Stability Rules

- Runtime behavior should not change only to make surfaces look symmetric.
- New core workflows should first define the durable capability, then choose the right form for each surface.
- Public docs should say "CLI, Python SDK/API, and MCP" when describing surfaces, and should not imply a hosted API.
- MCP tool additions should be agent-safe, schema-first, and narrower than the CLI unless there is a clear agent use case.
