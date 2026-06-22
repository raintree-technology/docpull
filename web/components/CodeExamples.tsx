import { CodeTabs, LandingSection, type CodeTab } from "@/components/landing";

const examples = [
  {
    id: "default",
    name: "Website",
    code: `docpull https://www.python.org/blogs/ -o ./python-news`,
    output: `./python-news/index.md:

---
title: "Blogs"
source: https://www.python.org/blogs/
---

# Blogs

News from the Python Software Foundation, Python core
developers, and the wider Python community.

Recent posts include release notes, governance updates,
events, and project announcements...`,
  },
  {
    id: "rag",
    name: "RAG",
    code: `docpull https://docs.anthropic.com --profile rag`,
    output: `./anthropic-api/messages.md:

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

./.claude/skills/vercel-ai/SKILL.md:

---
name: vercel-ai
description: "Vercel AI SDK source reference"
---

./.claude/skills/vercel-ai/getting-started.md:

---
title: "Getting Started"
source: https://sdk.vercel.ai/docs/getting-started
---

# Getting Started

Install the Vercel AI SDK to build AI-powered applications...`,
  },
  {
    id: "parallel",
    name: "Parallel",
    code: `docpull parallel context-pack "Track Parallel Web Systems API sources" \\
  --query "Parallel Search API docs" \\
  --query "Parallel Extract API docs" \\
  --include-domain parallel.ai \\
  --include-domain docs.parallel.ai \\
  --exclude-domain onparallel.com \\
  --extract-limit 3 \\
  --max-estimated-cost 0.05 \\
  --task-brief \\
  --output-dir ./packs/parallel-sources

docpull pack score ./packs/parallel-sources --require-domain parallel.ai
docpull pack sources ./packs/parallel-sources --require-domain docs.parallel.ai`,
    output: `./packs/parallel-sources/
├── AGENT_CONTEXT.md
├── documents.ndjson
├── corpus.manifest.json
├── parallel.pack.json
├── sources.md
├── brief.md
└── sources/
    ├── 01-parallel.md
    └── 02-parallel-documentation.md

AGENT_CONTEXT.md:
- Load documents.ndjson for chunked records
- Use sources.md for source order
- Review Source Scores before loading low-signal URLs
- Inspect parallel.pack.json for IDs, usage, warnings, and errors

parallel.pack.json:
{
  "provider": "parallel",
  "workflow": "context-pack",
  "session_id": "session_example_parallel_context_pack",
  "estimated_cost_usd": 0.013,
  "request_options": {
    "source_policy": {
      "include_domains": ["parallel.ai", "docs.parallel.ai"],
      "exclude_domains": ["onparallel.com"]
    }
  },
  "extract_result_count": 2,
  "extract_error_count": 1,
  "artifacts": {
    "agent_context": "AGENT_CONTEXT.md",
    "documents_ndjson": "documents.ndjson",
    "sources": "sources.md"
  }
}

Pack score: 95/100 (excellent; one extract error preserved)
Source scores: 2 sources -> source.scores.json`,
  },
  {
    id: "python",
    name: "Python",
    code: `from docpull import Fetcher, DocpullConfig

config = DocpullConfig(url="https://example.com/blog")
async with Fetcher(config) as fetcher:
    async for event in fetcher.run():
        print(f"{event.current}/{event.total}: {event.url}")`,
    output: `1/124: https://example.com/blog
2/124: https://example.com/blog/company-update
3/124: https://example.com/blog/product-launch
...
124/124: https://example.com/blog/changelog

Completed: 124 pages, 4.2 MB`,
  },
] as const satisfies readonly CodeTab[];

export default function CodeExamples() {
  return (
    <LandingSection
      id="examples"
      title="Examples"
      description="See the command, then see the artifact it leaves behind."
    >
      <CodeTabs examples={examples} initialId="default" />
    </LandingSection>
  );
}
