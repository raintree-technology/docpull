<p align="center">
  <img
    src="https://raw.githubusercontent.com/raintree-technology/docpull/main/docs/launch-assets/logo-square-light-400.png"
    alt="DocPull"
    width="112"
  />
</p>

# DocPull agent plugin

**Active plugin for developers using Codex or Claude Code.** Pull static and
server-rendered public web sources into an agent's local context with citations.

DocPull aligns core workflows across CLI, Python SDK, and MCP, with each surface
optimized for its user. See the [Surface Contract](../docs/surface-contract.md)
for the boundary between the plugin's MCP tools and the broader CLI/SDK.

## Install

The plugin wraps the `docpull` CLI. Install the MCP extra first:

```bash
pipx install 'docpull[mcp]==6.5.2'
docpull --version                   # should print 6.5.2 or newer
docpull mcp --help
```

The plain `pip install docpull` does not include the MCP dependency. If the
executable is missing, the plugin exits with the exact `pipx` setup command.

In Claude Code:

```text
/plugin marketplace add raintree-technology/docpull
/plugin install docpull@docpull
```

In Codex, install the plugin from the configured marketplace or a local plugin
source. The plugin starts the `docpull mcp` stdio server and exposes the
`docpull-research` skill.

## Try one source

```text
> /web-add fastapi
> How does FastAPI handle dependency injection scoping?
```

Expected result: the agent searches the cached source and answers with attribution to the local
Markdown. The first crawl populates the cache; later reads stay local.

## What you get

<!-- docpull:mcp-tools:start -->
- **MCP server** (37 tools):
  - Read: `fetch_url`, `list_sources`, `list_indexed`, `grep_docs`, `read_doc`, `pack_score`, `pack_diff`, `pack_citations`, `pack_entities`, `pack_search`, `pack_brief`, `graph_status`, `graph_query`, `graph_neighbors`, `validate_policy`, `explain_routes`, `serve_pack_status`
  - Write: `render_url`, `ensure_docs`, `workflow_run`, `website_pack`, `brand_pack`, `product_pack`, `styleguide_pack`, `image_pack`, `screenshot_pack`, `policy_pack`, `relationship_pack`, `intelligence_bundle`, `refresh_pack`, `audit_pack`, `pack_prepare`, `graph_build`, `graph_refresh`, `export_pack`, `add_source`, `remove_source`
  - All read tools advertise `readOnlyHint` so hosts that auto-approve safe tools won't prompt for them.
<!-- docpull:mcp-tools:end -->
- **Claude Code slash commands**:
  - `/web-add <alias-or-url>` — fetch a web source into the local index.
  - `/web-search <pattern> [source]` — regex-search cached Markdown and pull surrounding context for the top hits.
  - `/web-list` — show what's cached, with last-fetched age.
  - `/web-refresh <source>` — bypass the 7-day cache and re-fetch.
  - `/web-remove <source> [--keep-cache]` — drop a user alias and its cached Markdown.
  - `/docs-add`, `/docs-search`, `/docs-list`, `/docs-refresh`, and `/docs-remove` remain compatibility aliases for existing users.
- **Meta-skill** (`docpull-research`): teaches the agent *when* to reach for docpull — so you don't have to remember the tool exists every time you ask about a library, API, vendor, product page, or web source.

## Built-in source aliases

These are fetchable by name without any URL setup: `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `langchain`, `supabase`, `drizzle`, `prisma`.

For anything else, pass an HTTPS URL: `/web-add https://www.python.org/blogs/`.

## Where fetched Markdown is cached

By default, fetched Markdown lives under `$XDG_DATA_HOME/docpull-mcp/docs/` (or `~/.local/share/docpull-mcp/docs/` on macOS/Linux). Override with `DOCPULL_DOCS_DIR` if you want it somewhere else (e.g. one cache per project).

## Limits and privacy

- 100% local. No telemetry. No remote services.
- The plugin only sends HTTP requests to the URLs you ask it to fetch.
- The User-Agent is `docpull/<version> (+https://github.com/raintree-technology/docpull)` — public, identifiable, robots.txt-respecting.
- JavaScript rendering is explicit rather than part of the default fetch path.
- The plugin supports research workflows; it does not certify that a source is
  complete, current, or correct.

## Troubleshooting

| Symptom                                     | Fix |
|---------------------------------------------|-----|
| MCP tools missing after install             | Run `docpull mcp --help`. If it errors with "requires the 'mcp' package", reinstall with `pip install 'docpull[mcp]'`. |
| `/web-add fastapi` says "unknown source"    | Run `mcp__docpull__list_sources()` to see current aliases. Use a URL instead. |
| Slow first fetch                            | Normal — the first crawl populates the cache. Later runs use the local cache and conditional requests. |
| Want to refresh stale sources               | `mcp__docpull__ensure_docs(source="<alias>", force=true)`. |

## Deeper documentation

- [DocPull project guide](../README.md)
- [Surface contract](../docs/surface-contract.md)
- [CLI recipes](../docs/cli-recipes.md)
- [Security policy](../SECURITY.md)

## License

MIT — same as docpull itself. Source: <https://github.com/raintree-technology/docpull>.
