---
description: Fetch documentation for a library and make it searchable in this session. Accepts a built-in alias (e.g. "react") or an HTTPS URL.
argument-hint: <library-alias-or-url>
allowed-tools: mcp__docpull__ensure_docs, mcp__docpull__list_sources, mcp__docpull__list_indexed, mcp__docpull__fetch_url, Bash(docpull:*)
---

# Add docs to this session

The user wants to add documentation to docpull's local index so it's searchable later via `/docs-search` (or directly via the `grep_docs` MCP tool).

User input: **$ARGUMENTS**

## How to handle the input

Inspect `$ARGUMENTS`:

1. **Empty or missing.** Reply with a one-line usage hint and stop:
   `Usage: /docs-add <alias-or-url>. Run /docs-list to see what's already cached, or call list_sources to see built-in aliases.`

2. **Looks like an HTTPS URL** (starts with `https://`).
   - Run `docpull "<URL>"` via Bash to crawl and cache the whole site. This is the right path because `fetch_url` only handles a single page; `docpull` (the CLI) does discovery + crawl and writes into the same docs directory the MCP server reads from.
   - Stream/show progress to the user.
   - When done, call `list_indexed` to confirm the new entry shows up.

3. **Anything else** — treat as a built-in alias (e.g. `react`, `fastapi`, `nextjs`).
   - Call `ensure_docs(source="<alias>")`. Default profile (`rag`) is right for most cases — only override if the user mentioned a specific profile.
   - If the alias is unknown, the tool will return an error. In that case, call `list_sources()` and suggest the closest match by edit distance, or recommend running `/docs-add` with the canonical URL instead.

## After it succeeds

Report a one-line summary:
- Library name (alias or hostname for URL fetches).
- Pages fetched, cache hits if any, total time (these come back in the tool result).
- Suggest the next step: `/docs-search <pattern>` or ask Claude to grep for something specific.

## After it fails

Show the error in plain language. Common cases:
- **Unknown alias** → list a few suggestions from `list_sources`.
- **`docpull` not on PATH or MCP subcommand missing** → tell the user to run `pip install 'docpull[mcp]'` (or `pipx install 'docpull[mcp]'` / `uv tool install 'docpull[mcp]'`) — the `[mcp]` extra is required for the plugin's MCP server to start.
- **Network / 4xx / 5xx** → show the URL and status code; suggest checking network, the URL itself, or trying a different docs path.

Do not use any tools beyond the ones listed in `allowed-tools`. Do not send filler messages while the fetch is running — let the tool output speak for itself.
