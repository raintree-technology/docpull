import { absoluteUrl, discoveryPaths, site } from "@/lib/site";

export const dynamic = "force-static";

const body = `# ${site.name}

## Summary

docpull is a local, browser-free web puller that turns server-rendered pages into clean Markdown. It is built for documentation, blogs, help centers, pricing pages, changelogs, and other HTML-first content. It is useful for local archives, search indexes, agent workflows, and RAG ingestion.

## Positioning

Turn the web into Markdown.
Keep it all local.

docpull pulls server-rendered web pages into clean Markdown on your machine. Use it for docs, blogs, help centers, pricing pages, changelogs, and other HTML-first sites without a hosted crawler or browser runner in the loop.

## Installation

\`\`\`bash
pip install docpull
\`\`\`

Optional setup:
- PyPI: https://pypi.org/project/docpull/
- Claude plugin: https://github.com/raintree-technology/docpull/tree/main/plugin

## MCP and agent setup

docpull can be connected through a local MCP server for Claude Code, Cursor, and Codex.

Claude Code:
\`\`\`bash
pip install 'docpull[mcp]'
claude mcp add --transport stdio --scope user docpull -- docpull mcp
\`\`\`

Codex:
\`\`\`bash
pip install 'docpull[mcp]'
codex mcp add docpull -- docpull mcp
\`\`\`

Claude plugin install:
\`\`\`bash
pip install 'docpull[mcp]'
/plugin marketplace add raintree-technology/docpull
/plugin install docpull@docpull
\`\`\`

## Core workflow

1. Point docpull at a server-rendered URL.
2. Let the fetch pipeline discover pages and convert HTML to Markdown.
3. Use the output in a local docs folder, a search index, or an agent skill.

Agent skill output:
\`\`\`bash
docpull https://docs.example.com --skill example-docs --max-pages 100
\`\`\`

## Product strengths

- Markdown you can actually reuse.
- Dedup before disk fills up.
- Network rules stay enforced.
- Re-fetches stay selective.
- Partial crawls are first-class.

## Profiles

- RAG: deduped, metadata-rich output for LLMs and vector stores.
- Mirror: full archive with caching and resume support.
- Quick: 50 pages, depth 2, for testing and sampling.
- LLM: token-aware NDJSON for model ingestion, with clear skip reasons for JS-only pages. Add --strict-js-required when fail-loud routing is needed.

## Example output

\`\`\`
./docs/pricing.md:

---
title: "Pricing"
source: https://stripe.com/pricing
---

# Pricing

Choose the plan that matches your business.
Usage-based billing starts when you move past
the free tier.
\`\`\`

## Constraints

- docpull does not run a browser.
- JavaScript-heavy pages that require client-side rendering are detected and skipped.
- For JS-rendered sites, use a browser crawler when necessary.
- Config examples use the one-URL-per-DocpullConfig shape. For multiple sites,
  run separate CLI commands, load several configs in Python, or use MCP source
  aliases.

## Related resources

- Homepage: ${absoluteUrl(discoveryPaths.home)}
- llms.txt: ${absoluteUrl(discoveryPaths.llms)}
- llms-full.txt: ${absoluteUrl(discoveryPaths.llmsFull)}
- agent skills: ${absoluteUrl(discoveryPaths.agentSkills)}
- sitemap.xml: ${absoluteUrl(discoveryPaths.sitemap)}
- robots.txt: ${absoluteUrl(discoveryPaths.robots)}
- RSS: ${absoluteUrl(discoveryPaths.rss)}
- security.txt: ${absoluteUrl(discoveryPaths.security)}
- README: https://github.com/raintree-technology/docpull#readme
- Changelog: https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md
`;

export function GET() {
  return new Response(body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
