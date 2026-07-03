# docpull

**Context dependencies for AI agents. Browser-free by default.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/docpull.svg?label=package)](https://pypi.org/project/docpull/)
[![PyPI downloads](https://img.shields.io/pepy/dt/docpull?label=downloads)](https://pepy.tech/project/docpull)
[![GitHub stars](https://img.shields.io/github/stars/raintree-technology/docpull?style=social)](https://github.com/raintree-technology/docpull/stargazers)
[![License: MIT](https://img.shields.io/github/license/raintree-technology/docpull)](https://github.com/raintree-technology/docpull/blob/main/LICENSE)

DocPull is a local-first dependency manager for AI context. Define the public
docs and web sources an agent depends on, sync them into cited context packs,
diff what changed, and export reproducible context for Cursor, Claude, Codex,
OpenAI, LlamaIndex, LangChain, MCP clients, and RAG pipelines.

The core workflow is a `docpull.yaml` plus a `.docpull/context.lock.json`,
similar in spirit to code dependency manifests and lockfiles:

```bash
docpull init my-agent-context
docpull add stripe react postgres
docpull install
docpull deps
docpull sync
docpull diff
docpull export context-pack --target codex
```

Bundled aliases such as `stripe`, `react`, `postgres`, `openai`, and
`apple-hig` expand to normal HTTPS sources in `docpull.yaml`. Runs stay
reproducible through the lockfile: source URLs, discovered URLs, content hashes,
run IDs, aliases, and export metadata are recorded without storing secrets.
Use `docpull sources list` to inspect the bundled alias catalog and
`docpull install` to validate or recreate the local dependency lock.
Use `docpull deps` to see the current dependency, lockfile, latest run, and
export status.

Projects can also track typed known-source specs such as `pypi:requests`,
`rfc:9110`, `wiki:Web_scraping`, or a local dataset path. Those sources sync
through their typed lanes and do not use discovery.

The original `docpull URL ...` workflow still works: fetch public or explicitly
authorized static/server-rendered web pages and write clean Markdown, NDJSON,
SQLite, or OKF outputs. Project mode adds the persistent evidence lifecycle on
top: sources, runs, diffs, exports, evals, accounting, and local auditability.

DocPull is local-first: direct fetching, sitemap/link discovery, extraction,
indexing, pack intelligence, and opt-in `agent-browser` rendering can run with
no external account and no required API spend. Cloud rendering is explicit and
budget-guarded.

DocPull aligns core workflows across CLI, Python SDK, and MCP, with each surface
optimized for its user. The [Surface Contract](docs/surface-contract.md) defines
how those surfaces align and where they intentionally differ.
For the context dependency workflow, see
[Context Dependencies](docs/context-dependencies.md).

Web-source ingestion is the core workflow. Documentation is one high-value
lane, not the product boundary. It works best on static or server-rendered
pages such as blogs, API references, OpenAPI specs, changelogs, vendor pages,
product pages, filings, docs sites, and other pages where the useful content is
available in HTML or embedded page data.

DocPull is browser-free by default. JS-only pages are skipped with a clear
reason unless you explicitly opt into a local renderer. See
[Web Source Boundary](docs/scraping-boundary.md) and
[Alternatives](docs/alternatives.md) for the full boundary.

## Install

```bash
pip install docpull
```

## Project Quickstart

```bash
docpull init stripe-docs
docpull add stripe
docpull install
docpull sync
docpull deps
docpull diff
docpull export context-pack --target cursor
```

## Context CI

Use Context CI when an agent loop depends on current, cited context and a
missing or stale source should fail the build:

```bash
docpull ci --prepare
```

`docpull ci` runs locally against either a project root or a standalone pack. It
checks the project lockfile, pack score, pack audit, coverage confidence,
citation coverage, eval-grade sidecars, evidence basis quality, rights
metadata, and optional context predictions. It writes `context-ci.report.json` and `CONTEXT_CI.md`;
the command exits non-zero when hard gates fail.

Minimal GitHub Actions job:

```yaml
name: Context CI
on: [pull_request]
jobs:
  context:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install docpull
      - run: docpull ci --prepare
```

For the full workflow, see [Context CI](docs/context-ci.md). The durable
artifact shape is documented in
[Context Pack Contract v3](docs/context-pack-contract-v3.md).

Example diff after a later sync:

```text
Project diff: +4 -2 ~18 api=2 pricing=1

Changed pages:
- /payments/payment-intents
  likely API behavior change
- /billing/subscriptions
  pricing / billing change
- /webhooks
  likely API behavior change

0 failed URLs
0 robots blocked
0 paid/cloud routes used
```

## Context Pack Contract

DocPull writes three explicit layers of artifacts:

| Layer | Purpose | Contract check |
| --- | --- | --- |
| Raw extraction | Fetched documents, chunks, routes, and source index sidecars | `docpull pack validate PACK --level raw` |
| Agent-ready pack | Raw evidence plus citation index, coverage, score, audit, and lock sidecars | `docpull pack validate PACK --level agent` |
| Eval-grade pack | Agent pack plus rights, provenance, basis/eval artifacts, and pack card | `docpull pack validate PACK --level eval` |

Core ingestion paths write into the same v3 contract:

```bash
docpull https://docs.example.com -o packs/docs
docpull parse ./handbook.pdf -o packs/handbook --backend auto
docpull openapi-pack ./openapi.json -o packs/api
docpull feed-pack https://example.com/news -o packs/news
docpull paper-pack arxiv:1706.03762 -o packs/papers
docpull repo-pack psf/requests -o packs/repo --cache
docpull package-pack pypi:requests -o packs/package
docpull standards-pack rfc:9110 -o packs/standard
docpull dataset-pack ./metrics.csv -o packs/dataset
docpull transcript-pack ./meeting.vtt -o packs/transcript
docpull wiki-pack wiki:Web_scraping -o packs/wiki
docpull pack prepare packs/docs --eval-grade
docpull pack validate packs/docs --level eval
docpull export packs/docs --format openai-vector-jsonl -o exports/openai.jsonl
docpull export packs/docs --format cursor-rules -o .cursor/rules --skill-name docs
```

Use `docpull ci --prepare` to validate a project or standalone pack in CI.

Install optional extras as needed:

```bash
pip install 'docpull[llm]'           # tiktoken for token-accurate chunking
pip install 'docpull[trafilatura]'   # alternative extractor for noisy pages
pip install 'docpull[parse]'         # MarkItDown + Unstructured local document parsers
pip install 'docpull[presidio]'      # optional Presidio PII detection for redaction
pip install 'docpull[mcp]'           # stdio MCP server
pip install 'docpull[serve]'         # local pack JSON server runner
pip install 'docpull[parquet]'       # optional Parquet export support
pip install 'docpull[e2b]'           # E2B cloud sandbox renderer SDK
```

Prefer installing the extras needed for the current lane instead of a broad
bundle. The base install remains useful without API keys or paid services.

Browser rendering is an explicit external extension, not part of the base
install. Install an `agent-browser` compatible CLI separately, put it on
`PATH`, or set `DOCPULL_AGENT_BROWSER_BIN=/path/to/agent-browser`. Verify the
runtime with `docpull render --check`. Render targets must use HTTPS except for
localhost/loopback HTTP during local testing, and DocPull keeps renderer action
permissions locked down to HTML retrieval only. Because browser rendering
cannot fully enforce redirect, subresource, or connect-time DNS allow-lists,
network browser rendering fails closed unless the operator sets
`DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1` for trusted targets. For
localhost/loopback HTTP tests, set `DOCPULL_RENDER_ALLOW_LOCAL_TARGETS=1`.

For stronger isolation, cloud runtimes are available explicitly:
`docpull render URL --runtime vercel` uses the Vercel Sandbox CLI and Vercel
auth, while `docpull render URL --runtime e2b` uses the E2B Python SDK and
`E2B_API_KEY`. These are never enabled by default. All runtimes execute the same
`agent-browser --json` renderer contract. Use `--cloud-max-estimated-cost` to
set a local per-render budget guard, and use `--cloud-agent-browser-install skip`
with a prebuilt sandbox/template that already includes `agent-browser`. For E2B,
pass `--template` or set `DOCPULL_E2B_TEMPLATE` to use that prebuilt environment.

For release acceptance, run the opt-in real-data smoke harness. The default path
uses public free sources and local tooling; `--include-cloud` also attempts the
keyed/cloud render lanes when the local environment is configured for them.
The strict scorecard also requires synchronized generated metadata and a clean
`git status --short` before tagging.

```bash
python scripts/release_a_plus_check.py --strict
python scripts/real_feature_smoke.py --json --full-mcp --strict-ci --auth-matrix --monitor-soak-minutes 10
python scripts/real_feature_smoke.py --include-cloud --json
```

## Free-First Budgets

Use `--budget 0` when a run must not make paid-capable cloud calls:

```bash
docpull https://docs.example.com --budget 0 -o ./docs/example
DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 docpull render https://example.com/app --runtime local --budget 0
```

Under a zero budget, local cache, direct HTTP, sitemap/static-link discovery,
local extraction, local indexing, pack analysis, monitors, and local
browser rendering for trusted targets remain allowed. Vercel Sandbox and E2B
rendering are blocked before execution. Runs involving a budget or paid-capable
route write `run.accounting.json` with non-secret route, cost, HTTP/cache,
browser, and blocked-action metadata.

## Release Boundary

The open-source package owns local fetching, explicit rendering adapters,
source aliases, v3 pack contracts, validation, preparation, exports, Context
CI, monitors, MCP, budget policy, and accounting.

This release does not include a hosted scheduler, browser/proxy service,
accounts, marketplace, proprietary web index, CAPTCHA bypass, stealth scraping,
or hidden paid calls.

## Persistent Projects

Use project mode when a source corpus needs to stay fresh over time. A project
is a local `docpull.yaml` plus a `.docpull/` state directory containing run
history, cache, manifests, context-pack exports, eval sets, and a SQLite index.

```bash
docpull init stripe-docs
docpull add https://docs.stripe.com
docpull sync
docpull diff
docpull export context-pack --target cursor
```

Each sync writes a normal local DocPull pack under `.docpull/runs/<run_id>/`,
including `run.json`, `documents.jsonl`, `chunks.jsonl`, `manifest.json`,
`documents.ndjson`, `corpus.manifest.json`, `sources.md`,
`source-health.json`, `local.pack.json`, and accounting metadata.

```bash
# Inspect the latest project state
docpull status

# Show run history
docpull history

# Diff the latest two runs, with deterministic local categories by default
docpull diff

# Write a review summary for the latest run
docpull review

# Create a versioned context-pack release
docpull release context-pack --target cursor --tag stripe-docs-v1

# One-command project sync, diff, and export for one source
docpull watch https://docs.stripe.com --export cursor --alert changes
```

Ad hoc `docpull watch` projects are bounded to one page and one level of depth
by default. Use explicit bounds when the watch should cover more:

```bash
docpull watch https://docs.stripe.com --export cursor --max-pages 10 --max-depth 2
```

`docpull diff` is hash-based and deterministic locally. Optional BYOK semantic
summaries are advisory and skip cleanly when no model key is configured. Each
diff also writes local semantic categories to `semantic.diff.json`.
Use `docpull add URL --discover` or `docpull sync --update-discovery` to
refresh and persist discovered source URLs in `docpull.yaml`; sync then uses
that stored URL set for repeatable exact refreshes.

For authenticated sources, store only environment variable references in
`docpull.yaml`; DocPull resolves values in memory at sync time and writes only
masked auth type/readiness to status, manifests, reviews, releases, and
webhooks:

```yaml
sources:
  - name: internal-docs
    url: https://docs.example.com
    auth:
      type: bearer_env
      env: EXAMPLE_DOCS_TOKEN
      policy: explicit-private
```

The launch screenshot for this flow lives at
[`docs/launch-assets/docpull-project-diff-demo.png`](docs/launch-assets/docpull-project-diff-demo.png).

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

More examples live in [CLI Recipes](docs/examples/README.md).

With an explicit `--skill-agent`, docpull stores the fetched corpus under
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
| OpenAPI pack | `docpull openapi-pack` emits endpoint/schema records with v3 sidecars |
| RSS / Atom / JSON Feed | `docpull feed-pack` emits item-level records, dates, and listing sidecars |
| Research papers | `docpull paper-pack` emits paper metadata, abstracts, optional local/arXiv PDF full text, and references |
| Public GitHub repos | `docpull repo-pack` emits repo metadata, README/docs/examples/changelog files, manifests, and releases |
| npm / PyPI packages | `docpull package-pack` emits registry metadata, README/description, versions, license, dependencies, and install commands |
| Standards | `docpull standards-pack` emits RFC, IETF, W3C, and WHATWG metadata plus section-level records |
| Local datasets | `docpull dataset-pack` emits bounded schema, exact row counts where streamable, column, null-count, and sample summaries |
| Transcripts | `docpull transcript-pack` emits timestamped segment records from VTT, SRT, text, JSON, or direct transcript URLs |
| Wikimedia / Wikipedia | `docpull wiki-pack` emits MediaWiki REST page metadata, license/revision metadata, and section-level records |
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

All file-backed outputs now write the DocPull output contract v3 raw sidecars:
`corpus.manifest.json`, `sources.md`, and `acquisition.routes.json`. Use
`docpull pack validate <pack-dir> --level raw|agent|eval` to check whether a
pack is raw extraction output, agent-ready context, or eval-grade context.

Local files can enter the same contract with the document parse lane:

```bash
docpull parse ./handbook.pdf -o ./packs/handbook --backend auto
docpull parse ./handbook.docx -o ./packs/handbook --prepare --eval-grade
```

`--backend auto` reads plain text/Markdown directly and uses optional
MarkItDown or Unstructured parsers for complex office/PDF files when installed.
Install `docpull[markitdown]`, `docpull[unstructured]`, or `docpull[parse]`
for those backends.

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

For those cases, use full browser automation outside DocPull, then pass
rendered HTML or exported content into your pipeline. For simple public
JS-rendered pages, use `docpull render --runtime local` or fetch with
`--render fallback` for an explicit `agent-browser` fallback without changing
the default fetch behavior. DocPull does not claim complete browser coverage
unless rendering is explicitly enabled and available.

Use `--extractor ensemble` when a crawl should score multiple local extraction
candidates and keep the strongest Markdown. The ensemble always includes the
built-in generic extractor and adds trafilatura when `docpull[trafilatura]` is
installed.

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

- Local pack intelligence can build citation maps, extract cited entities,
  search pack records, build cited source graphs, prepare the full sidecar
  bundle, and write eval-grade rights/provenance artifacts with
  `docpull pack citations`, `docpull pack entities`, `docpull pack search`,
  `docpull pack brief`, `docpull graph build`, `docpull graph query`,
  and `docpull pack prepare --eval-grade`.
- Release commands add policy files, refresh reports, audits, exports, a
  localhost pack server, explicit rendering, authenticated-source checks, and
  cron-friendly monitors:
  `docpull policy`, `docpull refresh`,
  `docpull parse`, `docpull openapi-pack`, `docpull feed-pack`,
  `docpull paper-pack`, `docpull repo-pack`, `docpull package-pack`,
  `docpull standards-pack`, `docpull dataset-pack`, `docpull transcript-pack`,
  `docpull wiki-pack`,
  `docpull pack validate`,
  `docpull pack audit`, `docpull export`,
  `docpull serve`, `docpull share`, `docpull render`, `docpull auth check`,
  and `docpull monitor`.
- `docpull export` writes local files for OpenAI vector JSONL, LangChain,
  LlamaIndex, DSPy, Sheets CSV/TSV, n8n workflow JSON, Vercel AI SDK JSON,
  CrewAI JSON, warehouse NDJSON, optional Parquet via `docpull[parquet]`, and
  Codex/Claude/Cursor agent references.
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
- [Web Source Boundary](docs/scraping-boundary.md) - what docpull does and does not fetch.
- [Alternatives](docs/alternatives.md) - when to use browser automation or hosted extraction.
- [Corpus Manifest](docs/corpus-manifest.md) - stable IDs, hashes, and source maps.
- [Surface Contract](docs/surface-contract.md) - how the CLI, Python SDK/API, and MCP surfaces align.
- [Changelog](docs/CHANGELOG.md) - release history.

## Links

- [Website](https://docpull.raintree.technology)
- [PyPI](https://pypi.org/project/docpull/)
- [GitHub](https://github.com/raintree-technology/docpull)
- [Metrics](METRICS.md)

## License

MIT
