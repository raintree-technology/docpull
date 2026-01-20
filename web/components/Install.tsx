"use client";

import { useState, useCallback } from "react";
import { Copy, Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const altMethods = [
  { label: "pipx", command: "pipx install docpull" },
  { label: "uv", command: "uv pip install docpull" },
  { label: "+js", command: "pip install docpull[js]" },
  { label: "+all", command: "pip install docpull[all]" },
] as const;

export default function Install() {
  const [copied, setCopied] = useState<string | null>(null);
  const [showAlt, setShowAlt] = useState(false);

  const handleCopy = useCallback((text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  }, []);

  return (
    <section id="install" className="py-16 sm:py-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="max-w-2xl mx-auto text-center">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            Install
          </h2>
          {/* Main pip command */}
          <div className="flex items-center justify-center gap-2 mb-6">
            <code className="px-6 py-3 glass rounded-xl text-sm sm:text-base font-mono">
              pip install docpull
            </code>
            <button
              onClick={() => handleCopy("pip install docpull", "main")}
              className="p-3 rounded-xl glass hover:bg-foreground/5 transition-colors"
              aria-label={copied === "main" ? "Copied" : "Copy command"}
            >
              {copied === "main" ? (
                <Check className="h-4 w-4" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </button>
          </div>

          {/* Collapsible alternatives */}
          <div>
            <button
              onClick={() => setShowAlt(!showAlt)}
              className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors"
            >
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 transition-transform",
                  showAlt && "rotate-180",
                )}
              />
              More options
            </button>

            {showAlt && (
              <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-lg mx-auto">
                {altMethods.map((method, i) => (
                  <div
                    key={method.label}
                    className="flex items-center justify-between px-4 py-2.5 glass rounded-xl text-sm"
                  >
                    <div className="flex items-center gap-3">
                      <span className="text-muted-foreground text-xs font-medium min-w-[32px]">
                        {method.label}
                      </span>
                      <code className="font-mono text-xs">
                        {method.command}
                      </code>
                    </div>
                    <button
                      onClick={() => handleCopy(method.command, `alt-${i}`)}
                      className="p-1.5 rounded-lg hover:bg-foreground/5 transition-colors ml-2"
                      aria-label={
                        copied === `alt-${i}` ? "Copied" : "Copy command"
                      }
                    >
                      {copied === `alt-${i}` ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : (
                        <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                      )}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
