"use client";

import { useEffect, useState, useCallback } from "react";
import { Copy, Check } from "lucide-react";
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
      <div className="mx-auto max-w-6xl w-full px-6">
        <div className="grid grid-cols-1 lg:grid-cols-[1fr_1.3fr] gap-8 lg:gap-12 items-center">
          {/* Left: Content */}
          <div>
            {downloads && (
              <div className="mb-4">
                <a
                  href="https://pepy.tech/project/docpull"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors bg-background/50 py-0.5 px-1 rounded"
                  aria-label={`${downloads} downloads on PyPI`}
                >
                  <span>{downloads} downloads on PyPI</span>
                </a>
              </div>
            )}

            <h1 className="text-3xl sm:text-4xl lg:text-5xl font-medium tracking-tight mb-6">
              <span className="bg-background/50 px-1 rounded">Fetch docs.</span>
              <br />
              <span className="text-muted-foreground bg-background/50 px-1 rounded">
                Get clean Markdown.
              </span>
            </h1>

            <p className="text-muted-foreground text-base sm:text-lg mb-8 max-w-md bg-background/50 py-1 rounded">
              Feed any documentation site to your AI — clean, structured
              Markdown, ready for RAG pipelines, Claude Code skills, and
              training datasets.
            </p>

            {/* Install command + CTA */}
            <div className="flex flex-wrap items-center gap-3">
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
              <a
                href="#examples"
                className="px-4 py-2.5 rounded-xl bg-foreground text-background text-sm font-medium hover:opacity-90 transition-opacity"
              >
                See examples
              </a>
            </div>
          </div>

          {/* Right: Terminal */}
          <div className="terminal w-full overflow-hidden">
            <div className="terminal-header">
              <div className="terminal-dot terminal-dot-close" />
              <div className="terminal-dot terminal-dot-minimize" />
              <div className="terminal-dot terminal-dot-maximize" />
            </div>
            <div className="p-5 lg:p-8 font-mono text-sm sm:text-base lg:text-lg min-h-[220px] lg:min-h-[320px]">
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
