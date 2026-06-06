---
name: docpull-research
description: Use the docpull MCP tools (list_indexed, list_sources, ensure_docs, grep_docs, read_doc, fetch_url) to ground answers in real documentation when the user asks about a specific library, framework, SDK, API surface, version-sensitive tool behavior, or pasted documentation URL. Especially useful for fast-moving libraries and tool ecosystems such as Next.js, FastAPI, LangChain, React, Tailwind, Drizzle, Prisma, Anthropic/OpenAI SDKs, Vercel AI SDK, and Vercel skills.sh / skills CLI docs.
allowed-tools: mcp__docpull__list_indexed, mcp__docpull__list_sources, mcp__docpull__ensure_docs, mcp__docpull__grep_docs, mcp__docpull__read_doc, mcp__docpull__fetch_url
---

# docpull research

Ground library/framework answers in real documentation instead of training-data recall. The cost of one `grep_docs` call is ~50 ms; the cost of giving a confidently wrong answer about a fast-moving API is much higher.

## When to use this skill

**Activate when** the user's question names a specific library, framework, SDK, API surface, or docs-backed tool ecosystem — especially:

- **Fast-moving libraries** where training-data drift is likely: Next.js (App Router), Pydantic v2, LangChain, FastAPI, Anthropic SDK, OpenAI SDK, Drizzle, Prisma, Tailwind v4+, Vercel AI SDK.
- **Version-specific questions** ("how does X work in [library] v[N]").
- **Pasted docs URLs** the user wants explained or referenced.
- **Agent/tooling ecosystems** with live docs or CLIs, including `skills.sh`, `github.com/vercel-labs/skills`, Vercel agent skills, MCP docs, and SDK command references.
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

Use `grep_docs` with a focused regex. Prefer API nouns, method names, command names, or option flags over whole natural-language questions. The library is already on disk, so this is a local search:

```
grep_docs(library="fastapi", pattern="dependency injection", limit=10, context=2)
```

If you want more context around a hit, use `read_doc(library, path, line_start, line_end)`.

### 3. If the source is NOT cached → decide whether to fetch

- **Built-in alias** (the library appears in `list_sources()`): call `ensure_docs(source="<alias>")`. This crawls and indexes the whole library. ~10–30s for typical sites.
- **Arbitrary URL**: call `fetch_url(url=...)` if you only need one page. For a whole site you don't have an alias for, tell the user to run `/mcp__docpull__docs_add <URL>`; the MCP `fetch_url` is single-page only.
- **No alias, user didn't paste a URL**: ask the user once whether they'd like to add the library, and what the docs URL is. Don't fetch speculatively.

### 4. Special case: skills.sh and Vercel skills CLI

For questions about Vercel skills, `skills.sh`, `npx skills`, agent skill installation, or `SKILL.md` structure:

- Treat the docs as version-sensitive. First check `list_indexed` for an existing `skills`, `skills.sh`, or `vercel-labs-skills` source.
- If cached, search for exact commands or flags such as `skills add`, `--agent`, `--skill`, `--copy`, `--yes`, `skills use`, `skills list`, `skills find`, `skills update`, `skills remove`, `SKILL.md`, `frontmatter`, or the named agent.
- If not cached and the user pasted a skills.sh docs page, use `fetch_url` on that page.
- If not cached and no URL was pasted, prefer the official docs page `https://www.skills.sh/docs` for a quick one-page answer. For CLI option details, ask once before crawling the full GitHub repo, or use the official README URL if the user only needs install/use command syntax.
- When giving install commands for this repo, preserve project policy: `npx -y skills add <package> --skill '*' --agent codex --copy --yes`, then remove installer artifacts with `rm -rf .agents skills-lock.json`.
- Mention the security boundary from skills.sh when relevant: public skills can be audited, but users should review skill contents before installing.

### 5. Quote with attribution

When you cite docs, include the source path returned by `grep_docs` / `read_doc` so the user can verify. Example: "Per `fastapi/tutorial/dependencies.md:42`, dependencies declared with `Depends()` are resolved per-request..."

For one-page `fetch_url` answers, name the page URL or page title in the answer. For cached docs, prefer `library/path.md:line` style references.

### 6. Don't over-fetch

- Don't call `ensure_docs` for libraries the user didn't ask about ("while we're here, let me also fetch...").
- Don't crawl the same library twice in one session — `list_indexed` will tell you it's there.
- If `grep_docs` returns nothing useful, broaden the regex once before suggesting the user add more docs.

## Built-in aliases

These are pre-configured and resolvable by `ensure_docs(source=...)` without setup: `react`, `nextjs`, `tailwindcss`, `vite`, `hono`, `fastapi`, `express`, `anthropic`, `openai`, `langchain`, `supabase`, `drizzle`, `prisma`. Run `list_sources()` for the current set.

## Failure modes

- **`ensure_docs` returns "unknown source"**: the alias isn't built-in. Either suggest `/mcp__docpull__docs_add <URL>` or call `list_sources()` and propose a near match.
- **`grep_docs` returns empty**: the pattern is too narrow, or the library doesn't cover the topic. Broaden once, then surface the gap to the user.
- **`fetch_url` cannot read a docs page**: state that docpull could not fetch the page, then use a browser/search fallback only if the host permits it and the question needs current docs.
- **MCP server not responding**: tell the user to run `pip install 'docpull[mcp]'` and verify the plugin's MCP server is healthy. Fall back to answering from training data with an explicit caveat that docs weren't available.

## Tone

When you've grounded an answer in fetched docs, say so once at the start of the answer ("Per the FastAPI docs..."). Don't pad every paragraph with attribution — one source citation up front plus inline file references is enough.
