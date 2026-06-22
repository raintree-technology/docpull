"use client";

import { memo, type KeyboardEvent, useCallback, useState } from "react";
import { Check, Copy } from "lucide-react";
import { cn } from "@/lib/utils";

export type CodeTab = {
  id: string;
  name: string;
  code: string;
  output: string;
};

type CodeTabsProps = {
  examples: readonly CodeTab[];
  initialId?: string;
};

const CodeBlock = memo(function CodeBlock({
  code,
  output,
}: {
  code: string;
  output: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [code]);

  return (
    <div className="space-y-4">
      <div className="group relative">
        <div className="mb-2 text-sm font-medium leading-5 text-muted-foreground">
          Input
        </div>
        <pre className="glass overflow-x-auto rounded-lg p-4 text-[13px] leading-6 sm:text-sm">
          <code className="whitespace-pre">{code}</code>
        </pre>
        <button
          type="button"
          onClick={handleCopy}
          className="glass absolute right-2 top-8 min-h-11 min-w-11 rounded-lg p-2 opacity-100 transition-all hover:bg-foreground/5 sm:opacity-0 sm:group-hover:opacity-100"
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
        <div className="mb-2 text-sm font-medium leading-5 text-muted-foreground">
          Output
        </div>
        <pre className="glass max-h-80 overflow-auto rounded-lg p-4 text-[13px] leading-6 text-foreground/80 sm:text-sm">
          <code className="whitespace-pre">{output}</code>
        </pre>
      </div>
    </div>
  );
});

export default function CodeTabs({ examples, initialId }: CodeTabsProps) {
  const firstId = examples[0]?.id ?? "";
  const [activeExampleId, setActiveExampleId] = useState<string>(
    initialId ?? firstId,
  );
  const activeExample =
    examples.find((example) => example.id === activeExampleId) ?? examples[0];
  const activeIndex = examples.findIndex(
    (example) => example.id === activeExample?.id,
  );

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
      const nextId = examples[nextIndex]?.id;
      if (!nextId) return;
      setActiveExampleId(nextId);
      document.getElementById(`example-tab-${nextId}`)?.focus();
    },
    [activeIndex, examples],
  );

  if (!activeExample) return null;

  return (
    <>
      <div
        className="mb-6 flex flex-wrap justify-center gap-2 sm:justify-start"
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
            aria-selected={activeExample.id === example.id}
            aria-controls={`example-panel-${example.id}`}
            tabIndex={activeExample.id === example.id ? 0 : -1}
            className={cn(
              "min-h-11 rounded-md px-3.5 py-2 text-sm font-medium leading-5 transition-all duration-200",
              activeExample.id === example.id
                ? "bg-foreground text-background"
                : "glass text-muted-foreground hover:text-foreground",
            )}
          >
            {example.name}
          </button>
        ))}
      </div>

      <div
        id={`example-panel-${activeExample.id}`}
        role="tabpanel"
        aria-labelledby={`example-tab-${activeExample.id}`}
      >
        <CodeBlock code={activeExample.code} output={activeExample.output} />
      </div>
    </>
  );
}
