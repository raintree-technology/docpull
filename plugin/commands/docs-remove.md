---
description: Remove a user-defined source alias from sources.yaml, optionally deleting its cached docs.
argument-hint: <library> [--keep-cache]
allowed-tools: mcp__docpull__remove_source, mcp__docpull__list_indexed
---

# Remove a docs source

The user wants to remove a previously-added source. By default this also deletes the cached docs to free disk and avoid stale answers.

User input: **$ARGUMENTS**

## How to handle the input

Parse `$ARGUMENTS` as:

- **First token = library name** (the alias to remove).
- **Optional `--keep-cache` flag** = remove the alias from the user registry but leave the cached `.md` files on disk.

If empty: reply `Usage: /docs-remove <library> [--keep-cache]. Run /docs-list to see what's cached.` and stop.

## Workflow

1. **Default (no flag): remove alias AND delete cache.**
   Call `remove_source(name=<library>, delete_cache=true)`. The MCP tool refuses to remove builtins (`react`, `nextjs`, etc.) — pass that error through to the user with the suggestion in step 3.

2. **`--keep-cache` flag: remove alias only.**
   Call `remove_source(name=<library>, delete_cache=false)`. The cached docs stay; `/docs-search <library>` will keep working until the user runs this without the flag.

3. **If the tool returns "is a builtin source"**:
   Tell the user that builtins can't be removed but they can be shadowed with a custom URL via `/docs-add` (or by editing `sources.yaml` directly).

4. **If the tool returns the no-op response** (no user source AND no cache to delete): tell the user there was nothing to remove. Don't error.

## Output

One line: confirm what was removed (alias only, cache only, both, or nothing). The MCP tool's response is already specific — relay it.

## Don't

- Don't run `rm -rf` via Bash; the MCP tool's `delete_cache=true` does the safe path-validated deletion.
- Don't call this on builtins thinking force will help — there's no force flag for removal by design.
