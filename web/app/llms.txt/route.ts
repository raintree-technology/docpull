import { absoluteUrl, discoveryPaths, site } from "@/lib/site";

export const dynamic = "force-static";

const body = `# ${site.name}

> docpull turns server-rendered web pages into clean Markdown locally.

docpull is a browser-free web puller for documentation, blogs, help centers, changelogs, pricing pages, and other HTML-first sites. It is designed for local archives, agent workflows, RAG ingestion, and auditable content pipelines.

## Canonical site
- ${absoluteUrl(discoveryPaths.home)}

## Key resources
- Homepage: ${absoluteUrl(discoveryPaths.home)}
- README: https://github.com/raintree-technology/docpull#readme
- PyPI: https://pypi.org/project/docpull/
- Changelog: https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md
- Plugin: https://github.com/raintree-technology/docpull/tree/main/plugin
- MCP server docs: https://github.com/raintree-technology/docpull#mcp-server

## Machine-readable endpoints
- llms.txt: ${absoluteUrl(discoveryPaths.llms)}
- llms-full.txt: ${absoluteUrl(discoveryPaths.llmsFull)}
- sitemap.xml: ${absoluteUrl(discoveryPaths.sitemap)}
- robots.txt: ${absoluteUrl(discoveryPaths.robots)}
- RSS: ${absoluteUrl(discoveryPaths.rss)}
- security.txt: ${absoluteUrl(discoveryPaths.security)}
- agent skills: ${absoluteUrl(discoveryPaths.agentSkills)}

## What matters
- Local-first operation. The crawl stays on your machine.
- Browser-free fetching. JavaScript-heavy pages are detected and skipped.
- Clean Markdown output with source metadata.
- Profiles for RAG, mirror, quick sampling, and LLM ingestion.
- MCP support for Claude Code, Cursor, and Codex workflows.

## Primary sections on the homepage
- MCP setup
- URL In, Corpus Out
- What Holds Up
- Presets With Opinions
- Real Output
- Start Local
- Sharp Edges

## Notes for agents
- Prefer the README for installation and feature details.
- Prefer homepage copy for current product positioning.
- Prefer llms-full.txt when a single consolidated summary is more useful than fetching the homepage HTML.
- Prefer .well-known/agent-skills.json when you want explicit task-level instructions for docs-grounded research.
- docpull does not render JavaScript-heavy sites; route to a browser crawler when strict HTML-first fetching is insufficient.
`;

export function GET() {
  return new Response(body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
