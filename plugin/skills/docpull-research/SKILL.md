---
name: docpull-research
description: Use the docpull MCP tools (list_indexed, ensure_docs, grep_docs, read_doc, fetch_url) to ground answers in real documentation when the user asks about a specific library, framework, or API — especially for fast-moving libraries (Next.js, FastAPI, LangChain, Pydantic, React, Tailwind, Drizzle, Prisma, Anthropic SDK, etc.) where training data is likely stale or incomplete. Activate on questions like "how do I X in [library]", "what's the API for [framework].[method]", "show me how [library] handles Y", or when a user pastes a docs URL.
allowed-tools: mcp__docpull__list_indexed, mcp__docpull__list_sources, mcp__docpull__ensure_docs, mcp__docpull__grep_docs, mcp__docpull__read_doc, mcp__docpull__fetch_url
---

# docpull research

Ground library/framework answers in real documentation instead of training-data recall. The cost of one `grep_docs` call is ~50 ms; the cost of giving a confidently wrong answer about a fast-moving API is much higher.

## When to use this skill

**Activate when** the user's question names a specific library, framework, SDK, or API surface — especially:

- **Fast-moving libraries** where training-data drift is likely: Next.js (App Router), Pydantic v2, LangChain, FastAPI, Anthropic SDK, OpenAI SDK, Drizzle, Prisma, Tailwind v4+, Vercel AI SDK.
- **Version-specific questions** ("how does X work in [library] v[N]").
- **Pasted docs URLs** the user wants explained or referenced.
- **Code the user is actively writing** against a library, where wrong signatures will cost them debugging time.

**Do NOT activate for**:

- General programming questions ("what's a closure", "explain async/await").
- The user's own codebase — that's what Read/Grep are for.
- Highly stable, well-known stdlib APIs (Python `os`, JavaScript `Array.prototype`).
- Clarifying questions where the answer is trivial from context.

## Workflow

### 1. Check what's already cached

Always start with `list_indexed`. It's free and tells you which libraries you can search immediately without fetching.

```
list_indexed() → ["fastapi (3d ago)", "react (12h ago)", ...]
```

### 2. If the library is cached → search it

Use `grep_docs` with a focused regex. The library is already on disk, so this is a local search:

```
grep_docs(library="fastapi", pattern="dependency injection", limit=10, context=2)
```

If you want more context around a hit, use `read_doc(library, path, line_start, line_end)`.

### 3. If the library is NOT cached → decide whether to fetch

- **Built-in alias** (the library appears in `list_sources()`): call `ensure_docs(source="<alias>")`. This crawls and indexes the whole library. ~10–30s for typical sites.
- **Arbitrary URL**: call `fetch_url(url=...)` if you only need one page. For a whole site you don't have an alias for, tell the user to run `/docs-add <URL>` (which uses the docpull CLI to crawl); the MCP `fetch_url` is single-page only.
- **No alias, user didn't paste a URL**: ask the user once whether they'd like to add the library, and what the docs URL is. Don't fetch speculatively.

### 4. Quote with attribution

When you cite docs, include the source path returned by `grep_docs` / `read_doc` so the user can verify. Example: "Per `fastapi/tutorial/dependencies.md:42`, dependencies declared with `Depends()` are resolved per-request..."

### 5. Don't over-fetch

- Don't call `ensure_docs` for libraries the user didn't ask about ("while we're here, let me also fetch...").
- Don't crawl the same library twice in one session — `list_indexed` will tell you it's there.
- If `grep_docs` returns nothing useful, broaden the regex once before suggesting the user add more docs.

## Built-in aliases

These are pre-configured and resolvable by `ensure_docs(source=...)` without setup: `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `langchain`, `supabase`, `drizzle`, `prisma`. Run `list_sources()` for the current set.

## Failure modes

- **`ensure_docs` returns "unknown source"**: the alias isn't built-in. Either suggest `/docs-add <URL>` or call `list_sources()` and propose a near match.
- **`grep_docs` returns empty**: the pattern is too narrow, or the library doesn't cover the topic. Broaden once, then surface the gap to the user.
- **MCP server not responding**: tell the user to run `pip install docpull` and verify the plugin's MCP server is healthy. Fall back to answering from training data with an explicit caveat that docs weren't available.

## Tone

When you've grounded an answer in fetched docs, say so once at the start of the answer ("Per the FastAPI docs..."). Don't pad every paragraph with attribution — one source citation up front plus inline file references is enough.
