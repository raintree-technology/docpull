export const dynamic = "force-static";

const body = `---
name: docpull-research
description: Use the docpull MCP tools to ground answers in real documentation when the user asks about a specific library, framework, SDK, API, or pasted docs URL.
allowed-tools: mcp__docpull__list_indexed, mcp__docpull__list_sources, mcp__docpull__ensure_docs, mcp__docpull__grep_docs, mcp__docpull__read_doc, mcp__docpull__fetch_url
---

# docpull research

Ground library and framework answers in fetched documentation instead of model recall.

## When to use this skill

Activate when:
- the user names a specific library, framework, SDK, or API
- the question is version-sensitive or likely to drift
- the user pastes a docs URL
- a wrong answer would cause implementation churn

Do not activate for:
- general programming explanations
- the user's own codebase
- highly stable standard-library questions

## Workflow

1. Check what is already cached with \`list_indexed\`.
2. If the library is cached, search it with \`grep_docs\`.
3. Use \`read_doc\` for line-level follow-up context.
4. If the library is not cached:
   - use \`ensure_docs\` for a built-in alias
   - use \`fetch_url\` for a single pasted page
   - otherwise ask for the docs URL once; do not crawl unrelated docs speculatively
5. Answer with attribution to the fetched source.

## Guidance

- Prefer the docs over memory for fast-moving libraries.
- Do not over-fetch unrelated libraries.
- Broaden a search once before concluding the docs do not cover the topic.
- Say once that the answer is grounded in the docs, then stay concise.

## Built-in aliases

These aliases can be passed to \`ensure_docs(source=...)\` without additional setup: \`react\`, \`nextjs\`, \`tailwindcss\`, \`vite\`, \`hono\`, \`fastapi\`, \`express\`, \`anthropic\`, \`openai\`, \`langchain\`, \`supabase\`, \`drizzle\`, \`prisma\`.
`;

export function GET() {
  return new Response(body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
