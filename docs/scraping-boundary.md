# Web Source Boundary

DocPull is browser-free by default. It turns public static and server-rendered
web pages into local, auditable context artifacts for agents,
retrieval-augmented generation (RAG) systems, offline archives, and source
packs.

For the identity, robots.txt, rate-limit, and AI/TDM opt-out rules docpull
enforces while fetching, see [Compliance Posture](compliance.md).

## What docpull should do

- Fetch HTTPS pages with async HTTP, URL validation, DNS pinning, redirect
  revalidation, robots.txt compliance, and rate limits.
- Crawl known page graphs through sitemaps and static links.
- Convert server-rendered HTML, raw Markdown/text, OpenAPI specs, product
  pages, blogs, help centers, and common docs frameworks into clean Markdown.
- Recognize standards-style plain text and common textual media types, and
  locally parse remote PDFs only when `--remote-documents pdf` is explicit.
- Emit local artifacts that agents can inspect: Markdown, JSON, NDJSON, SQLite,
  OKF bundles, manifests, source indexes, and MCP-readable cached sources.
- Preserve provenance with source URLs, stable document/chunk IDs, content
  hashes, extraction metadata, and reproducible manifests.

## What docpull should not do by default

- Execute JavaScript for every page.
- Evade bot defenses, solve CAPTCHA challenges, or rotate residential proxies.
- Become a Scrapy-style programmable spider framework.
- Become a hosted extraction API.
- Send fetched content to third-party services as part of the public fetch path.
- Treat arbitrary downloads or remote PDFs as web pages without explicit
  document-type authorization.

## JavaScript boundary

docpull detects JS-only pages and skips them with a clear reason, or fails loud
when `--strict-js-required` is set. Optional rendering is explicit: use
`docpull render` or `--render fallback` with the local `agent-browser` runtime,
Vercel Sandbox, or E2B. Render backends have separate installation, domain,
timeout, size, and cloud-cost controls so the browser-free fetch path remains
the default. Network browser rendering also requires an operator
acknowledgement via `DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1`, because browser
runtimes cannot fully enforce redirect/subresource allow-lists after the
initial target check.

For deeper documentation capture, use the core fetch/project flow with explicit
source URLs, sitemap-derived URLs, or stored project sources. It still reports
JS-only limitations in run artifacts; it does not silently install or invoke a
browser.

## When to use another tool

- Use Scrapy when you need a programmable spider framework with custom item
  pipelines.
- Use agent-browser, Puppeteer, Selenium, or Crawlee when browser automation,
  sessions, queues, and interaction are the core job.
- Use a hosted extraction service when you want managed rendering, proxies, and
  external infrastructure.
- Use trafilatura directly when you only need article text extraction and do
  not need docpull's source traversal, security posture, output manifests, or agent
  pack formats.

## Escalation ladder

When local capture is partial, keep escalation explicit and auditable:

1. Improve local source coverage with explicit project sources, URL files, or
   sitemap-derived inputs.
2. Retry public JS-rendered pages with local `agent-browser` via
   `--render fallback` or `docpull render --runtime local`, with
   `DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1` for trusted targets.
3. Use Vercel Sandbox or E2B rendering only when local rendering or local
   infrastructure is unsuitable.

Cloud rendering must never be an automatic consequence of a local fetch. It
requires explicit flags, configured credentials, and local budget guards.

## Release boundary

Open-source DocPull owns local fetching, rendering adapters, source aliases,
extraction, indexing, v3 packs, diffs, monitors, MCP, budget policy, and
accounting.

This release does not include hosted scheduling, managed proxies, CAPTCHA
bypass, stealth scraping, automatic paid calls, or a proprietary web-scale
index.
