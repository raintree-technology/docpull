---
description: Fetch a web source and make its Markdown searchable in this session. Accepts a built-in alias (e.g. "react"), an HTTPS URL, or "name url" to register a custom alias.
argument-hint: <alias> | <https-url> | <name> <https-url>
allowed-tools: mcp__docpull__ensure_docs, mcp__docpull__add_source, mcp__docpull__list_sources
---

# Add a source to this session

The user wants to add a source to docpull's local Markdown index so it's searchable later via `/docs-search` (or directly via the `grep_docs` MCP tool).

User input: **$ARGUMENTS**

## How to handle the input

Inspect `$ARGUMENTS`:

1. **Empty or missing.** Reply with a one-line usage hint and stop:
   `Usage: /docs-add <alias>, /docs-add <https-url>, or /docs-add <name> <https-url>. Run /docs-list to see what's already cached.`

2. **One token, no URL scheme** (e.g. `react`, `fastapi`).
   - Treat as a built-in alias. Call `ensure_docs(source="<alias>")`. The default `rag` profile is right for most cases — only override if the user mentioned a specific profile.
   - If the alias is unknown, the tool will return an error listing available aliases. In that case call `list_sources()` and suggest the closest match by edit distance, or recommend running `/docs-add <name> <url>` with the source URL.

3. **One token, an HTTPS URL** (starts with `https://`).
   - Call `list_sources()` first so you can detect both built-in aliases and user-defined aliases, including sources that have not been fetched yet.
   - Auto-derive an alias name from the hostname:
     1. Take the hostname.
     2. Strip a leading `docs.` or `www.` if present.
     3. Take the first dot-separated label.
     4. Lowercase it.
     5. Examples: `https://docs.fastapi.tiangolo.com` → `fastapi`; `https://nextjs.org/docs` → `nextjs`; `https://example.com/api` → `example`.
   - If the derived name already appears in `list_sources()`, tell the user and suggest the explicit `/docs-add <name> <url>` form so they pick a unique name. Do not call `add_source` for a derived name that already exists.
   - Otherwise call `add_source(name=<derived>, url=<url>)` to register, then `ensure_docs(source=<derived>)` to fetch.

4. **Two tokens, second is an HTTPS URL** (`<name> <url>`).
   - Validate the name is a sensible alias (alnum + `_ . -`, ≤128 chars). If not, ask for a cleaner name.
   - Call `add_source(name=<name>, url=<url>)`. This intentionally updates an existing user-defined alias with the same name. If it returns "is a builtin source", tell the user that `add_source` refuses to shadow builtins by default (the agent shouldn't pass `force=true` here without explicit user consent).
   - Then call `ensure_docs(source=<name>)` to fetch.

## After it succeeds

Report a one-line summary:
- Source name (alias used).
- Pages fetched (from the `ensure_docs` response — pages_fetched / pages_skipped / pages_failed).
- Suggest the next step: `/docs-search <pattern> [source]` or ask the agent to grep for something specific.

## After it fails

Show the error in plain language. Common cases:
- **Unknown built-in alias** → list a few suggestions from `list_sources`.
- **URL rejected** (HTTP, localhost, private IP) → tell the user docpull is HTTPS-only by design and won't fetch internal hosts; suggest a public source URL.
- **`add_source` refused a builtin** → tell the user the alias collides with a built-in; pick a different name.
- **Network / 4xx / 5xx during `ensure_docs`** → show the URL and status code; suggest checking network, the URL itself, or trying a different public path.

Do not use any tools beyond the ones listed in `allowed-tools`. Do not send filler messages while the fetch is running — let the tool output speak for itself.
