# Scraping Boundary

docpull is a browser-free-by-default web scraper for turning public static and
server-rendered web pages into local, auditable context artifacts. Its sharpest
workflow is public web-source ingestion for agents, retrieval-augmented
generation (RAG) systems, offline archives, and source packs.

## What docpull should do

- Fetch HTTPS pages with async HTTP, URL validation, DNS pinning, redirect
  revalidation, robots.txt compliance, and rate limits.
- Crawl known page graphs through sitemaps and static links.
- Convert server-rendered HTML, raw Markdown/text, OpenAPI specs, product
  pages, blogs, help centers, and common docs frameworks into clean Markdown.
- Emit local artifacts that agents can inspect: Markdown, JSON, NDJSON, SQLite,
  OKF bundles, manifests, source indexes, and MCP-readable cached sources.
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
when `--strict-js-required` is set. Optional rendering is explicit: use
`docpull render` or `--render fallback` with an external `agent-browser`,
Vercel Sandbox, or E2B backend. Render backends have separate installation,
domain, timeout, size, and cloud-cost controls so the browser-free crawler
remains the default path.

## When to use another tool

- Use Scrapy when you need a programmable spider framework with custom item
  pipelines.
- Use Playwright, Puppeteer, Selenium, or Crawlee when browser automation,
  sessions, queues, and interaction are the core job.
- Use a hosted extraction service when you want managed rendering, proxies, and
  external infrastructure.
- Use trafilatura directly when you only need article text extraction and do
  not need docpull's crawler, security posture, output manifests, or agent
  pack formats.

## Escalation ladder

When local capture is partial, keep escalation explicit and auditable:

1. Improve provider-free discovery with `docpull discover scan`, URL files, or
   sitemap inputs.
2. Retry public JS-rendered pages with local `agent-browser` via
   `--render fallback` or `docpull render --runtime local`.
3. Use BYOK providers such as Tavily, Exa, or Parallel only after reviewing a
   dry-run plan, estimated paid request count, and cost guard.
4. Use Vercel Sandbox or E2B rendering only when local rendering or local
   infrastructure is unsuitable.

Cloud and provider escalations must never be automatic consequences of a local
fetch. They require explicit flags, configured credentials, and local budget
guards.

## Hosted product boundary

Open-source DocPull owns local fetching, rendering adapters, discovery
adapters, extraction, indexing, packs, diffs, monitors, MCP, BYOK providers,
budget policy, accounting, and benchmarks.

A hosted product can sell managed execution rather than hidden scraping magic:
always-on schedules, browser/proxy infrastructure, persistent auth profiles,
queues, alerts, dashboards, collaboration, retention, SSO, audit logs, SLAs,
and bundled provider billing. It should not imply CAPTCHA bypass, stealth
scraping, automatic paid calls, or a proprietary web-scale index in the OSS
tool.

## Worthwhile expansion

The product should deepen the local agent-ingestion lane:

- More web-source fixtures and framework-specific extractors.
- Better local search over generated artifacts.
- Stable corpus schemas, source maps, and chunk provenance.
- Pack scoring, diffing, and refresh workflows.
- Explicit authenticated/internal source mode only after source policy,
  redaction, and audit logging are designed.
