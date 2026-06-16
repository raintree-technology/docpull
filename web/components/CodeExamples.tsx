"use client";

import { useState, useCallback, memo, type KeyboardEvent } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";

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

./.claude/skills/vercel-ai/SKILL.md:

---
name: vercel-ai
description: "Vercel AI SDK documentation"
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
    code: `docpull parallel context-pack "Track Parallel Web Systems API docs" \\
  --query "Parallel Search API docs" \\
  --query "Parallel Extract API docs" \\
  --include-domain parallel.ai \\
  --include-domain docs.parallel.ai \\
  --exclude-domain onparallel.com \\
  --extract-limit 3 \\
  --max-estimated-cost 0.05 \\
  --task-brief \\
  --output-dir ./packs/parallel-docs

docpull pack score ./packs/parallel-docs --require-domain parallel.ai
docpull pack sources ./packs/parallel-docs --require-domain docs.parallel.ai`,
    output: `./packs/parallel-docs/
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
] as const;

const CodeBlock = memo(function CodeBlock({
  code,
  output,
}: {
  code: string;
  output: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [code]);

  return (
    <div className="space-y-4">
      <div className="relative group">
        <div className="text-xs text-muted-foreground mb-2">Input</div>
        <pre className="p-4 glass rounded-xl overflow-x-auto text-xs sm:text-sm">
          <code className="whitespace-pre">{code}</code>
        </pre>
        <button
          type="button"
          onClick={handleCopy}
          className="absolute top-7 right-2 min-h-11 min-w-11 p-2 rounded-lg glass opacity-100 sm:opacity-0 sm:group-hover:opacity-100 hover:bg-foreground/5 transition-all"
          aria-label={copied ? "Copied" : "Copy code"}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </button>
      </div>

      <div>
        <div className="text-xs text-muted-foreground mb-2">Output</div>
        <pre className="p-4 glass rounded-xl overflow-auto max-h-80 text-xs sm:text-sm text-muted-foreground">
          <code className="whitespace-pre">{output}</code>
        </pre>
      </div>
    </div>
  );
});

export default function CodeExamples() {
  const [activeExampleId, setActiveExampleId] = useState<string>("default");
  const activeExample = examples.find((e) => e.id === activeExampleId);
  const activeIndex = examples.findIndex((e) => e.id === activeExampleId);

  const handleTabClick = useCallback((id: string) => {
    setActiveExampleId(id);
  }, []);

  const handleTabKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
      }

      event.preventDefault();
      const lastIndex = examples.length - 1;
      const nextIndex =
        event.key === "Home"
          ? 0
          : event.key === "End"
            ? lastIndex
            : event.key === "ArrowRight"
              ? activeIndex === lastIndex
                ? 0
                : activeIndex + 1
              : activeIndex === 0
                ? lastIndex
                : activeIndex - 1;
      const nextId = examples[nextIndex].id;
      setActiveExampleId(nextId);
      document.getElementById(`example-tab-${nextId}`)?.focus();
    },
    [activeIndex],
  );

  return (
    <section id="examples" className="py-16 sm:py-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 sm:mb-12 text-center sm:text-left">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            <span>Examples</span>
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground">
            See the command, then see the artifact it leaves behind.
          </p>
        </div>

        <div
          className="flex flex-wrap justify-center sm:justify-start gap-2 mb-6"
          role="tablist"
          aria-label="Code example categories"
        >
          {examples.map((example) => (
            <button
              type="button"
              key={example.id}
              id={`example-tab-${example.id}`}
              onClick={() => handleTabClick(example.id)}
              onKeyDown={handleTabKeyDown}
              role="tab"
              aria-selected={activeExampleId === example.id}
              aria-controls={`example-panel-${example.id}`}
              tabIndex={activeExampleId === example.id ? 0 : -1}
              className={cn(
                "min-h-11 px-3 py-2 text-xs sm:text-sm rounded-md transition-all duration-200",
                activeExampleId === example.id
                  ? "bg-foreground text-background"
                  : "glass text-muted-foreground hover:text-foreground",
              )}
            >
              {example.name}
            </button>
          ))}
        </div>

        {activeExample && (
          <div
            id={`example-panel-${activeExample.id}`}
            role="tabpanel"
            aria-labelledby={`example-tab-${activeExample.id}`}
          >
            <CodeBlock code={activeExample.code} output={activeExample.output} />
          </div>
        )}
      </div>
    </section>
  );
}
