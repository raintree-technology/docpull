export function GET() {
  const body = `# DocPull

DocPull is a local-first Python CLI, SDK, and MCP server for declaring public web sources, syncing them into cited context packs, diffing changes, and exporting reproducible context for AI agents, MCP clients, and RAG pipelines.

Canonical project:
- GitHub: https://github.com/raintree-technology/docpull
- PyPI: https://pypi.org/project/docpull/
- Documentation: https://github.com/raintree-technology/docpull#readme
- Pricing: https://docpull.raintree.technology/pricing
- Privacy: https://docpull.raintree.technology/privacy
- Terms: https://docpull.raintree.technology/terms

Install:
\`\`\`bash
pip install docpull
pip install 'docpull[mcp]'
\`\`\`

Positioning:
- Local-first context dependencies for AI agents.
- Browser-free by default.
- Explicit user control for optional paid-capable provider or cloud-rendering routes.
- MIT licensed open-source core.
`;

  return new Response(body, {
    headers: {
      "content-type": "text/plain; charset=utf-8",
      "cache-control": "public, max-age=3600",
    },
  });
}
