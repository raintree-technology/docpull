"use client";

import Image from "next/image";
import { useEffect, useState } from "react";
import { Copy, Check } from "lucide-react";
import {
  claudePluginUrl,
  installCommand,
} from "@/lib/content/install";
import {
  heroHighlights,
  heroMetrics,
  heroTerminalLines,
} from "@/lib/content/home";
import { cn } from "@/lib/utils";
import { useReducedMotion } from "@/lib/use-reduced-motion";
import { useCopyToClipboard } from "@/lib/hooks/use-copy-to-clipboard";
import { HostTab } from "./HostBadge";

export default function Hero() {
  const [visibleLines, setVisibleLines] = useState(0);
  const { copiedId, copyFailed, copy } = useCopyToClipboard();
  const reducedMotion = useReducedMotion();
  const copied = copiedId === "install";

  useEffect(() => {
    if (reducedMotion) {
      return;
    }

    const timer = setInterval(() => {
      setVisibleLines((prev) => {
        if (prev >= heroTerminalLines.length) {
          clearInterval(timer);
          return prev;
        }
        return prev + 1;
      });
    }, 350);
    return () => clearInterval(timer);
  }, [reducedMotion]);

  const renderedVisibleLines = reducedMotion
    ? heroTerminalLines.length
    : visibleLines;

  return (
    <section
      id="overview"
      className="relative flex items-start justify-center overflow-hidden pt-24 pb-16 sm:pt-28 lg:pt-36 lg:pb-24"
    >
      <div className="pointer-events-none absolute inset-0 hairline-grid opacity-50" />
      <div className="mx-auto w-full max-w-6xl px-6">
        <div className="grid items-center gap-10 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)] lg:gap-14">
          <div className="relative z-10">
            <p className="section-kicker mb-4">Local-first web capture</p>
            <h1 className="max-w-[12ch] text-4xl font-medium tracking-tight sm:text-5xl lg:text-7xl lg:leading-[0.95]">
              Turn the web into Markdown that stays yours.
            </h1>

            <p className="mt-6 max-w-2xl text-base leading-relaxed text-foreground/74 sm:text-lg">
              docpull pulls server-rendered pages into clean Markdown on your
              machine. It keeps the workflow inspectable, avoids hosted crawler
              black boxes, and gives agents a stable local corpus to work from.
            </p>

            <div className="mt-8 flex flex-wrap items-center gap-3">
              <a href="#install" className="apple-button">
                Install locally
              </a>
              <a href="#mcp" className="apple-button-secondary">
                Choose your client
              </a>
              <a
                href={claudePluginUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="apple-button-secondary gap-2"
              >
                <Image
                  src="/brands/anthropic-symbol-dark.svg"
                  alt="Anthropic"
                  width={18}
                  height={18}
                  unoptimized
                  className="h-[18px] w-[18px]"
                />
                Add to Claude
              </a>
            </div>

            <div className="mt-8 apple-panel rounded-[1.75rem] p-4 sm:p-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-[11px] font-mono uppercase tracking-[0.18em] text-foreground/48">
                    Quick start
                  </p>
                  <code className="mt-2 block text-sm text-foreground/84 sm:text-base">
                    {installCommand}
                  </code>
                </div>
                <button
                  onClick={() => copy(installCommand, "install")}
                  className="flex min-h-11 min-w-11 items-center justify-center rounded-full border border-foreground/10 bg-background/70 p-2.5 transition-colors hover:bg-foreground/5"
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
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {heroHighlights.map((item) => (
                  <div
                    key={item}
                    className="rounded-2xl border border-foreground/8 bg-background/54 px-4 py-3 text-sm text-foreground/68"
                  >
                    {item}
                  </div>
                ))}
              </div>
            </div>

            <div className="mt-7 flex flex-wrap items-center gap-x-6 gap-y-3 text-sm text-muted-foreground/90">
              <HostTab brand="anthropic" label="Claude Code" />
              <HostTab brand="cursor" label="Cursor" />
              <HostTab brand="openai" label="Codex" />
            </div>
          </div>

          <div className="relative z-10">
            <div className="apple-panel rounded-[2rem] p-3 sm:p-4">
              <div className="terminal w-full overflow-hidden text-left">
                <div className="terminal-header">
                  <div className="terminal-dot terminal-dot-close" />
                  <div className="terminal-dot terminal-dot-minimize" />
                  <div className="terminal-dot terminal-dot-maximize" />
                  <span className="ml-2 text-xs text-neutral-500">
                    Local run
                  </span>
                </div>
                <div className="min-h-[220px] p-5 font-mono text-sm sm:text-base lg:min-h-[288px] lg:text-[0.98rem]">
                  {heroTerminalLines
                    .slice(0, renderedVisibleLines)
                    .map((line, i) => (
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
                  {renderedVisibleLines < heroTerminalLines.length && (
                    <span className="inline-block h-4 w-2 animate-pulse bg-neutral-500" />
                  )}
                </div>
              </div>

              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                {heroMetrics.map((metric) => (
                  <Metric
                    key={metric.value}
                    value={metric.value}
                    label={metric.label}
                  />
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}

function Metric({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-[1.4rem] border border-foreground/8 bg-background/58 px-4 py-4">
      <p className="text-sm font-medium text-foreground">{value}</p>
      <p className="mt-1 text-sm leading-relaxed text-foreground/62">{label}</p>
    </div>
  );
}
