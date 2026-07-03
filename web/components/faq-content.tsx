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
    q: "How does docpull compare to browser automation or hosted extraction APIs?",
    a: (
      <>
        Browser automation is the right layer for interaction-heavy pages,
        sessions, clicks, and private apps. Hosted extraction APIs are useful
        when you want someone else to operate rendering or source discovery.
        docpull is narrower: it runs locally, starts from selected URLs or
        files, and writes auditable context artifacts with{" "}
        <Src path="src/docpull/models/profiles.py">
          rag / mirror / quick profiles
        </Src>{" "}
        and v3 pack validation.
      </>
    ),
    aText:
      "Browser automation is the right layer for interaction-heavy pages, sessions, clicks, and private apps. Hosted extraction APIs are useful when you want someone else to operate rendering or source discovery. docpull is narrower: it runs locally, starts from selected URLs or files, and writes auditable context artifacts with rag, mirror, quick profiles, and v3 pack validation.",
  },
  {
    q: "How clean is the Markdown? Does it preserve code blocks, tables, and images?",
    a: (
      <>
        Yes.{" "}
        <Src path="src/docpull/conversion/extractor.py" line={110}>
          Fenced code blocks
        </Src>{" "}
        keep language hints from common Prism, highlight.js, Shiki, and GitHub
        class conventions,{" "}
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
      "Yes. Fenced code blocks keep language hints from common Prism, highlight.js, Shiki, and GitHub class conventions, tables convert to Markdown pipes, and images keep their alt text. Nav bars, footers, sidebars, and common cookie/consent banners (OneTrust, Osano, GDPR walls, Cookiebot, Iubenda) are stripped before conversion via the extractor's remove-selector list.",
  },
  {
    q: "Does it render JavaScript?",
    a: (
      <>
        Not by default. The default fetch path runs without a browser. Pages that
        require JS to render content are detected and skipped (or hard-failed
        with{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          --strict-js-required
        </code>
        ) so an agent can route elsewhere. For simple JS-rendered public pages,
        use{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          --render fallback
        </code>{" "}
        or{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          docpull render
        </code>
        . For interaction-heavy pages, use a browser automation tool.
      </>
    ),
    aText:
      "Not by default. The default fetch path runs without a browser. Pages that require JavaScript to render content are detected and skipped, or hard-failed with --strict-js-required, so an agent can route elsewhere. For simple JavaScript-rendered public pages, use --render fallback or docpull render. For interaction-heavy pages, use a browser automation tool.",
  },
  {
    q: "Will it scale to large sites, and can I re-run it on a schedule?",
    a: (
      <>
        Yes. DocPull streams page records and uses{" "}
        <Src path="src/docpull/pipeline/steps/dedup.py">
          Streaming deduplication
        </Src>{" "}
        keeps memory constant per page; when a cached response has validators,
        the fetch path sends{" "}
        <Src path="src/docpull/pipeline/steps/fetch.py">
          If-None-Match / If-Modified-Since
        </Src>{" "}
        so unchanged pages can 304-skip without re-downloading, and{" "}
        <Src path="src/docpull/cache/manager.py" line={45}>
          fetched and failed URL sets persist on disk
        </Src>{" "}
        so a crash resumes from the discovered-URL list instead of
        restarting.
      </>
    ),
    aText:
      "Yes. DocPull streams page records and uses streaming deduplication to keep memory bounded per page. When a cached response has validators, the fetch path sends If-None-Match / If-Modified-Since so unchanged pages can 304-skip without re-downloading, and fetched and failed URL sets persist on disk so a crash resumes from the discovered-URL list instead of restarting.",
  },
  {
    q: "Does it handle auth-gated pages?",
    a: (
      <>
        Yes. Pass credentials with{" "}
        <Src path="src/docpull/cli.py" line={202}>
          --auth-bearer, --auth-basic, --auth-cookie, or --auth-header
        </Src>
        . DocPull attaches them to its HTTP fetches, so HTTP-reachable static or
        server-rendered internal docs, subscriber pages, and wikis can use the
        same fetch path. SSO/MFA, JS-only portals, and interaction-heavy apps
        may require a browser workflow.
      </>
    ),
    aText:
      "Yes. Pass credentials with --auth-bearer, --auth-basic, --auth-cookie, or --auth-header. DocPull attaches them to its HTTP fetches, so HTTP-reachable static or server-rendered internal docs, subscriber pages, and wikis can use the same fetch path. SSO/MFA, JS-only portals, and interaction-heavy apps may require a browser workflow.",
  },
  {
    q: "What makes a v3 pack agent-ready?",
    a: (
      <>
        A raw fetch is not automatically an agent contract. Run{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          docpull pack prepare
        </code>{" "}
        to add a context lock, coverage report, citation index, score, and
        audit. Eval-grade packs add rights and provenance sidecars. Then use{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          docpull pack validate --level raw|agent|eval
        </code>{" "}
        as the public contract checker.
      </>
    ),
    aText:
      "A raw fetch is not automatically an agent contract. Run docpull pack prepare to add a context lock, coverage report, citation index, score, and audit. Eval-grade packs add rights and provenance sidecars. Then use docpull pack validate --level raw|agent|eval as the public contract checker.",
  },
  {
    q: "Does the output drop straight into a Claude Code skill?",
    a: (
      <>
        Yes. Run{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          docpull URL --skill name
        </code>{" "}
        and docpull writes a Claude Code skill directory to{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          .claude/skills/name/
        </code>
        : a generated{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          SKILL.md
        </code>{" "}
        manifest with{" "}
        <Src path="src/docpull/pipeline/steps/save.py">
          name and description fields
        </Src>{" "}
        derived from extracted page metadata when available, plus
        hierarchically-named pages alongside it. Use{" "}
        <code className="px-1 py-0.5 rounded bg-foreground/5 font-mono text-[13px] leading-5">
          --skill-description
        </code>{" "}
        when you want to override the generated description.
      </>
    ),
    aText:
      "Yes. Run `docpull URL --skill name` and docpull writes a Claude Code skill directory to .claude/skills/name/: a generated SKILL.md manifest with name and description fields derived from extracted page metadata when available, plus hierarchically-named pages alongside it. Use --skill-description when you want to override the generated description.",
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
