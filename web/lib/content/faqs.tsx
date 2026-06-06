import { type ReactNode } from "react";

// Shared FAQ source of truth.
// - `a`     - rich JSX rendered in the FAQ accordion with source links.
// - `aText` - plain-text equivalent used for FAQPage JSON-LD.
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
        Firecrawl and Jina Reader are hosted APIs: your URLs go through their
        infrastructure, and pricing changes once you leave the free tier.
        docpull runs locally, so the crawl stays on your machine. Crawl4AI is
        the closest OSS comparison, but it&apos;s a broader crawling toolkit.
        docpull is narrower by design: it focuses on server-rendered web
        content and writes YAML-frontmatter Markdown with{" "}
        <Src path="src/docpull/models/profiles.py">
          rag / mirror / quick / llm profiles
        </Src>{" "}
        built in.
      </>
    ),
    aText:
      "Firecrawl and Jina Reader are hosted APIs: your URLs go through their infrastructure, and pricing changes once you leave the free tier. docpull runs locally, so the crawl stays on your machine. Crawl4AI is the closest open-source comparison, but it is a broader crawling toolkit. docpull is narrower by design: it focuses on server-rendered web content and writes YAML-frontmatter Markdown with rag, mirror, quick, and llm profiles built in.",
  },
  {
    q: "How clean is the Markdown? Does it preserve code blocks, tables, and images?",
    a: (
      <>
        Yes.{" "}
        <Src path="src/docpull/conversion/extractor.py">
          Fenced code blocks
        </Src>{" "}
        keep their language hints, with Prism, highlight.js, Shiki, and GitHub
        conventions normalized.{" "}
        <Src path="src/docpull/conversion/markdown.py">Tables</Src> convert to
        Markdown pipes, and{" "}
        <Src path="src/docpull/conversion/extractor.py">images</Src> keep their
        alt text. Nav bars, footers, sidebars, and common cookie and consent
        banners such as OneTrust, Osano, GDPR walls, Cookiebot, and Iubenda are
        stripped before conversion via{" "}
        <Src path="src/docpull/conversion/extractor.py">
          the extractor&apos;s remove-selector list
        </Src>
        .
      </>
    ),
    aText:
      "Yes. Fenced code blocks keep their language hints, with Prism, highlight.js, Shiki, and GitHub conventions normalized. Tables convert to Markdown pipes, and images keep their alt text. Nav bars, footers, sidebars, and common cookie and consent banners such as OneTrust, Osano, GDPR walls, Cookiebot, and Iubenda are stripped before conversion via the extractor's remove-selector list.",
  },
  {
    q: "Does it render JavaScript-heavy sites?",
    a: (
      <>
        No. docpull does not run a browser. Pages that need JavaScript to render
        content are detected and skipped, or hard-failed with{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          --strict-js-required
        </code>
        , so an agent can route elsewhere. For JS-rendered sites, use Firecrawl
        or Crawl4AI instead.
      </>
    ),
    aText:
      "No. docpull does not run a browser. Pages that need JavaScript to render content are detected and skipped. Use --strict-js-required if you want that to fail loudly so an agent can route elsewhere. For JS-rendered sites, use Firecrawl or Crawl4AI instead.",
  },
  {
    q: "Will it scale to a 10,000-page site, and can I re-run it on a schedule?",
    a: (
      <>
        Yes. On a synthetic 10,000-page site, it measured{" "}
        <strong>~27&nbsp;s wall time</strong>,{" "}
        <strong>~28&nbsp;MB peak RSS</strong>,{" "}
        <strong>p99 ~5&nbsp;ms</strong> per-page latency. See{" "}
        <Src path="tests/benchmarks/test_10k_pages.py">
          tests/benchmarks/test_10k_pages.py
        </Src>{" "}
        for the benchmark workload.{" "}
        <Src path="src/docpull/pipeline/steps/dedup.py">
          Streaming deduplication
        </Src>{" "}
        keeps memory flat per page. The cache sends{" "}
        <Src path="src/docpull/pipeline/steps/fetch.py">
          If-None-Match / If-Modified-Since
        </Src>{" "}
        on cached URLs, so scheduled re-runs only transfer changed pages, and{" "}
        <Src path="src/docpull/cache/manager.py">
          fetched and failed URL sets persist on disk
        </Src>{" "}
        so a crash can resume from the discovered-URL list.
      </>
    ),
    aText:
      "Yes. On a synthetic 10,000-page site, it measured about 27 s wall time, about 28 MB peak RSS, and p99 around 5 ms per-page latency. Streaming deduplication keeps memory flat per page, the cache sends If-None-Match and If-Modified-Since on cached URLs so scheduled re-runs only transfer changed pages, and fetched and failed URL sets persist on disk so a crash can resume from the discovered-URL list.",
  },
  {
    q: "Does it handle auth-gated sites?",
    a: (
      <>
        Yes. Pass credentials with{" "}
        <Src path="src/docpull/cli.py">
          --auth-bearer, --auth-basic, --auth-cookie, or --auth-header
        </Src>
        . They are sent with every request, so internal sites, subscriber-only
        content, docs, and corporate wikis work.
      </>
    ),
    aText:
      "Yes. Pass credentials with --auth-bearer, --auth-basic, --auth-cookie, or --auth-header. They are sent with every request, so internal sites, subscriber-only content, docs, and corporate wikis work.",
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
        . It includes a generated{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-xs">
          SKILL.md
        </code>{" "}
        manifest with{" "}
        <Src path="src/docpull/pipeline/steps/save.py">
          name and description fields
        </Src>{" "}
        derived from the source&apos;s OpenGraph metadata, plus hierarchically
        named pages alongside it. No hand editing required.
      </>
    ),
    aText:
      "Yes. Run `docpull URL --skill name` and docpull writes a complete skill directory to .claude/skills/name/. It includes a generated SKILL.md manifest with name and description fields derived from the source's OpenGraph metadata, plus hierarchically named pages alongside it. No hand editing required.",
  },
  {
    q: "Can I use it as a Python library?",
    a: (
      <>
        Yes. Import{" "}
        <Src path="src/docpull/__init__.py">Fetcher and DocpullConfig</Src>,
        configure programmatically, and iterate over async events as pages are
        fetched. See the Python tab above for a minimal setup.
      </>
    ),
    aText:
      "Yes. Import Fetcher and DocpullConfig, configure programmatically, and iterate over async events as pages are fetched. See the Python tab above for a minimal setup.",
  },
];
