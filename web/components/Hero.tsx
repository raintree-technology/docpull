"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { Copy, Check } from "lucide-react";
import { cn } from "@/lib/utils";
import { useReducedMotion } from "@/lib/use-reduced-motion";

const terminalLines = [
  { type: "command", content: "docpull https://docs.anthropic.com" },
  { type: "output", content: "" },
  { type: "dim", content: "Discovering URLs..." },
  { type: "normal", content: "Found 247 pages" },
  { type: "dim", content: "Fetching with RAG profile" },
  { type: "normal", content: "[=============================] 247/247" },
  { type: "output", content: "" },
  { type: "success", content: "Done in 34s. Saved 12.3 MB to ./docs" },
] as const;

const INSTALL_COMMAND = "pip install docpull";
const COPY_RESET_DELAY_MS = 2_000;

export default function Hero() {
  const [visibleLines, setVisibleLines] = useState(0);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const copyResetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reducedMotion = useReducedMotion();

  useEffect(() => {
    if (reducedMotion) {
      return;
    }

    const timer = setInterval(() => {
      setVisibleLines((prev) => {
        if (prev >= terminalLines.length) {
          clearInterval(timer);
          return prev;
        }
        return prev + 1;
      });
    }, 350);
    return () => clearInterval(timer);
  }, [reducedMotion]);

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

  const renderedVisibleLines = reducedMotion ? terminalLines.length : visibleLines;

  return (
    <section className="flex items-start justify-center pt-20 lg:pt-56 pb-16 lg:pb-32">
      <div className="mx-auto max-w-6xl w-full px-6">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-8 lg:gap-12 items-center">
          {/* Left: Content */}
          <div>
            <h1 className="text-3xl sm:text-4xl lg:text-5xl font-medium tracking-tight mb-6">
              <span>Fetch docs.</span>
              <br />
              <span className="text-muted-foreground">Get clean Markdown.</span>
            </h1>

            <p className="text-muted-foreground text-base sm:text-lg mb-8 max-w-md">
              Local Python crawler that turns server-rendered docs into
              clean Markdown. Zero API keys, zero data leaving your
              machine. Built for RAG pipelines and Claude Code skills.
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
              Static and server-rendered sites only. JS-rendered SPAs are
              detected and skipped — pass{" "}
              <code className="font-mono text-[11px] bg-background/60 px-1 rounded">
                --strict-js-required
              </code>{" "}
              to make that an error so your agent can route elsewhere.
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
              {terminalLines.slice(0, renderedVisibleLines).map((line, i) => (
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
              {renderedVisibleLines < terminalLines.length && (
                <span className="inline-block w-2 h-4 bg-neutral-500 animate-pulse" />
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
