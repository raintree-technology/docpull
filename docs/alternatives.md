# DocPull Alternatives and When to Use Each

DocPull is optimized for one job: turn public static and server-rendered web
pages into clean, source-linked local context for AI agents, RAG/search systems,
offline archives, and developer workflows.

Web-source crawling is the core workflow; documentation is one high-value lane,
not the boundary. It is not a browser automation framework, anti-bot scraper,
hosted extraction API, or search engine. Use the guide below to choose the right
tool.

## Quick Decision Guide

| Need | Best fit | Why |
|---|---|---|
| Pull public web pages into Markdown for an agent or RAG index | DocPull | Fast local CLI/SDK/MCP workflow, source metadata, chunking, framework-aware extraction |
| Fetch one URL from an agent tool call | DocPull | `--single` and MCP `fetch_url` avoid crawl setup and browser overhead |
| Crawl static/server-rendered websites at modest to large scale | DocPull | Async HTTP, framework-aware extraction, manifests, cache support |
| Parse one messy article page in Python | trafilatura | Excellent text extraction library; DocPull can also use it as an optional extractor |
| Build a custom crawling pipeline with queues, middleware, and spiders | Scrapy | Mature scraping framework for custom pipelines and broad crawler control |
| Automate a real browser or interact with JavaScript-heavy pages | agent-browser, Puppeteer, Selenium | Required when useful content only exists after client-side rendering or interaction |
| Build browser-backed crawlers in the JavaScript ecosystem | Crawlee | Strong fit for JavaScript/TypeScript crawling stacks |
| Use a hosted web-to-LLM extraction service | Firecrawl, Jina Reader, hosted extraction APIs | Useful when you want an API service to manage crawling/extraction infrastructure |
| Search the live web before building a pack | Dedicated search/extract providers, then DocPull source ingestion | Search providers find candidate sources; DocPull's public release contract starts when selected sources are fetched, parsed, packed, and validated locally |

## Where DocPull Is Strongest

- Public static and server-rendered web pages, including documentation sites,
  blogs, API references, vendor pages, and product content.
- Static or server-rendered content from Sphinx, MkDocs, Docusaurus, Mintlify,
  GitBook, ReadMe.io, Next.js, VitePress, VuePress, Astro Starlight, OpenAPI,
  Redoc, Scalar, blogs, and similar sites.
- Agent workflows that need local files, source attribution, deterministic
  manifests, and readable Markdown instead of raw HTML.
- RAG/search pipelines that need repeatable document IDs, chunk IDs, hashes,
  token-aware chunking, and audit-friendly corpora.
- Local-first workflows where running a browser or hosted crawler would be too
  heavy, too opaque, or too awkward inside an agent sandbox.

## Where DocPull Is the Wrong Tool

- JS-only single-page apps where the meaningful content is created after client
  hydration.
- Sites that require login, form interaction, scrolling interaction, or
  browser state.
- CAPTCHA, anti-bot, residential proxy, or evasion workflows.
- Full custom crawler infrastructure with domain-specific queues, item
  pipelines, or storage backends.
- Search-engine style discovery across the open web.

## Comparison Matrix

| Tool/category | Local | Browser-free | Python-first | Agent/MCP workflow | Docs-aware extraction | Hosted service |
|---|---:|---:|---:|---:|---:|---:|
| DocPull | Yes | Yes | Yes | Yes | Yes | No |
| trafilatura | Yes | Yes | Yes | No | Partial | No |
| Scrapy | Yes | Yes by default | Yes | No | No | No |
| agent-browser/Puppeteer/Selenium | Yes | No | Mixed | Yes | No | No |
| Crawlee | Yes | Mixed | No | No | No | No |
| Firecrawl/Jina Reader/hosted extraction APIs | No | Hidden/varies | API-first | Partial | Partial | Yes |

## Practical Positioning

If you already know the URL and want clean local context, start with
DocPull:

```bash
docpull https://example.com/blog --profile llm --stream
```

If you need a single page inside an agent loop:

```bash
docpull https://example.com/pricing --single
```

If you need browser interaction, use browser automation outside DocPull. If you
need open-web source discovery before extraction, use a dedicated search or
research service separately, then feed selected URLs, files, or specs through
DocPull's local fetch, parse, OpenAPI, pack, validation, and export workflow.
