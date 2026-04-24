"use client";

import { useEffect, useState } from "react";
import { Database, Workflow, Sparkles } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

const URL = "docs.anthropic.com";
const TOTAL_PAGES = 247;

type Stage = "point" | "fetch" | "use" | "done";

export default function HowItWorks() {
  const [stage, setStage] = useState<Stage>("point");
  const [typed, setTyped] = useState("");
  const [pages, setPages] = useState(0);
  const [destLit, setDestLit] = useState(-1);

  useEffect(() => {
    if (typed.length < URL.length) {
      const t = setTimeout(
        () => setTyped(URL.slice(0, typed.length + 1)),
        65,
      );
      return () => clearTimeout(t);
    }
  }, [typed]);

  useEffect(() => {
    if (stage !== "point") return;
    if (typed.length < URL.length) return;
    const t = setTimeout(() => setStage("fetch"), 700);
    return () => clearTimeout(t);
  }, [stage, typed]);

  useEffect(() => {
    if (stage !== "fetch") return;
    if (pages >= TOTAL_PAGES) {
      const t = setTimeout(() => setStage("use"), 500);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => {
      setPages((p) =>
        Math.min(TOTAL_PAGES, p + Math.floor(Math.random() * 18) + 6),
      );
    }, 70);
    return () => clearTimeout(t);
  }, [stage, pages]);

  useEffect(() => {
    if (stage !== "use") return;
    if (destLit < 2) {
      const t = setTimeout(() => setDestLit((s) => s + 1), 500);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => setStage("done"), 1200);
    return () => clearTimeout(t);
  }, [stage, destLit]);

  useEffect(() => {
    if (stage !== "done") return;
    const t = setTimeout(() => {
      setPages(0);
      setDestLit(-1);
      setStage("point");
    }, 1400);
    return () => clearTimeout(t);
  }, [stage]);

  const activeIdx =
    stage === "point" ? 0 : stage === "fetch" ? 1 : stage === "use" ? 2 : 3;

  const flow1Active = stage === "fetch";
  const flow2Active = stage === "use";
  const flow1Lit = activeIdx >= 1;
  const flow2Lit = activeIdx >= 2;

  return (
    <section id="how-it-works" className="py-16 sm:py-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-10 sm:mb-14 text-center sm:text-left">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            <span className="bg-background/50 px-1 rounded">How it works</span>
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Three steps from URL to AI-ready output.
          </p>
        </div>

        <div className="glass rounded-2xl p-5 sm:p-8">
          <div className="grid grid-cols-1 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto_minmax(0,1fr)] gap-4 md:gap-0 items-start">
            <Stage num="01" active={activeIdx === 0} done={activeIdx > 0}>
              <UrlBar typed={typed} />
            </Stage>
            <Connector active={flow1Active} lit={flow1Lit} />
            <Stage num="02" active={activeIdx === 1} done={activeIdx > 1}>
              <FetchDisplay pages={pages} />
            </Stage>
            <Connector active={flow2Active} lit={flow2Lit} />
            <Stage num="03" active={activeIdx === 2} done={activeIdx > 2}>
              <DestList lit={destLit} />
            </Stage>
          </div>

          <div className="mt-6 sm:mt-8 pt-5 sm:pt-6 border-t border-foreground/10 grid grid-cols-1 md:grid-cols-3 gap-4 md:gap-6">
            <StepText
              title="Point"
              desc="Give docpull a URL — any documentation site, public or gated."
              active={activeIdx === 0}
            />
            <StepText
              title="Fetch"
              desc="It discovers pages, fetches them politely, and converts HTML to clean Markdown."
              active={activeIdx === 1}
            />
            <StepText
              title="Use"
              desc="Drop the output into your vector store, RAG pipeline, or Claude Code skill."
              active={activeIdx === 2}
            />
          </div>
        </div>
      </div>
    </section>
  );
}

function Stage({
  num,
  active,
  done,
  children,
}: {
  num: string;
  active: boolean;
  done: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 min-w-0">
      <div className="flex items-center gap-2">
        <span
          className={cn(
            "inline-block w-1.5 h-1.5 rounded-full transition-colors duration-300",
            active
              ? "bg-foreground"
              : done
                ? "bg-foreground/60"
                : "bg-foreground/20",
          )}
        />
        <span className="text-[10px] font-mono text-muted-foreground tracking-[0.18em]">
          STEP {num}
        </span>
      </div>
      <div
        className={cn(
          "transition-opacity duration-500 min-h-[96px]",
          !active && !done && "opacity-40",
        )}
      >
        {children}
      </div>
    </div>
  );
}

function UrlBar({ typed }: { typed: string }) {
  return (
    <div className="bg-background/40 border border-foreground/10 rounded-lg px-2.5 py-2.5 font-mono text-[11px] flex items-center gap-1 h-[42px] overflow-hidden">
      <svg
        viewBox="0 0 16 16"
        width="12"
        height="12"
        className="shrink-0 text-muted-foreground/70"
        aria-hidden
      >
        <path
          d="M6.5 3.5h-2a2 2 0 0 0-2 2v5a2 2 0 0 0 2 2h2M9.5 3.5h2a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2M5 8h6"
          stroke="currentColor"
          strokeWidth="1.25"
          fill="none"
          strokeLinecap="round"
        />
      </svg>
      <span className="text-muted-foreground/70 select-none shrink-0">
        https://
      </span>
      <span className="relative flex items-center min-w-0 flex-1 whitespace-nowrap overflow-hidden">
        <span className="text-foreground/90">{typed}</span>
        <span
          className="inline-block w-[2px] h-3.5 bg-foreground/70 animate-pulse shrink-0 ml-px"
          aria-hidden
        />
      </span>
    </div>
  );
}

