# docpull plugin for Claude Code

Pull server-rendered web content from any URL into Claude Code. Local, fast, no API keys.

This package is the agent wrapper around docpull's local MCP server. Claude Code
can install it as a Claude plugin; Codex can package the same `skills/` folder
through `.codex-plugin/plugin.json`. Cursor and Claude Desktop connect
`docpull mcp` directly.

This repo also carries host-native project guidance for the direct-MCP paths:
Claude Code can read `.mcp.json` plus `CLAUDE.md`; Cursor reads
`.cursor/mcp.json` plus `.cursor/rules/docpull-research.mdc`; Codex reads
`AGENTS.md`, supports project `.codex/config.toml` in trusted repos, and can
discover repo skills from `.agents/skills`.

## What you get

- **MCP server** (8 tools):
  - Read: `fetch_url`, `list_sources`, `list_indexed`, `grep_docs`, `read_doc`
  - Write: `ensure_docs`, `add_source`, `remove_source`
  - All read tools advertise `readOnlyHint` so hosts that auto-approve safe tools won't prompt for them.
- **MCP prompts**:
  - `/mcp__docpull__docs_add <alias-or-url>` — fetch a built-in alias, or register an HTTPS docs URL and then fetch it into the local index.
  - `/mcp__docpull__docs_search <pattern> [library]` — regex-search cached docs and pull surrounding context for the top hits.
  - `/mcp__docpull__docs_list` — show what's cached, with last-fetched age.
  - `/mcp__docpull__docs_refresh <library>` — bypass the 7-day cache and re-fetch.
  - `/mcp__docpull__docs_remove <library> [--keep-cache]` — drop a user alias and its cached docs.
- **Meta-skill** (`docpull-research`): teaches Claude *when* to reach for docpull — so you don't have to remember the tool exists every time you ask about a library or web source.

## Prerequisite

The plugin wraps the `docpull` CLI; install it with the `[mcp]` extra so the
MCP server is available:

```bash
pip install 'docpull[mcp]'          # or: pipx install 'docpull[mcp]'
                                    #     uv tool install 'docpull[mcp]'
docpull --version                   # should print 4.0.0 or newer
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

In Codex, the same folder is a Codex plugin source via
`plugin/.codex-plugin/plugin.json`. For direct MCP setup, use:

```bash
codex mcp add docpull -- docpull mcp
```

Or, in a trusted repo, add this to `.codex/config.toml`:

```toml
[mcp_servers.docpull]
command = "docpull"
args = ["mcp"]
```

From this repo, regenerate Codex's project config, repo-scoped skill copy, and
local plugin marketplace with:

```bash
make sync-agent-host-configs
```

For local bundle installs or smoke tests, first generate the self-contained
bundle:

```bash
python scripts/sync_claude_plugin.py
```

Then point Claude Code at `.claude-plugin/`. The bundle is generated from
`plugin/`; the copied plugin payload is not checked into git.

## 60-second demo

```
> /mcp__docpull__docs_add fastapi
[fetches the FastAPI docs in ~15s; ~400 pages, full-text indexed locally]

> How does FastAPI handle dependency injection scoping?
[Claude reaches for grep_docs(library="fastapi", pattern="depend"), pulls the
 relevant section, and answers with attribution to the actual docs file]
```

Older `/docs-add` plugin command wrappers are intentionally not shipped; the
workflows now live with the MCP server so they work through any host that
supports MCP prompts.

## Built-in library aliases

These are fetchable by name without any URL setup: `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `langchain`, `supabase`, `drizzle`, `prisma`.

For anything else, pass an HTTPS URL to the prompt. It derives an alias, writes
that alias to `~/.config/docpull-mcp/sources.yaml`, then calls `ensure_docs`:

`/mcp__docpull__docs_add https://docs.your-library.com`.

## Where docs are cached

By default, fetched docs live under `$XDG_DATA_HOME/docpull-mcp/docs/` (or
`~/.local/share/docpull-mcp/docs/` on macOS/Linux). Override with
`DOCPULL_DOCS_DIR` if you want them somewhere else (e.g. one cache per
project).

## Privacy

- 100% local. No telemetry. No remote services.
- The plugin only sends HTTP requests to the URLs you ask it to fetch.
- The User-Agent is `docpull/<version> (+https://github.com/raintree-technology/docpull)` — public, identifiable, robots.txt-respecting.

## Troubleshooting

| Symptom                                     | Fix |
|---------------------------------------------|-----|
| MCP tools missing after install             | Run `docpull mcp --help`. If it errors with "requires the 'mcp' package", reinstall with `pip install 'docpull[mcp]'`. |
| `/mcp__docpull__docs_add fastapi` says "unknown source" | Run `mcp__docpull__list_sources()` to see current aliases. If the source is not listed, use `/mcp__docpull__docs_add <name> <https-url>` to register it. |
| Slow first fetch                            | Normal — first crawl populates the cache. Subsequent runs hit the conditional-GET cache (~70 ms time-to-first-result). |
| Want to refresh stale docs                  | `mcp__docpull__ensure_docs(source="<alias>", force=true)`. |

## Notes

Direct MCP tools and MCP prompts are the supported workflow. Legacy plugin
command wrappers such as `/docs-add` are intentionally not shipped.

## License

MIT — same as docpull itself. Source: <https://github.com/raintree-technology/docpull>.
