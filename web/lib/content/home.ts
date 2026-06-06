export const heroTerminalLines = [
  { type: "command", content: "docpull https://stripe.com/pricing" },
  { type: "output", content: "" },
  { type: "dim", content: "Discovering URLs..." },
  { type: "normal", content: "Found 38 pages" },
  { type: "dim", content: "Fetching with RAG profile" },
  { type: "normal", content: "[=============================] 38/38" },
  { type: "output", content: "" },
  { type: "success", content: "Done in 11s. Saved 1.8 MB to ./docs" },
] as const;

export const heroHighlights = [
  "Markdown with frontmatter",
  "Dedup before disk writes",
  "Strict network guardrails",
] as const;

export const heroMetrics = [
  { value: "No hosted crawler", label: "Runs where your files live" },
  { value: "Deterministic output", label: "Stable enough for agents" },
  { value: "Human-readable", label: "Inspect the Markdown directly" },
] as const;

export const featuredFeatures = [
  {
    title: "Markdown you can actually reuse",
    description:
      "Every page lands as plain Markdown with frontmatter for title, source URL, heading outline, and description. It is ready for search, embeddings, or a checked-in local archive whether the source was docs, a help center, a blog, or a product page.",
    snippet: `---
title: "Authentication"
source: https://docs.stripe.com/authentication
description: "Authenticate requests with your API key."
---

# Authentication`,
    points: [
      "Keeps source metadata attached to the file.",
      "Drops straight into RAG pipelines or skill folders.",
    ],
    className: "lg:col-span-7",
  },
  {
    title: "Dedup before disk fills up",
    description:
      "docpull hashes pages while they stream in. If the content is the same, it gets skipped before you pay for writes, indexing, or another noisy diff.",
    snippet: `sha256(page) -> known
status       -> skipped
write        -> no-op`,
    points: ["Bounded memory per page.", "Cheap re-runs stay cheap."],
    className: "lg:col-span-5",
  },
] as const;

export const supportingFeatures = [
  {
    title: "Network rules stay enforced",
    description:
      "HTTPS only, robots.txt aware, and SSRF guarded with DNS pinning at connect time.",
  },
  {
    title: "Re-fetches stay selective",
    description:
      "Cached pages send conditional requests so unchanged pages do not transfer again.",
  },
  {
    title: "Partial crawls are first-class",
    description:
      "Use `--include-paths` and `--exclude-paths` when you only want one section of a site.",
  },
] as const;

export const profiles = [
  {
    name: "RAG",
    description: "Deduped, metadata-rich output for LLMs and vector stores.",
    example: "docpull URL --profile rag",
    accent: "Good default for embeddings and search.",
  },
  {
    name: "Mirror",
    description: "Full archive with caching, resume support, and hierarchical paths.",
    example: "docpull URL --profile mirror",
    accent: "Best when you want the whole site on disk.",
  },
  {
    name: "Quick",
    description: "50 pages, depth 2. For testing and sampling.",
    example: "docpull URL --profile quick",
    accent: "Use this when you need a fast sample first.",
  },
  {
    name: "LLM",
    description:
      "Token-aware NDJSON for LLM ingestion: chunked, deduped, skips JS-only pages unless strict mode is enabled.",
    example: "docpull URL --profile llm --stream | jq .",
    accent: "Purpose-built for ingestion pipelines.",
  },
] as const;

export const codeExamples = [
  {
    id: "default",
    name: "Default",
    code: `docpull https://stripe.com/pricing`,
    output: `./docs/pricing.md:

---
title: "Pricing"
source: https://stripe.com/pricing
---

# Pricing

Choose the plan that matches your business.
Usage-based billing starts when you move past
the free tier.

Enterprise support, SLAs, and procurement options
are available for larger teams...`,
  },
  {
    id: "rag",
    name: "RAG",
    code: `docpull https://docs.anthropic.com --profile rag`,
    output: `./docs/messages.md:

---
title: "Messages"
source: https://docs.anthropic.com/en/api/messages
description: "Send a structured list of input messages and get the model's response."
---

# Messages

Send messages to Claude using the Messages API...`,
  },
  {
    id: "skills",
    name: "Claude Code",
    code: `docpull https://sdk.vercel.ai --skill vercel-ai`,
    output: `.claude/skills/vercel-ai/
├── SKILL.md
├── getting-started.md
├── streaming.md
├── tools.md
└── providers.md

./.claude/skills/vercel-ai/getting-started.md:

---
title: "Getting Started"
source: https://sdk.vercel.ai/docs/getting-started
---

# Getting Started

Install the Vercel AI SDK to build AI-powered applications...`,
  },
  {
    id: "python",
    name: "Python",
    code: `from docpull import Fetcher, DocpullConfig

config = DocpullConfig(url="https://docs.example.com")
async with Fetcher(config) as fetcher:
    async for event in fetcher.run():
        print(f"{event.current}/{event.total}: {event.url}")`,
    output: `1/124: https://docs.example.com/intro
2/124: https://docs.example.com/quickstart
3/124: https://docs.example.com/api/overview
...
124/124: https://docs.example.com/changelog

Completed: 124 pages, 4.2 MB`,
  },
] as const;
