---
description: Search fetched Markdown by regex and pull surrounding context for the best hits. Optionally restrict to one source alias.
argument-hint: <pattern> [source]
allowed-tools: mcp__docpull__grep_docs, mcp__docpull__read_doc, mcp__docpull__list_indexed
---

# Search fetched Markdown

Compatibility alias: prefer `/web-search` for new web-source workflows.

The user wants to search Markdown that has already been pulled by `/docs-add` (or `ensure_docs`). This composes two MCP tools: `grep_docs` finds matching files; `read_doc` pulls more context around the top hits so the answer is grounded, not just a list of file:line references.

User input: **$ARGUMENTS**

## How to handle the input

Parse `$ARGUMENTS` as:

- **First whitespace-separated token = pattern** (regex; can be quoted to include spaces).
- **Optional second token = source alias** to restrict the search to one fetched source. Pass it as the `library` argument when calling `grep_docs`.

If empty: reply `Usage: /docs-search <pattern> [source]. Run /docs-list to see what's cached.` and stop.

## Workflow

1. **Find candidates.** Call `grep_docs(pattern=<pattern>, library=<source if given>, limit=10, context=2)`. The tool returns the top files ranked by match density with two lines of context above and below each hit.

2. **Read deeper context for the top 2–3 files.** For each of the top files in the grep result (max 3), call `read_doc(library=<lib>, path=<path>, line_start=<top-hit-line - 30>, line_end=<top-hit-line + 30>)` to pull a ~60-line window. Skip this step if the user's pattern is very narrow (a literal symbol name) and the grep context already answers the question.

3. **If grep returns nothing**:
   - If a source was specified, run `list_indexed()` to confirm the source is actually cached. If it isn't, suggest `/docs-add <source>` and stop.
   - If no library was specified, broaden the pattern *once* (e.g. add common prefixes/suffixes, drop word boundaries) and retry. If still nothing, surface the gap to the user.

4. **If `grep_docs` says "search timed out"**: the pattern is likely catastrophic. Suggest a tighter pattern (no nested quantifiers, anchor with `\b`).

## Output

- Lead with the synthesized answer to the user's likely question, grounded in what you read.
- Cite each source as `source/path.md:line` so the user can verify.
- Don't dump the full grep output unless the user asked for it — the goal is an answer, not a search log.

## Don't

- Don't call `ensure_docs` from this command. If the source isn't cached, send the user to `/docs-add` instead — auto-fetching from a search command surprises people.
- Don't re-`read_doc` the same file twice in one call.
- Don't use any tool not in `allowed-tools`.
