# Executive Summary

## What DocPull Is

DocPull is a Python CLI/library for browser-free web scraping of static and
server-rendered pages into local agent-ready context. Its sharpest workflow is
documentation ingestion: async HTTP fetch, sitemap/static-link discovery,
SSRF-hardened URL validation, robots.txt compliance, Markdown conversion,
chunking, manifests, and output sinks for Markdown, JSON, NDJSON, SQLite, and
Open Knowledge Format.

The repo also ships a Python stdio MCP server for agent use, optional Parallel
provider pack workflows, a Claude Code plugin bundle, a product website, and an
internal Bun/TypeScript MCP lab tree.

Current audit target evidence:

- `pyproject.toml` declares package `docpull` version `4.3.0`.
- `uv run docpull --version` reports `docpull 4.3.0`.
- Git `HEAD` is `4a3b3b5`.
- The release branch contains the 4.3.0 version, changelog, and audit updates.

## What Works

Current local verification:

- `uv run pytest` passed: 532 tests.
- `uv run mypy src/docpull` passed: 73 source files.
- `uv run ruff check .` passed.
- `uv run ruff format --check .` passed.
- `uv run docpull --version` reports `docpull 4.3.0`.
- `uv run --extra dev python -m pre_commit run --all-files --show-diff-on-failure` passed.

Implemented and covered by tests:

- Strong URL validation: HTTPS defaults, localhost/internal suffix blocks,
  private/link-local/reserved/multicast/CGNAT IP blocks, IPv4-mapped IPv6
  handling, and connect-time DNS pinning when no proxy delegates DNS.
- Redirect revalidation and auth stripping across origins.
- robots.txt handling, sitemap parsing through `defusedxml`, static link
  crawling, include/exclude path filters, rate limits, and streaming discovery.
- Framework-aware extraction for Next.js, Mintlify, OpenAPI, raw Markdown/text,
  Docusaurus, and Sphinx fixtures.
- Structured output records with stable document/chunk IDs, content hashes,
  corpus manifests, source indexes, and output path source maps.
- OKF bundle output with reserved `index.md` handling and generated indexes.
- SQLite output with an FTS5 search index and Python search helper.
- First-class scraper-facing API names over the existing Fetcher engine:
  `Scraper`, `scrape_one`, `scrape_site`, and `ScrapeResult`.

## Current Risks

1. The new work is unreleased and still needs review before publishing.
2. Proxy mode still weakens DNS-pinning guarantees by design unless
   `--require-pinned-dns` is used.
3. Optional JavaScript rendering remains intentionally out of scope for the
   default product; it should only land behind a separate explicit adapter.
4. Authenticated/internal docs mode is still strategic work and needs source
   policy, redaction, audit logging, and secret-scoping design before expansion.
5. Live-regression captures remain valuable for docs frameworks whose static
   fixture coverage is now present but whose real pages can drift.
6. Root TypeScript MCP remains a separate/internal surface and should not become
   the primary documented MCP path unless that strategy is deliberately changed.

## Strategic Direction

Do not turn DocPull into a general browser automation scraper. Scrapy, Crawlee,
hosted extraction services, and trafilatura already cover adjacent broader
categories. DocPull should deepen the local, auditable, browser-free
web-to-agent-context lane:

1. Finish release hygiene and keep CLI/API/MCP smoke tests green.
2. Keep the scraper API as a thin, stable facade over Fetcher.
3. Expand framework fixtures before adding browser rendering.
4. Improve local retrieval, manifests, source maps, and pack diff/scoring.
5. Add authenticated/internal docs mode only after the security model is
   explicit and tested.