function FetchDisplay({ pages }: { pages: number }) {
  const pct = (pages / TOTAL_PAGES) * 100;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between font-mono text-[11px]">
        <span className="text-muted-foreground">discovered</span>
        <span className="tabular-nums">
          <span className="text-foreground/90">
            {pages.toString().padStart(3, "0")}
          </span>
          <span className="text-muted-foreground/60"> / {TOTAL_PAGES}</span>
        </span>
      </div>
      <div className="relative h-1 rounded-full bg-foreground/10 overflow-hidden">
        <div
          className="h-full bg-foreground/80 transition-[width] duration-100 ease-linear"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground pt-0.5">
        <span className="px-1.5 py-0.5 rounded bg-foreground/5 border border-foreground/10">
          HTML
        </span>
        <svg
          width="18"
          height="8"
          viewBox="0 0 18 8"
          fill="none"
          className="text-foreground/40"
        >
          <path
            d="M0 4 H14 M10 1 L14 4 L10 7"
            stroke="currentColor"
            strokeWidth="1"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="px-1.5 py-0.5 rounded bg-foreground/5 border border-foreground/10">
          MD
        </span>
      </div>
    </div>
  );
}

function DestList({ lit }: { lit: number }) {
  return (
    <div className="flex flex-col gap-1.5">
      <DestChip icon={Database} label="Vector store" on={lit >= 0} />
      <DestChip icon={Workflow} label="RAG pipeline" on={lit >= 1} />
      <DestChip icon={Sparkles} label="Agent skill" on={lit >= 2} />
    </div>
  );
}

function DestChip({
  icon: Icon,
  label,
  on,
}: {
  icon: LucideIcon;
  label: string;
  on: boolean;
}) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md border px-2 py-1 transition-colors duration-300",
        on
          ? "border-foreground/20 bg-foreground/[0.06]"
          : "border-foreground/10 bg-foreground/[0.02]",
      )}
    >
      <span
        className={cn(
          "inline-block w-1.5 h-1.5 rounded-full transition-colors duration-300",
          on ? "bg-foreground/80" : "bg-foreground/20",
        )}
      />
      <Icon
        className={cn(
          "w-3 h-3 transition-colors duration-300",
          on ? "text-foreground/70" : "text-foreground/30",
        )}
      />
      <span
        className={cn(
          "text-[11px] font-mono transition-colors duration-300",
          on ? "text-foreground/80" : "text-muted-foreground/50",
        )}
      >
        {label}
      </span>
    </div>
  );
}

function Connector({ active, lit }: { active: boolean; lit: boolean }) {
  const baseOpacity = lit ? "0.45" : "0.18";
  const arrowOpacity = lit ? "0.55" : "0.3";
  return (
    <>
      <div
        aria-hidden
        className="hidden md:flex items-center justify-center px-3 pt-6 text-foreground"
      >
        <svg
          width="56"
          height="16"
          viewBox="0 0 56 16"
          className="overflow-visible"
        >
          <line
            x1="0"
            y1="8"
            x2="50"
            y2="8"
            stroke="currentColor"
            strokeOpacity={baseOpacity}
            strokeWidth="1"
            strokeDasharray="2 4"
          />
          {active && (
            <line
              x1="0"
              y1="8"
              x2="50"
              y2="8"
              stroke="currentColor"
              strokeOpacity="0.9"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeDasharray="6 50"
              className="[animation:flow-right_1.5s_linear_infinite] motion-reduce:[animation:none]"
            />
          )}
          <path
            d="M46 3 L53 8 L46 13"
            stroke="currentColor"
            strokeOpacity={arrowOpacity}
            strokeWidth="1.25"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
      <div
        aria-hidden
        className="flex md:hidden items-center justify-center py-1 text-foreground"
      >
        <svg
          width="16"
          height="28"
          viewBox="0 0 16 28"
          className="overflow-visible"
        >
          <line
            x1="8"
            y1="0"
            x2="8"
            y2="22"
            stroke="currentColor"
            strokeOpacity={baseOpacity}
            strokeWidth="1"
            strokeDasharray="2 4"
          />
          {active && (
            <line
              x1="8"
              y1="0"
              x2="8"
              y2="22"
              stroke="currentColor"
              strokeOpacity="0.9"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeDasharray="6 22"
              className="[animation:flow-down_1.5s_linear_infinite] motion-reduce:[animation:none]"
            />
          )}
          <path
            d="M3 18 L8 25 L13 18"
            stroke="currentColor"
            strokeOpacity={arrowOpacity}
            strokeWidth="1.25"
            fill="none"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </div>
    </>
  );
}

function StepText({
  title,
  desc,
  active,
}: {
  title: string;
  desc: string;
  active: boolean;
}) {
  return (
    <div
      className={cn("transition-opacity duration-500", !active && "opacity-60")}
    >
      <h3
        className={cn(
          "font-medium text-sm mb-1 transition-colors duration-300",
          active ? "text-foreground" : "text-muted-foreground",
        )}
      >
        {title}
      </h3>
      <p className="text-xs sm:text-sm text-muted-foreground leading-relaxed">
        {desc}
      </p>
    </div>
  );
}
