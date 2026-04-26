---
description: Re-fetch a cached library, ignoring the 7-day cache. Use when docs have been updated upstream.
argument-hint: <library>
allowed-tools: mcp__docpull__ensure_docs, mcp__docpull__list_indexed
---

# Refresh cached docs

Force-refetch a library that's already cached. The default `ensure_docs` honors a 7-day cache; this command bypasses it.

User input: **$ARGUMENTS**

## Workflow

1. Parse `$ARGUMENTS` as a single library name. If empty: reply `Usage: /docs-refresh <library>. Run /docs-list to see what's cached.` and stop.

2. Call `ensure_docs(source=<library>, force=true)`. The tool will re-crawl the source (using whatever URL the alias resolves to) and overwrite the cached `.md` files in place.

3. **If the alias is unknown**: pass through the tool's error. Suggest `/docs-add <library>` if it's a built-in or `/docs-add <name> <url>` if not.

4. After success, report a one-line summary using the tool's response (pages fetched / skipped / failed).

## When to use this vs `/docs-add`

- `/docs-add <name>` — first time fetching, OR when the cache is fresh and you want to use it.
- `/docs-refresh <name>` — already cached but you want the latest. Don't run this every time you search; the conditional-GET cache makes it cheap, but it still hits the network for every page.

## Don't

- Don't loop this across all cached libraries unprompted. If the user wants a global refresh, ask first.
- Don't pass `force=true` to `ensure_docs` from any other command — that's what this command is for.
