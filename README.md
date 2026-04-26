# docpull

**Security-hardened, browser-free crawler that turns static documentation sites into clean, AI-ready Markdown — fast.**

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://badge.fury.io/py/docpull.svg)](https://badge.fury.io/py/docpull)
[![Downloads](https://pepy.tech/badge/docpull)](https://pepy.tech/project/docpull)
[![License: MIT](https://img.shields.io/github/license/raintree-technology/docpull)](https://github.com/raintree-technology/docpull/blob/main/LICENSE)

<p align="center">
  <a href="https://docpull.raintree.technology">
    <img src="https://pub-e85a1abca36f4fd8b4300a6ec2d6f45f.r2.dev/marketing/docpull/1768954147343-iaiziy-docpull-terminal-hero.gif" alt="docpull demo" width="600">
  </a>
</p>

docpull uses async HTTP (not Playwright) to fetch server-rendered pages,
extracts main content, and writes clean Markdown with source-URL frontmatter —
in seconds, with a small install footprint. It won't render JavaScript, but for
the large class of docs that don't need it (API references, Python/Go stdlib,
most dev-tool docs, OpenAPI specs, Next.js and Docusaurus builds), it is a
fast, auditable, sandbox-friendly way to pipe documentation into an LLM context,
a RAG index, or an offline archive. SSRF, XXE, DNS-rebinding, and
CRLF-injection protections are on by default — a necessity when an AI agent
is choosing the URLs.

## Install

```bash
pip install docpull

# Optional extras
pip install 'docpull[llm]'           # tiktoken for token-accurate chunking
pip install 'docpull[trafilatura]'   # alternative extractor for noisy pages
pip install 'docpull[mcp]'           # run as an MCP server for AI agents
pip install 'docpull[all]'           # everything above
```

## Quick start

```bash
# Crawl and save Markdown
docpull https://docs.example.com

# One page, no crawl — the fast path for agents
docpull https://docs.example.com/guide --single

# LLM-ready NDJSON with 4k-token chunks streamed to stdout
docpull https://docs.example.com --profile llm --stream | jq .

# Mirror a site for offline use
docpull https://docs.example.com --profile mirror --cache
```

## Framework-aware extraction

docpull inspects each page before running the generic extractor and can pull
content directly from framework data feeds:

| Framework | Strategy |
|-----------|----------|
| Next.js   | Parses `__NEXT_DATA__` JSON |
| Mintlify  | `__NEXT_DATA__` with Mintlify tagging |
| OpenAPI   | Renders `openapi.json` / `swagger.json` into Markdown |
| Docusaurus| Detected and tagged; generic extractor produces Markdown |
| Sphinx    | Detected and tagged; generic extractor produces Markdown |

JS-only SPAs with no server-rendered content are detected and skipped with a
clear reason (or, with `--strict-js-required`, reported as an error so agents
can route elsewhere).

## Agent-friendly features

- **`--single`** — fetch a single URL without discovery. Designed for tool loops.
- **`--stream`** — NDJSON one-record-per-line, flushed on every page, pipeable.
- **`--max-tokens-per-file N`** — split each page into token-bounded chunks on
  heading boundaries (exact counts with tiktoken, estimate without).
- **`--emit-chunks`** — write one file or record per chunk instead of per page.
- **`--strict-js-required`** — hard-fail on JS-only pages instead of silently
  skipping.
- **`--extractor trafilatura`** — swap in [trafilatura](https://trafilatura.readthedocs.io/)
  for sites where the default heuristics struggle.

## Python API

```python
from docpull import fetch_one

ctx = fetch_one("https://docs.python.org/3/library/asyncio.html")
print(ctx.title, ctx.source_type)
print(ctx.markdown[:500])
```

Async streaming:

```python
import asyncio
from docpull import Fetcher, DocpullConfig, ProfileName, EventType

async def main():
    cfg = DocpullConfig(
        url="https://docs.example.com",
        profile=ProfileName.LLM,  # chunked NDJSON output
    )
    async with Fetcher(cfg) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_PROGRESS:
                print(f"{event.current}/{event.total}: {event.url}")
        print(f"Done: {fetcher.stats.pages_fetched} pages")

asyncio.run(main())
```

Single-page from an agent tool:

```python
from docpull import Fetcher, DocpullConfig

async def tool_call(url: str) -> str:
    async with Fetcher(DocpullConfig(url=url)) as f:
        ctx = await f.fetch_one(url, save=False)
        return ctx.markdown or ctx.error or ""
```

## Profiles

```bash
docpull https://site.com --profile rag      # Default. Dedup, rich metadata.
docpull https://site.com --profile llm      # NDJSON + chunks + metadata.
docpull https://site.com --profile mirror   # Full archive, polite, cached.
docpull https://site.com --profile quick    # Sampling: 50 pages, depth 2.
```

## MCP server

docpull ships an MCP (Model Context Protocol) server so AI agents can call it
directly over stdio:

```bash
pip install 'docpull[mcp]'
docpull mcp  # starts the stdio server
```

Add to Claude Desktop or Claude Code manually:

```json
{
  "mcpServers": {
    "docpull": {
      "command": "docpull",
      "args": ["mcp"]
    }
  }
}
```

Or, if you use Claude Code, install the plugin instead — it bundles the MCP
server, five slash commands (`/docs-add`, `/docs-search`, `/docs-list`,
`/docs-refresh`, `/docs-remove`), and a meta-skill that teaches Claude
when to reach for docpull automatically:

```bash
# 1. Install docpull with the MCP extra (required for the plugin)
pip install 'docpull[mcp]'
```

```
# 2. Then in Claude Code:
/plugin marketplace add raintree-technology/docpull
/plugin install docpull@docpull
```

See [plugin/README.md](plugin/README.md) for details.

Tools exposed (8 total — read tools advertise `readOnlyHint` so hosts that auto-approve safe tools won't prompt):

Read:
- `fetch_url(url, max_tokens?)` — one-shot fetch, no crawl. HTTPS-only, SSRF-validated.
- `list_sources(category?)` — show available aliases (react, nextjs, fastapi, …)
- `list_indexed()` — what has been fetched locally, with last-fetched age
- `grep_docs(pattern, library?, limit?, context?)` — regex search across fetched Markdown (length-capped + wall-clock budgeted to mitigate ReDoS)
- `read_doc(library, path, line_start?, line_end?)` — read a specific cached file, optionally line-sliced

Write:
- `ensure_docs(source, force?, profile?)` — fetch a named library (cached 7 days). Forwards progress to clients that supply a `progressToken`.
- `add_source(name, url, description?, category?, max_pages?, force?)` — register a user alias (HTTPS-only, atomic write to `sources.yaml`).
- `remove_source(name, delete_cache?)` — drop a user alias and (optionally) its cached docs.

All tools that carry data also return `structuredContent` validated against an `outputSchema` for clients that prefer typed output.

User-defined sources live in `~/.config/docpull-mcp/sources.yaml`:

```yaml
sources:
  mydocs:
    url: https://docs.example.com
    description: My internal docs
    category: internal
    maxPages: 200
```

## Output

Markdown files with YAML frontmatter:

```markdown
---
title: "Getting Started"
source: https://docs.example.com/guide
source_type: "nextjs"
---

# Getting Started
…
```

NDJSON (one record per page or chunk):

```json
{"url": "...", "title": "...", "content": "...", "hash": "...", "token_count": 842, "chunk_index": 0}
```

## Security

- HTTPS-only, mandatory robots.txt compliance
- SSRF protection: blocks private/internal network IPs, DNS rebinding via
  connect-time address pinning
- XXE protection via `defusedxml` on sitemaps
- Path traversal and CRLF header injection guards
- Auth headers stripped on cross-origin redirects

When running with `--proxy`, DNS pinning is delegated to the proxy. Pass
`--require-pinned-dns` to refuse this configuration and keep the connector-
level SSRF guarantees in effect.

## Options

Run `docpull --help` for the full list. Highlights:

```
Core:
  --profile {rag,mirror,quick,llm,custom}
  --single                Fetch one URL (no crawl)
  --format {markdown,json,ndjson,sqlite}
  --stream                Stream NDJSON to stdout

LLM / chunking:
  --max-tokens-per-file N
  --tokenizer NAME        tiktoken encoding (default cl100k_base)
  --emit-chunks           One file/record per chunk

Content extraction:
  --extractor {default,trafilatura}
  --no-special-cases      Disable framework extractors
  --strict-js-required    Error on JS-only pages

Cache:
  --cache                 Enable incremental updates
  --cache-dir DIR
  --cache-ttl DAYS
```

## Performance

End-to-end numbers from `tests/benchmarks/test_10k_pages.py` against a
synthetic 10,000-page localhost site (RAG profile, `max_concurrent=50`,
HTTP keep-alive, 5% injected duplicate content):

| Metric | Value |
|---|---|
| Total wall time | ~27 s |
| Discovery (sitemap parse) | ~80 ms |
| Fetch + convert + save | ~27 s |
| Per-page latency p50 / p95 / p99 | ~2.6 / 4.6 / 5.3 ms |
| Peak RSS delta from baseline | ~28 MB |
| Cache manifest size on disk | ~3.4 MB |
| Duplicates detected (5% injected) | 499 / 500 |

Reproduce with `make benchmark` (requires `aiohttp`; runs the gated
benchmark in `tests/benchmarks/` and prints a JSON line you can pipe
into trend tooling).

## Troubleshooting

```bash
docpull --doctor              # Check installation
docpull URL --verbose         # Verbose output
docpull URL --dry-run         # Test without downloading
docpull URL --preview-urls    # List URLs without fetching
```

## Links

- [Website](https://docpull.raintree.technology)
- [PyPI](https://pypi.org/project/docpull/)
- [GitHub](https://github.com/raintree-technology/docpull)
- [Changelog](https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md)
- [Metrics](https://github.com/raintree-technology/docpull/blob/main/METRICS.md) — auto-refreshed daily (PyPI downloads, plugin installs via clone count, traffic)

## License

MIT
