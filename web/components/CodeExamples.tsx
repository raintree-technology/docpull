"use client";

import { useState, useCallback, memo, type KeyboardEvent } from "react";
import { Copy, Check } from "lucide-react";
import { codeExamples } from "@/lib/content/home";
import { cn } from "@/lib/utils";
import { useCopyToClipboard } from "@/lib/hooks/use-copy-to-clipboard";

const CodeBlock = memo(function CodeBlock({
  code,
  output,
}: {
  code: string;
  output: string;
}) {
  const { copiedId, copy } = useCopyToClipboard();
  const copied = copiedId === "code";

  return (
    <div className="space-y-4">
      {/* Input */}
      <div className="relative group">
        <div className="text-xs text-muted-foreground mb-2">Input</div>
        <pre className="p-4 glass rounded-xl overflow-x-auto text-xs sm:text-sm">
          <code className="whitespace-pre">{code}</code>
        </pre>
        <button
          type="button"
          onClick={() => copy(code, "code")}
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
  const activeExample = codeExamples.find((e) => e.id === active);
  const activeIndex = codeExamples.findIndex((e) => e.id === active);

  const handleTabClick = useCallback((id: string) => {
    setActive(id);
  }, []);

  const handleTabKeyDown = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
        return;
      }

      event.preventDefault();
      const lastIndex = codeExamples.length - 1;
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
      const nextId = codeExamples[nextIndex].id;
      setActive(nextId);
      document.getElementById(`example-tab-${nextId}`)?.focus();
    },
    [activeIndex],
  );

  return (
    <section
      id="examples"
      className="border-t border-foreground/8 py-16 sm:py-24"
    >
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 text-center sm:text-left">
          <p className="section-kicker mb-3">Real output</p>
          <h2 className="section-title mb-4">
            The quickest way to explain docpull is to show the files.
          </h2>
          <p className="section-copy max-w-3xl">
            These examples stay specific on purpose. Commands matter less than
            the shape of the Markdown and folders you get back from a real web
            pull.
          </p>
        </div>

        <div
          className="mb-6 flex flex-wrap justify-center gap-2 rounded-[1.25rem] border border-foreground/8 bg-background/45 p-2 sm:justify-start"
          role="tablist"
          aria-label="Code example categories"
        >
          {codeExamples.map((example) => (
            <button
              type="button"
              key={example.id}
              id={`example-tab-${example.id}`}
              onClick={() => handleTabClick(example.id)}
              onKeyDown={handleTabKeyDown}
              role="tab"
              aria-selected={active === example.id}
              aria-controls={`example-panel-${example.id}`}
              tabIndex={active === example.id ? 0 : -1}
              className={cn(
                "min-h-11 rounded-full px-4 py-2 text-xs transition-all duration-200 sm:text-sm",
                active === example.id
                  ? "bg-foreground text-background shadow-[0_8px_24px_rgba(15,23,42,0.16)]"
                  : "text-muted-foreground hover:bg-background/70 hover:text-foreground",
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
