"use client";

import { useEffect, useState, useCallback } from "react";
import { Copy, Check, Download } from "lucide-react";
import { cn } from "@/lib/utils";

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

export default function Hero() {
  const [visibleLines, setVisibleLines] = useState(0);
  const [copied, setCopied] = useState(false);
  const [downloads, setDownloads] = useState<string | null>(null);

  useEffect(() => {
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
  }, []);

  useEffect(() => {
    fetch("https://static.pepy.tech/badge/docpull")
      .then((res) => res.text())
      .then((svg) => {
        // Extract the download count from the SVG (last text element with the count)
        const match = svg.match(/textLength="[^"]*">(\d+[kKmM]?)<\/text>/g);
        if (match && match.length >= 2) {
          const countMatch = match[match.length - 1].match(/>(\d+[kKmM]?)</);
          if (countMatch) {
            setDownloads(countMatch[1]);
          }
        }
      })
      .catch(() => setDownloads(null));
  }, []);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(INSTALL_COMMAND);
    setCopied(true);
    const timeout = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timeout);
  }, []);

  return (
    <section className="flex items-start justify-center pt-20 lg:pt-56 pb-16 lg:pb-32">
      <div className="mx-auto max-w-5xl w-full px-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 lg:gap-16 items-center">
          {/* Left: Content */}
          <div>
            <div className="flex items-center gap-3 text-sm text-muted-foreground mb-4">
              <span className="bg-background/50 py-0.5 rounded">
                Documentation fetcher for AI
              </span>
              {downloads && (
                <a
                  href="https://pepy.tech/project/docpull"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-foreground hover:opacity-80 transition-opacity"
                >
                  <Download className="h-3 w-3" />
                  {downloads}
                </a>
              )}
            </div>

            <h1 className="text-3xl sm:text-4xl lg:text-5xl font-medium tracking-tight mb-6">
              Fetch docs.
              <br />
              <span className="text-muted-foreground">Get clean Markdown.</span>
            </h1>

            <p className="text-muted-foreground text-base sm:text-lg mb-8 max-w-md bg-background/50 py-1 rounded">
              Turn any docs site into AI-ready Markdown. Built for RAG
              pipelines, Claude Code skills, and training datasets.
            </p>

            {/* Install command */}
            <div className="flex items-center gap-3">
              <code className="px-4 py-2.5 glass rounded-xl text-sm font-mono">
                {INSTALL_COMMAND}
              </code>
              <button
                onClick={handleCopy}
                className="p-2.5 rounded-xl glass hover:bg-foreground/5 transition-colors"
                aria-label={copied ? "Copied" : "Copy install command"}
              >
                {copied ? (
                  <Check className="h-4 w-4" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          {/* Right: Terminal */}
          <div className="terminal w-full overflow-hidden">
            <div className="terminal-header">
              <div className="terminal-dot terminal-dot-close" />
              <div className="terminal-dot terminal-dot-minimize" />
              <div className="terminal-dot terminal-dot-maximize" />
            </div>
            <div className="p-4 lg:p-6 font-mono text-xs sm:text-sm lg:text-base min-h-[180px] lg:min-h-[240px]">
              {terminalLines.slice(0, visibleLines).map((line, i) => (
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
              {visibleLines < terminalLines.length && (
                <span className="inline-block w-2 h-4 bg-neutral-500 animate-pulse" />
              )}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
