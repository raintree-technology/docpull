# docpull plugin for Claude Code

Pull docs from any URL into Claude Code. Local, fast, no API keys.

## What you get

- **MCP server** (8 tools):
  - Read: `fetch_url`, `list_sources`, `list_indexed`, `grep_docs`, `read_doc`
  - Write: `ensure_docs`, `add_source`, `remove_source`
  - All read tools advertise `readOnlyHint` so hosts that auto-approve safe tools won't prompt for them.
- **Slash commands**:
  - `/docs-add <alias-or-url>` — fetch a library into the local index.
  - `/docs-search <pattern> [library]` — regex-search cached docs and pull surrounding context for the top hits.
  - `/docs-list` — show what's cached, with last-fetched age.
  - `/docs-refresh <library>` — bypass the 7-day cache and re-fetch.
  - `/docs-remove <library> [--keep-cache]` — drop a user alias and its cached docs.
- **Meta-skill** (`docpull-research`): teaches Claude *when* to reach for docpull — so you don't have to remember the tool exists every time you ask about a library.

## Prerequisite

The plugin wraps the `docpull` CLI; install it with the `[mcp]` extra so the
MCP server is available:

```bash
pip install 'docpull[mcp]'          # or: pipx install 'docpull[mcp]'
                                    #     uv tool install 'docpull[mcp]'
docpull --version                   # should print 2.5.0 or newer
docpull mcp --help                  # confirm the MCP subcommand is wired
```

The plain `pip install docpull` works for CLI use but does **not** include the
`mcp` Python package — `docpull mcp` will exit with "requires the 'mcp'
package". Always install with `[mcp]` for plugin use.

## Install

In Claude Code:

```
/plugin marketplace add raintree-technology/docpull
/plugin install docpull@docpull
```

The MCP server starts automatically. The skill activates when you ask Claude about a specific library.

## 60-second demo

```
> /docs-add fastapi
[fetches the FastAPI docs in ~15s; ~400 pages, full-text indexed locally]

> How does FastAPI handle dependency injection scoping?
[Claude reaches for grep_docs(library="fastapi", pattern="depend"), pulls the
 relevant section, and answers with attribution to the actual docs file]
```

## Built-in library aliases

These are fetchable by name without any URL setup: `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `langchain`, `supabase`, `drizzle`, `prisma`.

For anything else, pass an HTTPS URL: `/docs-add https://docs.your-library.com`.

## Where docs are cached

By default, fetched docs live under `$XDG_DATA_HOME/docpull/docs/` (or `~/.local/share/docpull/docs/` on macOS/Linux). Override with `DOCPULL_DOCS_DIR` if you want them somewhere else (e.g. one cache per project).

## Privacy

- 100% local. No telemetry. No remote services.
- The plugin only sends HTTP requests to the docs URLs you ask it to fetch.
- The User-Agent is `docpull/<version> (+https://github.com/raintree-technology/docpull)` — public, identifiable, robots.txt-respecting.

## Troubleshooting

| Symptom                                     | Fix |
|---------------------------------------------|-----|
| MCP tools missing after install             | Run `docpull mcp --help`. If it errors with "requires the 'mcp' package", reinstall with `pip install 'docpull[mcp]'`. |
| `/docs-add fastapi` says "unknown source"   | Run `mcp__docpull__list_sources()` to see current aliases. Use a URL instead. |
| Slow first fetch                            | Normal — first crawl populates the cache. Subsequent runs hit the conditional-GET cache (~70 ms time-to-first-result). |
| Want to refresh stale docs                  | `mcp__docpull__ensure_docs(source="<alias>", force=true)`. |

## Roadmap

- **v0.3.0**: per-project docs cache directory, `/docs-skill <library>` for generating Claude Code skill scaffolds from fetched libraries, `docs-researcher` subagent for parallel multi-library research.

## License

MIT — same as docpull itself. Source: <https://github.com/raintree-technology/docpull>.
