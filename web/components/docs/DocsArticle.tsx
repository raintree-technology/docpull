import Link from "next/link";
import {
  CheckCircle2,
  FileJson,
  FileText,
  Layers3,
  Lock,
  PackageCheck,
  Server,
  Sparkles,
  Terminal,
} from "lucide-react";
import DocsCodeBlock from "./DocsCodeBlock";
import {
  AnchorHeading,
  Callout,
  DocsTable,
  FactStrip,
  InlineCode,
  ResourceCard,
} from "./DocsPrimitives";
import { outputRows, overviewBadges, profileRows } from "./docs-data";

const overviewFacts = [
  { icon: Terminal, label: "CLI / SDK / MCP" },
  { icon: FileText, label: "Markdown + NDJSON" },
  { icon: Lock, label: "Local-first default" },
  { icon: PackageCheck, label: "v3 pack contract" },
] as const;

export function DocsArticle() {
  return (
    <>
      <div id="overview" className="scroll-mt-24">
        <div className="mb-5 flex flex-wrap items-center gap-2 text-sm leading-5 text-muted-foreground">
          <Link href="/docs" className="hover:text-foreground">
            Docs
          </Link>
          <span>/</span>
          <span>Get started</span>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {overviewBadges.map((badge) => (
            <span
              key={badge}
              className="inline-flex min-h-8 items-center rounded-md border bg-muted px-2.5 text-sm font-semibold leading-5"
            >
              {badge}
            </span>
          ))}
        </div>

        <h1 className="mt-5 text-4xl font-semibold leading-tight tracking-normal sm:text-5xl">
          Get started with docpull
        </h1>
        <p className="mt-5 text-lg leading-8 text-muted-foreground">
          docpull is a Python CLI, SDK, and MCP server that turns public or
          explicitly authorized static and server-rendered web pages into clean
          local artifacts for agents, RAG, and offline research.
        </p>
        <p className="mt-4 text-base leading-7 text-muted-foreground">
          It is browser-free by default, writes auditable files, and uses
          explicit rendering only when you ask.
        </p>
        <FactStrip facts={overviewFacts} />
      </div>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="install">Install</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Install the base CLI from PyPI. The default path handles static HTML
          and server-rendered pages without a browser or external account.
        </p>
        <DocsCodeBlock code="pip install docpull" />
        <p className="text-base leading-7 text-muted-foreground">
          Add extras only for the workflows you need:
        </p>
        <DocsCodeBlock
          code={`pip install 'docpull[llm]'           # token-aware chunking
pip install 'docpull[trafilatura]'   # alternate extractor
pip install 'docpull[mcp]'           # stdio MCP server
pip install 'docpull[serve]'         # local pack JSON server
pip install 'docpull[parse]'         # document parsing extras
pip install 'docpull[parquet]'       # Parquet export support
pip install 'docpull[all]'           # all optional extras`}
        />
        <DocsCodeBlock code="docpull --doctor" title="verify" />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="quickstart">Quickstart</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Pull one public page into a local directory:
        </p>
        <DocsCodeBlock code="docpull https://www.python.org/blogs/ --single -o ./python-news" />
        <p className="text-base leading-7 text-muted-foreground">
          A minimal run writes a readable Markdown file and a corpus manifest:
        </p>
        <DocsCodeBlock
          language="text"
          title="output"
          code={`python-news/
  index.md
  corpus.manifest.json`}
        />
        <p className="text-base leading-7 text-muted-foreground">
          For LLM and RAG pipelines, stream chunked records as NDJSON:
        </p>
        <DocsCodeBlock
          code={`docpull https://www.python.org/blogs/ \\
  --single \\
  --profile llm \\
  --stream | jq .`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="outputs">Outputs</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          File-backed runs write a <InlineCode>corpus.manifest.json</InlineCode>
          with stable document IDs, chunk IDs, hashes, output paths, and chunk
          counts.
        </p>
        <DocsTable rows={outputRows} />
        <DocsCodeBlock
          code={`docpull https://www.python.org/blogs/ --format markdown -o ./out/markdown
docpull https://www.python.org/blogs/ --format json -o ./out/json
docpull https://www.python.org/blogs/ --format ndjson -o ./out/ndjson
docpull https://www.python.org/blogs/ --format sqlite -o ./out/sqlite
docpull https://www.python.org/blogs/ --format okf -o ./out/okf`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="local-inputs">Local inputs</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Known-source pack builders normalize files, APIs, feeds, papers,
          repos, packages, standards, datasets, transcripts, and wiki pages
          into the same v3 pack contract.
        </p>
        <DocsCodeBlock
          code={`docpull parse ./handbook.pdf -o ./packs/handbook --backend auto
docpull openapi-pack ./openapi.json -o ./packs/api
docpull feed-pack ./feed.xml -o ./packs/feed
docpull paper-pack arxiv:1706.03762 -o ./packs/papers
docpull repo-pack psf/requests -o ./packs/repo --cache
docpull package-pack pypi:requests -o ./packs/package
docpull standards-pack rfc:9110 -o ./packs/standard
docpull dataset-pack ./metrics.csv -o ./packs/dataset
docpull transcript-pack ./meeting.vtt -o ./packs/transcript
docpull wiki-pack wiki:Web_scraping -o ./packs/wiki`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="pack-contract">Pack contract</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          A raw output directory becomes an agent-ready dependency only after it
          satisfies the v3 pack contract. The validator is the public source of
          truth for required records, sidecars, citations, rights, provenance,
          and audit artifacts.
        </p>
        <DocsCodeBlock
          code={`docpull pack validate ./packs/docs --level raw
docpull pack prepare ./packs/docs --eval-grade
docpull pack validate ./packs/docs --level eval --format json`}
        />
        <Callout icon={CheckCircle2} title="Three pack levels">
          Raw packs include corpus, sources, and acquisition sidecars. Agent
          packs add context locks, coverage, citations, scores, and audits.
          Eval packs add rights, provenance, basis artifacts, and a pack card.
        </Callout>
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="profiles">Profiles</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Profiles tune extraction, chunking, and artifact shape for common
          workflows.
        </p>
        <DocsTable rows={profileRows} />
        <DocsCodeBlock
          code={`docpull https://site.com --profile rag
docpull https://site.com --profile llm --stream
docpull https://site.com --profile okf -o ./site-okf
docpull https://site.com --profile mirror --cache -o ./site-mirror`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="zero-budget">Zero-budget runs</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Use <InlineCode>--budget 0</InlineCode> when a run must not make
          cloud rendering calls. Local cache, direct HTTP, sitemap discovery,
          extraction, indexing, pack analysis, and local rendering remain
          available.
        </p>
        <DocsCodeBlock
          code={`docpull https://docs.example.com --budget 0 -o ./docs/example
docpull render https://example.com/app --runtime local --budget 0
docpull ci --prepare --budget 0`}
        />
        <Callout icon={CheckCircle2} title="Accounting is explicit">
          Runs involving a budget or paid-capable route write
          <InlineCode>run.accounting.json</InlineCode> with non-secret route,
          cost, HTTP/cache, browser, and blocked-action metadata.
        </Callout>
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="python-sdk">Python SDK</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Use the SDK when docpull is part of a Python pipeline.
        </p>
        <DocsCodeBlock
          language="python"
          title="python"
          code={`from docpull import fetch_one

ctx = fetch_one("https://docs.python.org/3/library/asyncio.html")
print(ctx.title)
print(ctx.markdown[:500])`}
        />
        <DocsCodeBlock
          language="python"
          title="async workflow"
          code={`import asyncio
from docpull import Fetcher, DocpullConfig, EventType, ProfileName

async def main():
    cfg = DocpullConfig(url="https://example.com/blog", profile=ProfileName.LLM)
    async with Fetcher(cfg) as fetcher:
        async for event in fetcher.run():
            if event.type == EventType.FETCH_PROGRESS:
                print(f"{event.current}/{event.total}: {event.url}")

asyncio.run(main())`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="mcp-server">MCP server</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Install the MCP extra and run the Python stdio server:
        </p>
        <DocsCodeBlock
          code={`pip install 'docpull[mcp]'
docpull mcp`}
        />
        <p className="text-base leading-7 text-muted-foreground">
          Claude Code can add it directly:
        </p>
        <DocsCodeBlock code="claude mcp add --transport stdio docpull -- docpull mcp" />
        <p className="text-base leading-7 text-muted-foreground">
          Cursor and Claude Desktop use the same
          <InlineCode>mcpServers</InlineCode> shape:
        </p>
        <DocsCodeBlock
          language="json"
          title="mcp.json"
          code={`{
  "mcpServers": {
    "docpull": {
      "type": "stdio",
      "command": "docpull",
      "args": ["mcp"]
    }
  }
}`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="agent-skills">Agent skills</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Turn a source corpus into agent-ready skills or rules for Claude
          Code, Codex, and Cursor.
        </p>
        <DocsCodeBlock
          code={`docpull https://sdk.vercel.ai \\
  --skill vercel-ai \\
  --skill-agent all \\
  --skill-description "Vercel AI SDK source reference"`}
        />
        <ul className="list-disc space-y-2 pl-5 text-base leading-7 text-muted-foreground">
          <li>
            Claude Code wrappers are written under
            <InlineCode>.claude/skills/</InlineCode>.
          </li>
          <li>
            Codex wrappers are written under
            <InlineCode>.agents/skills/</InlineCode>.
          </li>
          <li>
            Cursor project rules are written under
            <InlineCode>.cursor/rules/</InlineCode>.
          </li>
        </ul>
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="rendering">Rendering fallback</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          docpull intentionally skips JS-only pages by default. Enable rendering
          explicitly for public pages that need a lightweight HTML-rendering
          pass.
        </p>
        <DocsCodeBlock
          code={`docpull render --check
docpull https://example.com/app --single --render fallback -o ./packs/rendered
docpull render https://example.com/app -o ./rendered`}
        />
        <p className="text-base leading-7 text-muted-foreground">
          Rendering requires an external
          <InlineCode>agent-browser</InlineCode> compatible executable on
          <InlineCode>PATH</InlineCode> or
          <InlineCode>DOCPULL_AGENT_BROWSER_BIN</InlineCode>.
        </p>
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="context-ci">Context CI and exports</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Keep source packs current in CI, then export validated records to the
          agent or data surface that consumes them.
        </p>
        <DocsCodeBlock
          code={`docpull ci --prepare
docpull export ./packs/docs --format openai-vector-jsonl -o ./exports/openai.jsonl
docpull export ./packs/docs --format claude-skill -o ./.claude/skills/docs
docpull export ./packs/docs --format cursor-rules -o ./.cursor/rules/docs.mdc`}
        />
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="security">Security defaults</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          docpull is built for public or explicitly authorized sources. Its
          default fetch path includes HTTPS defaults, robots.txt compliance,
          SSRF protections, private network blocking, DNS rebinding protection,
          sitemap XXE protection, path traversal guards, CRLF header injection
          guards, and cross-origin auth stripping.
        </p>
        <Callout icon={Lock} title="Know the boundary">
          For authenticated dashboards, CAPTCHA-protected pages, or complex
          browser workflows, use browser automation and pass the exported
          content into your pipeline.
        </Callout>
      </section>

      <section className="mt-11 space-y-5">
        <AnchorHeading id="troubleshooting">Troubleshooting</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          Start with deterministic local checks before changing your source
          policy.
        </p>
        <DocsCodeBlock
          code={`docpull --doctor
docpull render --check
docpull URL --verbose
docpull URL --dry-run
docpull URL --preview-urls`}
        />
      </section>

      <section id="recipes" className="mt-11 scroll-mt-24 space-y-5">
        <AnchorHeading id="resources">Resources</AnchorHeading>
        <p className="text-base leading-7 text-muted-foreground">
          The repository still contains the full long-form references. This
          page links into them while the website docs grow.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <ResourceCard
            href="https://github.com/raintree-technology/docpull/blob/main/docs/examples/README.md"
            icon={Terminal}
            title="CLI recipes"
          >
            Commands for source ingestion, formats, packs, rendering, exports,
            and monitors.
          </ResourceCard>
          <ResourceCard
            href="https://github.com/raintree-technology/docpull/blob/main/docs/scraping-boundary.md"
            icon={Layers3}
            title="Web source boundary"
          >
            What docpull fetches by default and when to use rendering.
          </ResourceCard>
          <ResourceCard
            href="https://github.com/raintree-technology/docpull/blob/main/docs/surface-contract.md"
            icon={Server}
            title="Surface contract"
          >
            How the CLI, Python SDK, and MCP surfaces align.
          </ResourceCard>
          <ResourceCard
            href="https://github.com/raintree-technology/docpull/blob/main/docs/CHANGELOG.md"
            icon={FileJson}
            title="Changelog"
          >
            Release notes and current package changes.
          </ResourceCard>
        </div>
        <div className="mt-8 rounded-lg border p-5">
          <Sparkles
            className="h-5 w-5 text-teal-700 dark:text-teal-300"
            aria-hidden="true"
          />
          <p className="mt-3 text-lg font-semibold leading-7">
            Ready for a real source pack?
          </p>
          <p className="mt-2 text-base leading-7 text-muted-foreground">
            Crawl a small public docs section, inspect the manifest, then move
            to profiles, exports, or MCP only when the local pack shape is
            right.
          </p>
          <DocsCodeBlock
            className="mb-0"
            code="docpull https://example.com/docs --max-pages 25 --max-depth 2 -o ./packs/example-docs"
          />
        </div>
      </section>
    </>
  );
}
