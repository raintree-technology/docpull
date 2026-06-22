# docpull

**Turn public web sources into local Markdown, NDJSON, and agent-ready context packs. Browser-free by default.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/docpull.svg)](https://badge.fury.io/py/docpull)
[![PyPI downloads](https://img.shields.io/pepy/dt/docpull?label=downloads)](https://pepy.tech/project/docpull)
[![GitHub stars](https://img.shields.io/github/stars/raintree-technology/docpull?style=social)](https://github.com/raintree-technology/docpull/stargazers)
[![License: MIT](https://img.shields.io/github/license/raintree-technology/docpull)](https://github.com/raintree-technology/docpull/blob/main/LICENSE)

docpull is a Python CLI, SDK, and MCP server that fetches public or explicitly
authorized static/server-rendered web pages and converts them into clean,
auditable local artifacts for LLMs, retrieval-augmented generation (RAG),
offline research, and agent workflows.

DocPull is local-first: direct fetching, sitemap/link discovery, extraction,
indexing, pack intelligence, and local `agent-browser` rendering can run with no
provider account and no required API spend. Tavily, Exa, Parallel, and cloud
renderers are optional escalation paths when local and open-source routes are
not enough.

DocPull exposes the same core workflows through CLI, Python SDK, and MCP, with
each surface optimized for its user. The [Surface Contract](docs/surface-contract.md)
defines how those surfaces align and where they intentionally differ.

Web-source ingestion is the core workflow. Documentation is one high-value
lane, not the product boundary. It works best on static or server-rendered
pages such as blogs, API references, OpenAPI specs, changelogs, vendor pages,
product pages, filings, docs sites, and other pages where the useful content is
available in HTML or embedded page data.

docpull is browser-free by default. JS-only pages are skipped with a clear
reason unless you explicitly opt into the local `agent-browser` renderer. See
[Scraping Boundary](docs/scraping-boundary.md) and
[Alternatives](docs/alternatives.md) for the full boundary.

## Install

```bash
pip install docpull
```

Install optional extras as needed:

```bash
pip install 'docpull[llm]'           # tiktoken for token-accurate chunking
pip install 'docpull[trafilatura]'   # alternative extractor for noisy pages
pip install 'docpull[mcp]'           # stdio MCP server
pip install 'docpull[serve]'         # local pack JSON server runner
pip install 'docpull[parallel]'      # Parallel context packs
pip install 'docpull[observability]' # Raindrop benchmark tracing
pip install 'docpull[e2b]'           # E2B cloud sandbox renderer SDK
pip install 'docpull[all]'           # all optional extras
```

Browser rendering is an explicit external extension, not part of the base
install. Install an `agent-browser` compatible CLI separately, put it on
`PATH`, or set `DOCPULL_AGENT_BROWSER_BIN=/path/to/agent-browser`. Verify the
runtime with `docpull render --check`. Render targets must use HTTPS except for
localhost/loopback HTTP during local testing, and DocPull keeps renderer action
permissions locked down to HTML retrieval only.

For stronger isolation, cloud runtimes are available explicitly:
`docpull render URL --runtime vercel` uses the Vercel Sandbox CLI and Vercel
auth, while `docpull render URL --runtime e2b` uses the E2B Python SDK and
`E2B_API_KEY`. These are never enabled by default. All runtimes execute the same
`agent-browser --json` renderer contract. Use `--cloud-max-estimated-cost` to
set a local per-render budget guard, and use `--cloud-agent-browser-install skip`
with a prebuilt sandbox/template that already includes `agent-browser`. For E2B,
pass `--template` or set `DOCPULL_E2B_TEMPLATE` to use that prebuilt environment.

## Free-First Budgets

Use `--budget 0` when a run must not make paid-capable provider or cloud calls:

```bash
docpull https://docs.example.com --budget 0 -o ./docs/example
docpull discover scan https://docs.example.com -o ./packs/discovery
docpull render https://example.com/app --runtime local --budget 0
docpull providers context-pack "Find official docs" --provider all --dry-run --budget 0 --json
docpull benchmark quick --zero-dollar --target-set zero-dollar --provider all
```

Under a zero budget, local cache, direct HTTP, sitemap/static-link discovery,
local extraction, local indexing, pack analysis, monitors, and local
`agent-browser` rendering remain allowed. Live Tavily, Exa, Parallel, Vercel
Sandbox, and E2B calls are blocked before execution. Runs involving a budget or
paid-capable route write `run.accounting.json` with non-secret route, cost,
HTTP/cache, browser, and blocked-action metadata.

Use `docpull discover scan URL` to build a provider-free discovery pack from
open site hints: `llms.txt`, RSS/Atom feeds, OpenAPI specs, sitemap indexes,
and public GitHub docs trees. It writes the same `candidate_sources.ndjson`
contract as provider imports and URL/sitemap files, so the next step is still
`docpull discover select` or `docpull discover fetch`.

When a zero-dollar benchmark or local run is partial, DocPull reports the
lowest-friction escalation path before spending money: local `--render fallback`
first, BYOK providers next, and cloud rendering only when local rendering or
infrastructure is the blocker. Benchmark reports include suggested commands,
estimated paid request counts, and estimated paid cost guards before any
provider or cloud call is made.

The `zero-dollar` benchmark target set is the Phase 2 measurement matrix. It
keeps the existing docs/provider targets and adds JS-heavy docs, pricing,
filings, feeds, sitemaps, and search-to-evidence tasks. The report classifies
each target as `complete_for_0`, `complete_with_local_browser`, `partial_for_0`,
`requires_provider`, `requires_cloud_browser`, or `blocked_by_policy`.

## Open Source And Hosted Boundary

The open-source package owns local fetching, local rendering adapters,
provider-free discovery, extraction, indexing, packs, diffs, monitors, MCP,
BYOK providers, budget policy, accounting, and benchmarks.

A hosted DocPull product, if offered, should sell managed execution: always-on
schedules, browser/proxy infrastructure, persistent auth profiles, queues,
alerts, dashboards, collaboration, retention, SSO, audit logs, SLAs, and
bundled provider billing. The hosted boundary does not change the OSS default:
no hidden paid calls, no CAPTCHA bypass, no stealth scraping, and no claim of a
proprietary web-scale index.

## 30-Second Usage

```bash
docpull https://www.python.org/blogs/ --single -o ./python-news
```

Example output:

```text
python-news/
  index.md
  corpus.manifest.json
```

Markdown includes source metadata and readable page content:

```markdown
---
title: "Blogs"
source: https://www.python.org/blogs/
source_type: "html"
---

# Blogs

News from the Python Software Foundation, Python core developers, and the
wider Python community.
```

Stream chunked NDJSON for agents and RAG:

```bash
docpull https://www.python.org/blogs/ \
  --single \
  --profile llm \
  --stream | jq .
```

Each line is a JSON document:

```json
{"schema_version":1,"document_id":"doc_...","chunk_id":"chunk_...","url":"https://www.python.org/blogs/","title":"Blogs","content":"News from the Python Software Foundation...","source_type":"html","chunk_index":0,"token_count":842}
```

## Common Workflows

```bash
# Crawl a public web section and write Markdown files
docpull https://www.python.org/blogs/ -o ./python-news

# Stream LLM-ready NDJSON chunks from a source
docpull https://www.python.org/blogs/ --profile llm --stream | jq .

# Write SQLite with an FTS5 search index
docpull https://www.python.org/blogs/ --format sqlite -o ./python-news-db

# Build an Open Knowledge Format (OKF) bundle for portable source packs
docpull https://example.com --profile okf -o ./site-okf

# Turn a source corpus into agent-ready skills/rules
docpull https://sdk.vercel.ai \
  --skill vercel-ai \
  --skill-agent all \
  --skill-description "Vercel AI SDK source reference"
```

Local-first parity workflows mirror common hosted search/extract/crawl/research
API shapes while writing auditable files instead of relying on a hosted index:

```bash
# Normalize candidate URLs without fetching content
docpull map urls ./urls.txt -o ./packs/map

# Extract known URLs into a local pack
docpull extract-pack ./urls.txt -o ./packs/extract

# Select mapped candidates and fetch them
docpull crawl-pack ./packs/map --select top:10 -o ./packs/crawl

# Answer/research from an existing local pack with lifecycle artifacts
docpull research-pack ./packs/crawl \
  --objective "Summarize auth and webhook behavior" \
  --schema ./output.schema.json

# Build a cited entity/list pack from existing evidence
docpull entities-pack ./packs/crawl --limit 100
```

More examples live in [CLI Recipes](docs/examples/README.md).

With an explicit `--skill-agent`, docpull stores the scraped corpus under
`.docpull/skills/<name>/references` and creates agent-specific wrappers that
point at that corpus. `--skill-agent claude` writes a Claude Code skill under
`.claude/skills/<name>/`, `--skill-agent codex` writes a Codex skill under
`.agents/skills/<name>/` with `agents/openai.yaml`, and `--skill-agent cursor`
writes a Cursor project rule at `.cursor/rules/<name>.mdc`. Use
`--skill-agent all` to create all three. If you pass `--output-dir`, docpull
stages the generated corpus there; explicit `--skill-agent` targets still write
their active agent wrappers.

Use docpull when you need to:

- Convert public web sources - docs, blogs, API references, vendor pages,
  product pages, changelogs, filings, and OpenAPI specs - into Markdown or
  chunked NDJSON for LLM and RAG pipelines.
- Give an agent a local tool for fetching, caching, grepping, and reading web
  sources.
- Build repeatable context packs with stable IDs, hashes, manifests, and source
  metadata.
- Mirror public web content for offline work while preserving attribution.

## Why docpull?

docpull is designed for agent and RAG workflows, not just downloading pages.

| Need | docpull gives you |
| --- | --- |
| Clean Markdown | Article-focused extraction with source metadata |
| LLM chunks | NDJSON streaming and optional token-aware chunking |
| Repeatability | Stable document IDs, chunk IDs, hashes, and manifests |
| Offline work | Cached archives and mirrored source artifacts |
| Agent access | Local CLI, Python SDK, and stdio MCP server |
| Downstream exports | JSONL, Sheets CSV/TSV, n8n JSON, Vercel AI JSON, CrewAI JSON, warehouse NDJSON, optional Parquet, and agent skills |
| Safer fetching | HTTPS defaults, robots.txt compliance, SSRF protections, and redirect guards |

## Supported Sources

docpull uses async HTTP instead of browser automation by default and includes
special handling for common web, documentation, and API surfaces.

| Source shape | Support |
| --- | --- |
| Static HTML / SSR pages | Extracts article, main, or document regions |
| Next.js / Mintlify | Parses static HTML and `__NEXT_DATA__` when available |
| OpenAPI / Swagger | Renders specs into Markdown |
| Docusaurus / Sphinx / MkDocs | Extracts static article or document regions |
| VitePress / VuePress / Astro Starlight | Extracts static content regions |
| GitBook / ReadMe.io | Extracts available article or content regions |
| Redoc / Scalar | Extracts static API reference regions |
| JS-only apps | Skipped unless useful content is present in HTML or embedded data |

Use `--strict-js-required` when an agent should treat JS-only pages as hard
errors instead of normal skips.

## Output Formats

| Output | Use it for |
| --- | --- |
| Markdown | Local readable source snapshots with YAML frontmatter |
| NDJSON | Streamed records or chunked records for agents and RAG |
| SQLite | Local retrieval with an FTS5 index |
| OKF | Portable Open Knowledge Format bundles with indexes and manifests |
| Archive / mirror | Cached offline source snapshots |

Every file-backed run writes `corpus.manifest.json` with stable document IDs,
chunk IDs, hashes, output paths, and chunk counts. See
[Corpus Manifest](docs/corpus-manifest.md).

## Profiles

```bash
docpull https://site.com --profile rag        # Default. Dedup + metadata.
docpull https://site.com --profile llm        # NDJSON chunks for agents/RAG.
docpull https://site.com --profile okf        # Portable Open Knowledge Format bundle.
docpull https://site.com --profile mirror     # Cached archive.
docpull https://site.com --profile quick      # Small sampling crawl.
docpull https://site.com --profile sec-filing # EDGAR-friendly evidence chunks.
```

Run `docpull --help` for the full option list.

## When Not to Use docpull

docpull intentionally does not use a browser unless rendering is explicitly
enabled. It is not the right tool for:

- JS-only pages that require complex browser workflows beyond static rendered HTML.
- Authenticated dashboards or private apps.
- Pages behind CAPTCHA or bot challenges.
- Workflows that require clicking, scrolling, or browser state.

For those cases, use browser automation, such as Playwright, then pass rendered
HTML or exported content into your pipeline. For simple public JS-rendered
pages, `docpull render` and `--render fallback` provide an explicit local
fallback without changing the default crawler behavior. The fallback requires
the optional external `agent-browser` backend.

## How It Compares

| Tool type | Best for | Tradeoff |
| --- | --- | --- |
| `wget` / site mirroring | Downloading raw files | Not agent/RAG-oriented |
| Browser automation | JS-heavy pages and interactions | Slower, heavier, more stateful |
| Hosted extraction APIs | Managed extraction at scale | External dependency and cost |
| docpull | Local public web-source extraction and context packs | No JavaScript rendering by default |

## Python SDK

```python
from docpull import fetch_one

ctx = fetch_one("https://docs.python.org/3/library/asyncio.html")
print(ctx.title)
print(ctx.markdown[:500])
```

```python
import asyncio
from docpull import Fetcher, DocpullConfig, EventType, ProfileName

async def main():
    cfg = DocpullConfig(url="https://example.com/blog", profile=ProfileName.LLM)
    async with Fetcher(cfg) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_PROGRESS:
                print(f"{event.current}/{event.total}: {event.url}")

asyncio.run(main())
```

## MCP Server

docpull can run as a stdio MCP server for agent clients:

```bash
pip install 'docpull[mcp]'
docpull mcp
```

Claude Code:

```bash
claude mcp add --transport stdio docpull -- docpull mcp
```

Cursor and Claude Desktop use the same `mcpServers` shape:

```json
{
  "mcpServers": {
    "docpull": {
      "type": "stdio",
      "command": "docpull",
      "args": ["mcp"]
    }
  }
}
```

The supported MCP path is the Python stdio server started by `docpull mcp`.
The repository's `mcp/` directory is an internal TypeScript/Bun lab and is not
part of the package release contract.

## Advanced Workflows

- `docpull[parallel]` can discover, extract, enrich, score, diff, and archive
  live web sources with your own Parallel API key. See
  [Parallel Integration](docs/parallel.md).
- Local pack intelligence can build citation maps, extract cited entities,
  search pack records, write provider-free research briefs, build cited source
  graphs, or prepare the full sidecar bundle with
  `docpull pack citations`, `docpull pack entities`, `docpull pack search`,
  `docpull pack brief`, `docpull graph build`, `docpull graph query`, and
  `docpull pack prepare`.
- Local-first expansion commands add policy files, discovery packs, refresh
  reports, audits, cited answers, exports, a localhost pack server, explicit
  rendering, authenticated-source checks, and cron-friendly monitors:
  `docpull policy`, `docpull discover`, `docpull refresh`,
  `docpull pack audit`, `docpull answer-pack`, `docpull export`,
  `docpull serve`, `docpull render`, `docpull auth check`, and
  `docpull monitor`.
- `docpull export` writes local files for OpenAI vector JSONL, LangChain,
  LlamaIndex, DSPy, Sheets CSV/TSV, n8n workflow JSON, Vercel AI SDK JSON,
  CrewAI JSON, warehouse NDJSON, optional Parquet via `docpull[parquet]`, and
  Codex/Claude/Cursor agent references.
- Optional provider workflows can use Parallel, Tavily, and Exa when configured.
  Tavily and Exa are available through `docpull providers ...` and first-class
  aliases such as `docpull tavily context-pack`, `docpull exa context-pack`,
  `docpull exa extract-pack`, and `docpull tavily map-pack`. Use
  `docpull providers capabilities` to see the shared baseline and provider-only
  extensions. For agent or CI logs, use
  `docpull providers auth --json --require-ready --redact-paths` for offline
  local readiness, then `docpull providers probe --json --require-verified
  --redact-paths` when explicit live key validation is intended. Successful
  provider context-pack runs are post-processed into the same local pack
  intelligence artifacts.
  See [CLI Recipes](docs/examples/README.md#parallel-context-pack).
- SEC filing evidence packs use rule profiles such as
  [vendor-dependency-rules.yml](docs/examples/vendor-dependency-rules.yml).

## Security Defaults

- HTTPS-only fetching with robots.txt compliance.
- SSRF protections, private network blocking, DNS rebinding protection, and
  connect-time address pinning.
- XXE protection for sitemaps.
- Path traversal and CRLF header injection guards.
- Auth headers stripped on cross-origin redirects.

When running with `--proxy`, DNS pinning is delegated to the proxy. Pass
`--require-pinned-dns` to refuse that configuration.

## Troubleshooting

```bash
docpull --doctor
docpull render --check
docpull URL --verbose
docpull URL --dry-run
docpull URL --preview-urls
```

## Documentation

- [CLI Recipes](docs/examples/README.md) - common commands and advanced workflows.
- [Scraping Boundary](docs/scraping-boundary.md) - what docpull does and does not fetch.
- [Alternatives](docs/alternatives.md) - when to use browser automation or hosted extraction.
- [Corpus Manifest](docs/corpus-manifest.md) - stable IDs, hashes, and source maps.
- [Surface Contract](docs/surface-contract.md) - how the CLI, Python SDK/API, and MCP surfaces align.
- [Parallel Integration](docs/parallel.md) - live-source context pack workflows.
- [Changelog](docs/CHANGELOG.md) - release history.

## Links

- [Website](https://docpull.raintree.technology)
- [PyPI](https://pypi.org/project/docpull/)
- [GitHub](https://github.com/raintree-technology/docpull)
- [Metrics](METRICS.md)

## License

MIT
