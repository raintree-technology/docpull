import { type ReactNode } from "react";

// Shared FAQ source of truth.
// - `a`     — rich JSX rendered in the FAQ accordion (with source links).
// - `aText` — a plain-text equivalent used for FAQPage JSON-LD (Spec: SEO /
//             Structured data). Keep the two in sync when editing an answer.

const REPO = "https://github.com/raintree-technology/docpull/blob/main";

function Src({
  path,
  line,
  children,
}: {
  path: string;
  line?: number;
  children: ReactNode;
}) {
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

export const faqs: { q: string; a: ReactNode; aText: string }[] = [
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
    aText:
      "Firecrawl and Jina Reader are hosted APIs — your URLs route through their infrastructure and pricing scales past their free tiers. docpull runs locally, stays free, and leaves no trace outside your machine. Crawl4AI is the closest open-source peer, but it's a general-purpose agent toolkit; docpull is narrower — YAML-frontmatter Markdown tuned for documentation sites, with rag, mirror, and quick profiles baked in.",
  },
  {
    q: "How clean is the Markdown? Does it preserve code blocks, tables, and images?",
    a: (
      <>
        Yes.{" "}
        <Src path="src/docpull/conversion/extractor.py" line={110}>
          Fenced code blocks
        </Src>{" "}
        keep their language hints (Prism, highlight.js, Shiki, GitHub
        conventions all normalized),{" "}
        <Src path="src/docpull/conversion/markdown.py" line={32}>
          tables
        </Src>{" "}
        convert to Markdown pipes, and{" "}
        <Src path="src/docpull/conversion/extractor.py" line={109}>
          images
        </Src>{" "}
        keep their alt text. Nav bars, footers, sidebars, and common
        cookie/consent banners (OneTrust, Osano, GDPR walls, Cookiebot,
        Iubenda) are stripped before conversion via{" "}
        <Src path="src/docpull/conversion/extractor.py" line={42}>
          the extractor&apos;s remove-selector list
        </Src>
        .
      </>
    ),
    aText:
      "Yes. Fenced code blocks keep their language hints (Prism, highlight.js, Shiki, and GitHub conventions are all normalized), tables convert to Markdown pipes, and images keep their alt text. Nav bars, footers, sidebars, and common cookie/consent banners (OneTrust, Osano, GDPR walls, Cookiebot, Iubenda) are stripped before conversion via the extractor's remove-selector list.",
  },
  {
    q: "Does it render JavaScript?",
    a: (
      <>
        No. docpull runs no browser. Pages that require JS to render
        content are detected and skipped (or hard-failed with{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          --strict-js-required
        </code>
        ) so an agent can route elsewhere. For JS-rendered docs, use
        Firecrawl or Crawl4AI.
      </>
    ),
    aText:
      "No. docpull runs no browser. Pages that require JavaScript to render content are detected and skipped — or hard-failed with --strict-js-required — so an agent can route elsewhere. For JS-rendered docs, use Firecrawl or Crawl4AI.",
  },
  {
    q: "Will it scale to a 10,000-page site, and can I re-run it on a schedule?",
    a: (
      <>
        Yes — measured against a synthetic 10,000-page site:{" "}
        <strong>~27&nbsp;s wall time</strong>,{" "}
        <strong>~28&nbsp;MB peak RSS</strong>,{" "}
        <strong>p99 ~5&nbsp;ms</strong> per-page latency. See{" "}
        <Src path="tests/benchmarks/test_10k_pages.py">
          tests/benchmarks/test_10k_pages.py
        </Src>{" "}
        for the workload.{" "}
        <Src path="src/docpull/pipeline/steps/dedup.py">
          Streaming deduplication
        </Src>{" "}
        keeps memory constant per page; the cache sends{" "}
        <Src path="src/docpull/pipeline/steps/fetch.py">
          If-None-Match / If-Modified-Since
        </Src>{" "}
        on every cached URL so scheduled re-runs only transfer changed
        pages, and{" "}
        <Src path="src/docpull/cache/manager.py" line={45}>
          fetched and failed URL sets persist on disk
        </Src>{" "}
        so a crash resumes from the discovered-URL list instead of
        restarting.
      </>
    ),
    aText:
      "Yes — measured against a synthetic 10,000-page site: about 27 s wall time, about 28 MB peak RSS, and p99 around 5 ms per-page latency. Streaming deduplication keeps memory constant per page; the cache sends If-None-Match / If-Modified-Since on every cached URL so scheduled re-runs only transfer changed pages, and fetched and failed URL sets persist on disk so a crash resumes from the discovered-URL list instead of restarting.",
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
    aText:
      "Yes. Pass credentials with --auth-bearer, --auth-basic, --auth-cookie, or --auth-header. They ride with every request, so internal docs, subscriber-only content, and corporate wikis all work.",
  },
  {
    q: "Does the output drop straight into a Claude Code skill?",
    a: (
      <>
        Yes. Run{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          docpull URL --skill name
        </code>{" "}
        and docpull writes a complete skill directory to{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          .claude/skills/name/
        </code>
        : a generated{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          SKILL.md
        </code>{" "}
        manifest with{" "}
        <Src path="src/docpull/pipeline/steps/save.py">
          name and description fields
        </Src>{" "}
        derived from the source&apos;s OpenGraph metadata, plus
        hierarchically-named pages alongside it. No hand-editing
        required.
      </>
    ),
    aText:
      "Yes. Run `docpull URL --skill name` and docpull writes a complete skill directory to .claude/skills/name/: a generated SKILL.md manifest with name and description fields derived from the source's OpenGraph metadata, plus hierarchically-named pages alongside it. No hand-editing required.",
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
    aText:
      "Yes. Import Fetcher and DocpullConfig, configure programmatically, and iterate over async events as pages are fetched. See the Python tab above for a minimal setup.",
  },
];
