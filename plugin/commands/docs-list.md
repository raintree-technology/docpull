---
description: List source aliases currently cached locally, with last-fetched age.
allowed-tools: mcp__docpull__list_indexed, mcp__docpull__list_sources
---

# List cached sources

Compatibility alias: prefer `/web-list` for new web-source workflows.

Show what's available to `/docs-search` right now.

## Workflow

1. Call `list_indexed()`. It returns source aliases that have been fetched, with file count and how long ago they were fetched.

2. **If empty**: reply with a one-liner pointing to `/docs-add` and `list_sources` for the built-in alias list. Don't fetch anything.

3. **If non-empty**: render the list as the tool returned it (it's already formatted). Note any sources marked `stale` (older than 7 days) and suggest `/docs-refresh <source>` for those if there are any.

4. If the user is likely going to follow up with a search, suggest `/docs-search <pattern> [source]` once at the bottom.

## Don't

- Don't crawl, fetch, or call `ensure_docs` from this command. It's a read-only listing.
- Don't expand each source's file tree — `list_indexed` summarizes for a reason.
