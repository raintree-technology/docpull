# Internal MCP Lab

This directory is not the supported docpull MCP server.

Use the package-shipped Python MCP server instead:

```bash
pip install 'docpull[mcp]'
docpull mcp
```

The Python server is the release-contract path documented in the root README
and used by the Claude Code plugin. It exposes the supported local tools for
fetching, caching, grepping, and reading web-source Markdown.

The code in this directory is an internal TypeScript + Bun lab for optional
PostgreSQL/pgvector semantic search. It has a different runtime, persistence
model, dependency set, and privacy boundary because semantic indexing can call
OpenAI embeddings when configured. Do not configure agents or users to run this
server unless you are explicitly developing that lab.

For maintainers working on this lab only:

```bash
bun install
bun run typecheck
bun test
```

Required services for the semantic-search path are intentionally not documented
as an end-user install flow here. Promote this directory to a supported product
only after its install path, release target, privacy copy, and package boundary
are deliberately decided.
