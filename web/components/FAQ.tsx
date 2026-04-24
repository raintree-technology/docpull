"use client";

import { useState, type ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const REPO = "https://github.com/raintree-technology/docpull/blob/main";

function Src({ path, line, children }: { path: string; line?: number; children: ReactNode }) {
  const href = `${REPO}/${path}${line ? `#L${line}` : ""}`;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="underline decoration-dotted underline-offset-2 hover:text-foreground transition-colors"
    >
      {children}
    </a>
  );
}

const faqs: { q: string; a: ReactNode }[] = [
  {
    q: "How does docpull compare to Firecrawl, Jina Reader, or Crawl4AI?",
    a: (
      <>
        Firecrawl and Jina Reader are hosted APIs — your URLs route through their
        infrastructure and pricing scales past their free tiers. docpull runs
        locally, stays free, and leaves no trace outside your machine. Crawl4AI
        is the closest OSS peer, but it&apos;s a general-purpose agent toolkit;
        docpull is narrower — YAML-frontmatter Markdown tuned for documentation
        sites, with{" "}
        <Src path="src/docpull/models/profiles.py">
          rag / mirror / quick profiles
        </Src>{" "}
        baked in.
      </>
    ),
  },
  {
    q: "How clean is the Markdown? Does it preserve code blocks, tables, and images?",
    a: (
      <>
        Yes.{" "}
        <Src path="src/docpull/conversion/extractor.py" line={110}>
          Fenced code blocks
        </Src>{" "}
        keep their language hints,{" "}
        <Src path="src/docpull/conversion/markdown.py" line={32}>
          tables
        </Src>{" "}
        convert to Markdown pipes, and{" "}
        <Src path="src/docpull/conversion/extractor.py" line={109}>
          images
        </Src>{" "}
        keep their alt text. Nav bars, footers, sidebars, and cookie banners are
        stripped before conversion via{" "}
        <Src path="src/docpull/conversion/extractor.py" line={42}>
          the extractor&apos;s remove-selector list
        </Src>
        .
      </>
    ),
  },
  {
    q: "Will it scale to a 10,000-page site, and can I re-run it on a schedule?",
    a: (
      <>
        Yes.{" "}
        <Src path="src/docpull/pipeline/steps/dedup.py">
          Streaming deduplication
        </Src>{" "}
        hashes content as it arrives so memory stays flat. The cache tracks{" "}
        <Src path="src/docpull/cache/manager.py" line={222}>
          ETags per URL
        </Src>
        , so scheduled re-runs only transfer pages that actually changed, and{" "}
        <Src path="src/docpull/cache/manager.py" line={45}>
          fetched and failed URL sets persist on disk
        </Src>{" "}
        so a crash resumes instead of restarts.
      </>
    ),
  },
  {
    q: "Does it handle auth-gated documentation?",
    a: (
      <>
        Yes. Pass credentials with{" "}
        <Src path="src/docpull/cli.py" line={202}>
          --auth-bearer, --auth-basic, --auth-cookie, or --auth-header
        </Src>
        . They ride with every request, so internal docs, subscriber-only
        content, and corporate wikis all work.
      </>
    ),
  },
  {
    q: "Does the output drop straight into a Claude Code skill?",
    a: (
      <>
        Yes — Claude Code skills are Markdown files with YAML frontmatter, which
        is exactly what docpull emits. Run{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          docpull URL -o .claude/skills/name
        </code>{" "}
        and you get a working skill directory you can edit or version-control.
        No conversion step.
      </>
    ),
  },
  {
    q: "Can I use it as a Python library?",
    a: (
      <>
        Yes. Import{" "}
        <Src path="src/docpull/__init__.py" line={20}>
          Fetcher and DocpullConfig
        </Src>
        , configure programmatically, and iterate over async events as pages
        are fetched. See the Python tab above for a minimal setup.
      </>
    ),
  },
];

function FaqItem({ q, a }: { q: string; a: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b last:border-b-0">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between py-4 text-left gap-4"
        aria-expanded={open}
      >
        <span className="text-sm font-medium">{q}</span>
        <ChevronDown
          className={cn(
            "h-4 w-4 text-muted-foreground shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="pb-4 text-sm text-muted-foreground leading-relaxed pr-8">
          {a}
        </div>
      )}
    </div>
  );
}

export default function FAQ() {
  return (
    <section id="faq" className="py-16 sm:py-24 border-t">
      <div className="mx-auto max-w-3xl px-6">
        <div className="mb-8 sm:mb-12 text-center sm:text-left">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            <span className="bg-background/50 px-1 rounded">Why docpull?</span>
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Answers to questions people ask before installing.
          </p>
        </div>

        <div className="rounded-xl glass px-5">
          {faqs.map((faq, i) => (
            <FaqItem key={i} q={faq.q} a={faq.a} />
          ))}
        </div>
      </div>
    </section>
  );
}
