# DocPull Launch Kit

Use this as the operational source for launch submissions. The longer target
research lives in [`docs/marketing-visibility-research.md`](marketing-visibility-research.md).

## Core Links

- Website: <https://docpull.raintree.technology>
- GitHub: <https://github.com/raintree-technology/docpull>
- PyPI: <https://pypi.org/project/docpull/>
- Downloads: <https://pepy.tech/project/docpull>
- MCP plugin: <https://github.com/raintree-technology/docpull/tree/main/plugin>
- Comparison guide: <https://github.com/raintree-technology/docpull/blob/main/docs/alternatives.md>

## Recommended GitHub Topics

```text
python
web-scraping
crawler
documentation
web-extraction
markdown
rag
llm
mcp-server
ai-agents
developer-tools
cli
openapi
docs-as-code
context-engineering
```

## Launch Copy

### Tagline

Turn public documentation sites into local Markdown, NDJSON, and agent-ready
context packs. No browser required.

### One-line pitch

DocPull turns public static and server-rendered documentation/API pages into
clean local Markdown, NDJSON, and agent-ready context packs from a Python CLI,
SDK, or MCP server.

### 50-word description

DocPull is a Python CLI, SDK, and MCP server that fetches public static or
server-rendered pages and converts them into clean Markdown, NDJSON, and local
context packs. It avoids browser automation, preserves source metadata, supports
LLM/RAG chunking, and includes security protections for agent-selected URLs.

### 150-word description

DocPull is a security-hardened Python tool for turning public static and
server-rendered web pages into clean, structured context for developers, AI
agents, and retrieval-augmented generation (RAG) systems. It fetches pages
without Playwright, discovers links, extracts main content, preserves source
metadata, and writes Markdown, NDJSON, Open Knowledge Format (OKF) bundles,
SQLite, or local archives. Documentation sites, blogs, vendor pages, API
references, OpenAPI specs, and other public web content all fit when the useful
content is available in HTML or embedded page data.

Use it as a CLI, Python SDK, or MCP server. Agents can fetch one URL, crawl a
site, stream chunked records, cache sources, grep local Markdown, and read exact
files with attribution. SSRF, XXE, DNS-rebinding, and CRLF-injection protections
are on by default because AI agents often choose URLs dynamically. DocPull is
not browser automation; it is the fast, auditable path for clean web context.

## Show HN

Title:

```text
Show HN: DocPull - turn public docs into Markdown, no browser required
```

First comment:

```text
Hi HN, I built DocPull, a Python CLI/SDK/MCP server for turning public static and server-rendered documentation/API pages into clean Markdown and NDJSON for coding agents, RAG indexes, and offline archives.

The specific pain: agents often need current web context, but browser automation is heavy, hosted scraping APIs can be overkill, and raw HTML makes poor context. DocPull uses async HTTP, framework-aware extraction, source metadata, chunking, and local caching. It intentionally does not render JavaScript. For public pages that are static or server-rendered, that tradeoff keeps it fast, inspectable, and easy to run in local agent workflows.

Examples:

pip install docpull
docpull https://www.python.org/blogs/ --max-pages 25 -o ./python-news
docpull https://docs.python.org/3/library/asyncio.html --single
pip install 'docpull[mcp]'
docpull mcp

I would especially like feedback from people building coding agents, RAG/search systems, knowledge tooling, and scraping pipelines. Where would you expect the boundary to be between a browser-free crawler like this and full browser automation?
```

## Product Hunt

- Tagline: `Turn public docs into local Markdown and agent-ready context packs.`
- Suggested categories: Developer Tools, Open Source, Artificial Intelligence,
  Productivity.
- Maker comment angle: explain the web-context problem, the no-browser
  boundary, the MCP server, and where feedback is wanted.

## Existing Assets

- Logo SVG: [`web/public/logo.svg`](../web/public/logo.svg)
- Open Graph image: [`web/public/og-image.png`](../web/public/og-image.png)
- README demo GIF: `https://pub-e85a1abca36f4fd8b4300a6ec2d6f45f.r2.dev/marketing/docpull/1768954147343-iaiziy-docpull-terminal-hero.gif`
- Download chart: [`docs/cumulative-downloads-history.svg`](cumulative-downloads-history.svg)

## Web-Only Tasks

- Set GitHub repo topics using the recommended list above.
- Verify PyPI renders the updated metadata after the next release.
- Export square PNG logo variants for Product Hunt and AI directories.
- Capture 2-3 screenshots: terminal run, generated Markdown, MCP tool usage.
- Record a 30-60 second demo video or GIF.
- Submit the MCP server to Official MCP Registry, Glama, and Smithery.
- Submit newsletters and launch/community posts in the order from the research doc.
