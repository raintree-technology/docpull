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
└── models.md

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
    id: "pack",
    name: "v3 Pack",
    code: `docpull https://docs.example.com -o ./packs/docs
docpull pack validate ./packs/docs --level raw
docpull pack prepare ./packs/docs --eval-grade
docpull pack validate ./packs/docs --level eval
docpull export ./packs/docs --format openai-vector-jsonl -o ./exports/docs.jsonl`,
    output: `./packs/docs/
├── documents.ndjson
├── corpus.manifest.json
├── acquisition.routes.json
├── sources.md
├── context.lock.json
├── coverage.report.json
├── citation.index.json
├── pack.score.json
├── pack.audit.json
├── rights.manifest.json
├── provenance.graph.json
└── PACK_CARD.md

pack validate:
{
  "schema_version": "3",
  "level": "eval",
  "ok": true,
  "required_sidecars": "present",
  "citation_index": "valid"
}

./exports/docs.jsonl:
{"custom_id":"S1.1","body":{"input":"..."}}`,
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
