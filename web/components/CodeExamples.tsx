"use client";

import { useState, useCallback, memo } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";

const examples = [
  {
    id: "default",
    name: "Default",
    code: `docpull https://docs.stripe.com`,
    output: `./docs/authentication.md:

---
title: "Authentication"
source: https://docs.stripe.com/authentication
---

# Authentication

The Stripe API uses API keys to authenticate requests.
You can view and manage your API keys in the Stripe
Dashboard.

Test mode secret keys have the prefix sk_test_ and live
mode secret keys have the prefix sk_live_...`,
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
    code: `docpull https://sdk.vercel.ai -o .claude/skills/vercel-ai`,
    output: `.claude/skills/vercel-ai/
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
      {/* Input */}
      <div className="relative group">
        <div className="text-xs text-muted-foreground mb-2">Input</div>
        <pre className="p-4 glass rounded-xl overflow-x-auto text-xs sm:text-sm">
          <code className="whitespace-pre">{code}</code>
        </pre>
        <button
          onClick={handleCopy}
          className="absolute top-8 right-3 p-1.5 rounded-lg glass opacity-100 sm:opacity-0 sm:group-hover:opacity-100 hover:bg-foreground/5 transition-all"
          aria-label={copied ? "Copied" : "Copy code"}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </button>
      </div>

      {/* Output */}
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
  const [active, setActive] = useState<string>("default");
  const activeExample = examples.find((e) => e.id === active);

  const handleTabClick = useCallback((id: string) => {
    setActive(id);
  }, []);

  return (
    <section id="examples" className="py-16 sm:py-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 sm:mb-12 text-center sm:text-left">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            <span className="bg-background/50 px-1 rounded">Examples</span>
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Real commands, real output — from one-shot fetches to Claude Code skills.
          </p>
        </div>

        <div className="flex flex-wrap justify-center sm:justify-start gap-2 mb-6">
          {examples.map((example) => (
            <button
              key={example.id}
              onClick={() => handleTabClick(example.id)}
              className={cn(
                "px-3 py-1.5 text-xs sm:text-sm rounded-md transition-all duration-200",
                active === example.id
                  ? "bg-foreground text-background"
                  : "glass text-muted-foreground hover:text-foreground",
              )}
            >
              {example.name}
            </button>
          ))}
        </div>

        {activeExample && (
          <CodeBlock code={activeExample.code} output={activeExample.output} />
        )}
      </div>
    </section>
  );
}
