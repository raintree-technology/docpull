# docpull plugin

Pull static and server-rendered public web sources into Codex or Claude Code. Local, fast, no API keys.

DocPull exposes the same core workflows through CLI, Python SDK, and MCP, with
each surface optimized for its user. See the [Surface Contract](../docs/surface-contract.md)
for the boundary between the plugin's MCP tools and the broader CLI/SDK.

## What you get

<!-- docpull:mcp-tools:start -->
- **MCP server** (36 tools):
  - Read: `fetch_url`, `list_sources`, `list_indexed`, `grep_docs`, `read_doc`, `pack_score`, `pack_diff`, `pack_citations`, `pack_entities`, `pack_search`, `pack_brief`, `graph_status`, `graph_query`, `graph_neighbors`, `validate_policy`, `serve_pack_status`
  - Write: `render_url`, `ensure_docs`, `parallel_context_pack`, `discover_sources`, `fetch_discovered_sources`, `extract_pack`, `map_sources`, `crawl_pack`, `research_pack`, `entities_pack`, `parallel_api_pack`, `refresh_pack`, `audit_pack`, `answer_pack`, `pack_prepare`, `graph_build`, `graph_refresh`, `export_pack`, `add_source`, `remove_source`
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

## Prerequisite

The plugin wraps the `docpull` CLI; install it with the `[mcp]` extra so the
MCP server is available:

```bash
pip install 'docpull[mcp]'          # or: pipx install 'docpull[mcp]'
                                    #     uv tool install 'docpull[mcp]'
docpull --version                   # should print 5.0.0 or newer
docpull mcp --help                  # confirm the MCP subcommand is wired
```

The plain `pip install docpull` works for CLI use but does **not** include the
`mcp` Python package — `docpull mcp` will exit with "requires the 'mcp'
package". Always install with `[mcp]` for plugin use.

## Install In Codex

Install this plugin from the configured marketplace or local plugin source. The plugin starts the `docpull mcp` stdio server and makes the `docpull-research` skill available.

## Install In Claude Code

In Claude Code:

```
/plugin marketplace add raintree-technology/docpull
/plugin install docpull@docpull
```

The MCP server starts automatically. The slash commands and skill activate when you ask Claude about a specific source.

## 60-second demo

```
> /web-add fastapi
[fetches the FastAPI docs in ~15s; ~400 pages, full-text indexed locally]

> How does FastAPI handle dependency injection scoping?
[The agent reaches for grep_docs(library="fastapi", pattern="depend"), pulls the
 relevant section, and answers with attribution to the cached source file]
```

## Built-in source aliases

These are fetchable by name without any URL setup: `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `parallel`, `langchain`, `supabase`, `drizzle`, `prisma`.

For anything else, pass an HTTPS URL: `/web-add https://www.python.org/blogs/`.

## Where fetched Markdown is cached

By default, fetched Markdown lives under `$XDG_DATA_HOME/docpull-mcp/docs/` (or `~/.local/share/docpull-mcp/docs/` on macOS/Linux). Override with `DOCPULL_DOCS_DIR` if you want it somewhere else (e.g. one cache per project).

## Privacy

- 100% local. No telemetry. No remote services.
- The plugin only sends HTTP requests to the URLs you ask it to fetch.
- The User-Agent is `docpull/<version> (+https://github.com/raintree-technology/docpull)` — public, identifiable, robots.txt-respecting.

## Troubleshooting

| Symptom                                     | Fix |
|---------------------------------------------|-----|
| MCP tools missing after install             | Run `docpull mcp --help`. If it errors with "requires the 'mcp' package", reinstall with `pip install 'docpull[mcp]'`. |
| `/web-add fastapi` says "unknown source"    | Run `mcp__docpull__list_sources()` to see current aliases. Use a URL instead. |
| Slow first fetch                            | Normal — first crawl populates the cache. Subsequent runs hit the conditional-GET cache (~70 ms time-to-first-result). |
| Want to refresh stale sources               | `mcp__docpull__ensure_docs(source="<alias>", force=true)`. |

## Roadmap

- Per-project source cache directory, `/web-skill <source>` for generating skill scaffolds from fetched sources, and a `web-researcher` subagent for parallel multi-source research.

## License

MIT — same as docpull itself. Source: <https://github.com/raintree-technology/docpull>.
