# DocPull 6.8: local web search, cited research, and shared pack recipes

DocPull 6.8 adds web search, cited research answers, and reproducible pack
recipes, all running on the local fetch pipeline. It also resolves open
dependency advisories and requires Python 3.11 or newer.

This is the first PyPI release with these features. 6.7.0 was published to npm
only, because its PyPI release gate stopped on the advisories fixed here.

## Search the web through a backend you choose

```bash
docpull search "rate limit headers" --backend searxng --include-domain docs.github.com
docpull search "static site generators" --backend marginalia --max-results 5 --json
```

`docpull search` uses a self-hosted SearXNG instance or Marginalia's independent
index. `--include-content` fetches each result through the local pipeline.

For offline search, build or download discovery index shards. Shards are SQLite
files of page URLs, titles, and headings, with no page bodies. `pull` needs an
explicit HTTPS manifest URL and verifies each shard's sha256.

```bash
docpull index pull https://index.example/v1/manifest.json
docpull search "asyncio timeouts" --backend index
```

## Answer questions with citations

```bash
docpull research "What is the GitHub REST API rate limit?" --backend searxng
```

`docpull research` searches, fetches results locally, and returns ranked passages
with exact spans and `[S#]` citations. An optional OpenAI-compatible model can
rewrite the answer. It must run on localhost unless you pass
`--allow-remote-llm`, and the rewrite is kept only when every citation names a
real source.

## Share packs as recipes

```bash
docpull registry export --name stripe-api --version 2026-10 --description "Stripe API reference"
docpull add stripe-api@2026-10
docpull registry verify stripe-api@2026-10 --json
```

A recipe holds a pack's public sources, pinned page URLs, and per-page content
hashes. It holds no fetched content. `docpull add` checks the recipe's sha256,
syncs the sources, and reports which pages still match.

## Point hosted-API code at local fetching

`docpull serve --compat` accepts Tavily `/extract`, Exa `/contents`, and
Firecrawl `/scrape` request shapes, so agent code written for those APIs can
switch by changing its base URL. With `--search-backend`, it also serves their
search shapes, Exa `/answer`, Tavily `include_answer`, Firecrawl crawl jobs,
Parallel task runs, and Tavily `/research`. Network binds require
`--api-key-env`.

## Security

- The lockfiles use Pillow 13.0.0, which fixes its 2026 advisories, including
  heap buffer overflows in the JPEG and TIFF code and a Ghostscript argument
  injection.
- The package requires aiohttp 3.14.4 or newer. The extras that use them require
  mcp 1.30.0 and pypdf 6.21.1 or newer.
- Builds and CI moved to Bun 1.4.4, Node 24.22.1, and uv 0.13.4. Those releases
  fix a Brotli decoder RCE and TLS hostname verification bypasses, and remove
  the emSign ECC Root CA - G3 after the eMudhra intermediate key compromise.

## Upgrade and compatibility

```bash
pip install --upgrade 'docpull==6.8.0'
# For the Python MCP server:
pip install --upgrade 'docpull[mcp]==6.8.0'
# For the TypeScript pack-reading and CLI SDK:
npm install @raintree-technology/docpull-sdk@6.8.0
```

DocPull now requires Python 3.11 or newer. Python 3.10 reaches end of life in
October 2026, and Pillow 13 does not support it. On Python 3.10, pip keeps
resolving DocPull 6.6.0.

The public CLI, Python SDK exports, and MCP tools are unchanged apart from the
additions above. This release deletes internal modules behind commands that were
already removed from the public surface, including the hosted-provider workflows,
parity wrappers, evidence packs, and the `Scraper` facade. Code that imported
those module paths directly will fail. Use `docpull-bench` in `bench/` for
provider comparisons.

See the [CLI recipes](https://github.com/raintree-technology/docpull/blob/v6.8.0/docs/cli-recipes.md)
for full examples and the
[changelog](https://github.com/raintree-technology/docpull/blob/v6.8.0/docs/CHANGELOG.md)
for the complete release changes.
