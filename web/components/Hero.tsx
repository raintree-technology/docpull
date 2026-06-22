import { ArrowRight } from "lucide-react";
import {
  CommandCopy,
  StatGrid,
  TerminalPanel,
  type TerminalLine,
} from "@/components/landing";

const terminalLines = [
  {
    type: "command",
    content: "docpull https://www.python.org/blogs/ --profile rag -o ./python-news",
  },
  { type: "output", content: "" },
  { type: "dim", content: "robots.txt allowed; discovered 38 pages" },
  { type: "normal", content: "fetching static HTML with conditional cache" },
  { type: "normal", content: "[==============================] 38/38" },
  { type: "success", content: "wrote Markdown, NDJSON, manifest, and sources.md" },
  { type: "dim", content: "done in 12s; 2.8 MB saved to ./python-news" },
] as const satisfies readonly TerminalLine[];

const INSTALL_COMMAND = "pip install docpull";

const proofPoints = [
  { label: "Surfaces", value: "CLI / SDK / MCP" },
  { label: "Outputs", value: "Markdown + NDJSON" },
  { label: "Default", value: "No browser, no API key" },
] as const;

export default function Hero() {
  return (
    <section className="relative flex items-center justify-center pt-24 pb-12 sm:pt-28 sm:pb-16 lg:pt-32">
      <div className="mx-auto w-full max-w-6xl px-6">
        <div className="max-w-3xl">
          <p className="mb-4 inline-flex items-center gap-2 rounded-md border bg-background/80 px-3 py-1.5 text-sm font-semibold leading-5 text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
            docpull
          </p>

          <h1 className="max-w-4xl text-4xl font-semibold leading-[1.16] sm:text-5xl sm:leading-[1.15] lg:text-6xl lg:leading-[1.15]">
            Public web to agent-ready Markdown.
          </h1>

          <p className="mt-5 max-w-2xl text-lg leading-8 text-muted-foreground">
            Fetch public web sources locally, use explicit rendering only when
            needed, then hand clean Markdown, NDJSON, and context packs to
            coding agents, MCP clients, and RAG pipelines.
          </p>
        </div>

        <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:items-center">
          <CommandCopy command={INSTALL_COMMAND} />

          <div className="grid grid-cols-2 gap-3 sm:flex sm:items-center">
            <a
              href="/docs"
              className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg bg-foreground px-4 py-2.5 text-[15px] font-semibold leading-5 text-background transition-opacity hover:opacity-90"
            >
              Read docs
              <ArrowRight className="h-4 w-4" />
            </a>

            <a
              href="https://github.com/raintree-technology/docpull"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center justify-center rounded-lg border bg-background/80 px-4 py-2.5 text-[15px] font-semibold leading-5 transition-colors hover:bg-muted"
            >
              GitHub
            </a>
          </div>
        </div>

        <StatGrid
          stats={proofPoints}
          className="mt-7 hidden grid-cols-3 sm:grid sm:max-w-3xl"
        />

        <TerminalPanel
          lines={terminalLines}
          title="rag crawl"
          className="mt-8 w-full max-w-5xl"
        />
      </div>
    </section>
  );
}
