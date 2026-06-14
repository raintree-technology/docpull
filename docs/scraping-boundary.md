# Scraping Boundary

docpull is a browser-free web scraper for turning static and server-rendered
web pages into local, auditable context artifacts. Its sharpest workflow is
documentation ingestion for agents, RAG systems, offline archives, and source
packs.

## What docpull should do

- Fetch HTTPS pages with async HTTP, URL validation, DNS pinning, redirect
  revalidation, robots.txt compliance, and rate limits.
- Crawl known page graphs through sitemaps and static links.
- Convert server-rendered HTML, raw Markdown/text, OpenAPI specs, and common
  docs frameworks into clean Markdown.
- Emit local artifacts that agents can inspect: Markdown, JSON, NDJSON, SQLite,
  OKF bundles, manifests, source indexes, and MCP-readable docs.
- Preserve provenance with source URLs, stable document/chunk IDs, content
  hashes, extraction metadata, and reproducible manifests.

## What docpull should not do by default

- Execute JavaScript for every page.
- Evade bot defenses, solve CAPTCHA challenges, or rotate residential proxies.
- Become a Scrapy-style programmable spider framework.
- Become a hosted scraping API.
- Send scraped content to third-party services unless the user explicitly opts
  into a provider workflow.

## JavaScript boundary

docpull detects JS-only pages and skips them with a clear reason, or fails loud
when `--strict-js-required` is set. Optional rendering can be added later only
behind a separate extra, explicit domain/budget controls, and tests proving the
browser-free default remains unchanged.

## When to use another tool

- Use Scrapy when you need a programmable spider framework with custom item
  pipelines.
- Use Crawlee when browser automation, sessions, queues, and anti-blocking
  workflows are the core job.
- Use a hosted extraction service when you want managed rendering, proxies, and
  external infrastructure.
- Use trafilatura directly when you only need article text extraction and do
  not need docpull's crawler, security posture, output manifests, or agent
  pack formats.

## Worthwhile expansion

The product should deepen the local agent-ingestion lane:

- More docs-framework fixtures and extractors.
- Better local search over generated artifacts.
- Stable corpus schemas, source maps, and chunk provenance.
- Pack scoring, diffing, and refresh workflows.
- Explicit authenticated/internal docs mode only after source policy, redaction,
  and audit logging are designed.
