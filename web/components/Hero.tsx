"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";

const terminalLines = [
  { type: "command", content: "docpull https://www.python.org/blogs/ -o ./python-news" },
  { type: "output", content: "" },
  { type: "dim", content: "Discovering URLs..." },
  { type: "normal", content: "Found 38 pages" },
  { type: "dim", content: "Fetching with RAG profile" },
  { type: "normal", content: "[==============================] 38/38" },
  { type: "output", content: "" },
  { type: "success", content: "Done in 12s. Saved 2.8 MB to ./python-news" },
] as const;

const INSTALL_COMMAND = "pip install docpull";
const COPY_RESET_DELAY_MS = 2_000;

export default function Hero() {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const copyResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (copyResetTimer.current) {
        clearTimeout(copyResetTimer.current);
      }
    };
  }, []);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_COMMAND);
      setCopied(true);
      setCopyFailed(false);
      if (copyResetTimer.current) {
        clearTimeout(copyResetTimer.current);
      }
      copyResetTimer.current = setTimeout(() => {
        setCopied(false);
        copyResetTimer.current = null;
      }, COPY_RESET_DELAY_MS);
    } catch (error) {
      const writeFailed = error !== undefined;
      setCopied(false);
      setCopyFailed(writeFailed);
    }
  }, []);

  return (
    <section className="flex items-start justify-center pt-20 lg:pt-56 pb-16 lg:pb-32">
      <div className="mx-auto max-w-6xl w-full px-6">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-8 lg:gap-12 items-center">
          {/* Left: Content */}
          <div>
            <h1 className="text-3xl sm:text-4xl lg:text-5xl font-medium tracking-tight mb-6">
              <span>Pull public web.</span>
              <br />
              <span className="text-muted-foreground">Feed better agents.</span>
            </h1>

            <p className="text-muted-foreground text-base sm:text-lg mb-8 max-w-md">
              Turn static and server-rendered web pages into clean Markdown,
              NDJSON, and local context packs for coding agents, MCP clients,
              and RAG pipelines.
            </p>

            {/* Install command + CTA */}
            <div className="flex flex-wrap items-center gap-3">
              <code className="px-4 py-2.5 glass rounded-xl text-sm font-mono">
                {INSTALL_COMMAND}
              </code>
              <button
                onClick={handleCopy}
                className="min-h-11 min-w-11 p-2.5 rounded-xl glass hover:bg-foreground/5 transition-colors"
                aria-label={
                  copyFailed
                    ? "Copy failed"
                    : copied
                      ? "Copied"
                      : "Copy install command"
                }
              >
                {copied ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
              <a
                href="#examples"
                className="min-h-11 inline-flex items-center px-4 py-2.5 rounded-xl bg-foreground text-background text-sm font-medium hover:opacity-90 transition-opacity"
              >
                See examples
              </a>
            </div>

            <p className="mt-4 text-xs text-muted-foreground max-w-md leading-relaxed">
              Base crawls need no browser or API key. JavaScript-heavy pages are
              detected and skipped automatically so agents can route elsewhere.
            </p>
          </div>

          {/* Right: Terminal */}
          <div className="terminal w-full overflow-hidden">
            <div className="terminal-header">
              <div className="terminal-dot terminal-dot-close" />
              <div className="terminal-dot terminal-dot-minimize" />
              <div className="terminal-dot terminal-dot-maximize" />
            </div>
            <div className="p-5 lg:p-8 font-mono text-sm sm:text-base lg:text-lg min-h-[220px] lg:min-h-[320px]">
              {terminalLines.map((line, i) => (
                <div
                  key={i}
                  className={cn(
                    "mb-0.5",
                    line.type === "command" && "text-white",
                    line.type === "dim" && "text-neutral-500",
                    line.type === "normal" && "text-neutral-400",
                    line.type === "success" && "text-neutral-300",
                    line.type === "output" && "h-4",
                  )}
                >
                  {line.type === "command" && (
                    <span className="text-neutral-500">$ </span>
                  )}
                  {line.content}
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
