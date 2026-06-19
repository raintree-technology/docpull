# docpull

**Turn public documentation sites into local Markdown, NDJSON, and agent-ready context packs. No browser required.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/docpull.svg)](https://badge.fury.io/py/docpull)
[![PyPI downloads](https://img.shields.io/pepy/dt/docpull?label=downloads)](https://pepy.tech/project/docpull)
[![GitHub stars](https://img.shields.io/github/stars/raintree-technology/docpull?style=social)](https://github.com/raintree-technology/docpull/stargazers)
[![License: MIT](https://img.shields.io/github/license/raintree-technology/docpull)](https://github.com/raintree-technology/docpull/blob/main/LICENSE)

docpull is a Python CLI, SDK, and MCP server that fetches public static or
server-rendered web pages and converts them into clean, auditable local
artifacts for LLMs, retrieval-augmented generation (RAG), offline research, and
agent workflows.

DocPull exposes the same core workflows through CLI, Python SDK, and MCP, with
each surface optimized for its user. The [Surface Contract](docs/surface-contract.md)
defines how those surfaces align and where they intentionally differ.

It works best on documentation sites, blogs, API references, OpenAPI specs,
vendor pages, and other public pages where the useful content is available in
HTML or embedded page data.

docpull is not a browser and does not render JavaScript. JS-only pages are
skipped with a clear reason. See [Scraping Boundary](docs/scraping-boundary.md)
and [Alternatives](docs/alternatives.md) for the full boundary.

## Install

```bash
pip install docpull
```

Install optional extras as needed:

```bash
pip install 'docpull[llm]'           # tiktoken for token-accurate chunking
pip install 'docpull[trafilatura]'   # alternative extractor for noisy pages
pip install 'docpull[mcp]'           # stdio MCP server
pip install 'docpull[parallel]'      # Parallel context packs
pip install 'docpull[observability]' # Raindrop benchmark tracing
pip install 'docpull[all]'           # all optional extras
```

## 30-Second Usage

```bash
docpull https://docs.python.org/3/library/asyncio.html --single -o ./asyncio-docs
```

Example output:

```text
asyncio-docs/
  index.md
  corpus.manifest.json
```

Markdown includes source metadata and readable page content:

```markdown
---
title: "asyncio - Asynchronous I/O"
source: https://docs.python.org/3/library/asyncio.html
source_type: "sphinx"
---

# asyncio - Asynchronous I/O

asyncio is a library to write concurrent code using the async/await syntax.
```

Stream chunked NDJSON for agents and RAG:

```bash
docpull https://docs.python.org/3/library/asyncio.html \
  --single \
  --profile llm \
  --stream | jq .
```

Each line is a JSON document:

```json
{"schema_version":1,"document_id":"doc_...","chunk_id":"chunk_...","url":"https://docs.python.org/3/library/asyncio.html","title":"asyncio - Asynchronous I/O","content":"asyncio is a library to write concurrent code...","source_type":"sphinx","chunk_index":0,"token_count":842}
```

## Common Workflows

```bash
# Crawl a site and write Markdown files
docpull https://docs.example.com -o ./docs-example

# Stream LLM-ready NDJSON chunks from a site
docpull https://docs.example.com --profile llm --stream | jq .

# Write SQLite with an FTS5 search index
docpull https://docs.example.com --format sqlite -o ./docs-db

# Build an Open Knowledge Format (OKF) bundle for portable source packs
docpull https://example.com --profile okf -o ./site-okf
```

More examples live in [CLI Recipes](docs/examples/README.md).

Use docpull when you need to:

- Convert public docs, blogs, API references, vendor pages, and OpenAPI specs
  into Markdown or chunked NDJSON for LLM and RAG pipelines.
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
| Safer fetching | HTTPS defaults, robots.txt compliance, SSRF protections, and redirect guards |

## Supported Sources

docpull uses async HTTP instead of browser automation and includes special
handling for common documentation and API surfaces.

| Source shape | Support |
| --- | --- |
| Static HTML / SSR docs | Extracts article or document regions |
| Next.js / Mintlify | Parses static HTML and `__NEXT_DATA__` when available |
| OpenAPI / Swagger | Renders specs into Markdown |
| Docusaurus / Sphinx / MkDocs | Extracts static article or document regions |
| VitePress / VuePress / Astro Starlight | Extracts static docs content |
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

docpull intentionally does not use a browser. It is not the right tool for:

- JS-only pages that require client-side rendering.
- Authenticated dashboards or private apps.
- Pages behind CAPTCHA or bot challenges.
- Workflows that require clicking, scrolling, or browser state.

For those cases, use browser automation, such as Playwright, then pass the
rendered HTML or exported content into your pipeline.

## How It Compares

| Tool type | Best for | Tradeoff |
| --- | --- | --- |
| `wget` / site mirroring | Downloading raw files | Not agent/RAG-oriented |
| Browser automation | JS-heavy pages and interactions | Slower, heavier, more stateful |
| Hosted extraction APIs | Managed extraction at scale | External dependency and cost |
| docpull | Local public-doc extraction and context packs | No JavaScript rendering |

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
    cfg = DocpullConfig(url="https://docs.example.com", profile=ProfileName.LLM)
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
  search pack records, write provider-free research briefs, or prepare the full
  sidecar bundle with
  `docpull pack citations`, `docpull pack entities`, `docpull pack search`,
  `docpull pack brief`, and `docpull pack prepare`.
- Optional provider workflows can use Parallel, Tavily, and Exa when configured.
  Successful provider context-pack runs are post-processed into the same local
  pack intelligence artifacts.
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
