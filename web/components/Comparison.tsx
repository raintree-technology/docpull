import { ExternalLink } from "lucide-react";
import { GlassPanel, LandingSection } from "@/components/landing";
import { cn } from "@/lib/utils";

type Product = {
  name: string;
  href: string;
  accent: string;
};

type ComparisonRow = {
  criterion: string;
  docpull: string;
  exa: string;
  parallel: string;
  tavily: string;
};

const products = [
  {
    name: "DocPull",
    href: "https://github.com/raintree-technology/docpull",
    accent: "bg-foreground",
  },
  {
    name: "Exa",
    href: "https://exa.ai/docs/reference/search-api-guide",
    accent: "bg-sky-500",
  },
  {
    name: "Parallel",
    href: "https://docs.parallel.ai/getting-started/overview",
    accent: "bg-violet-500",
  },
  {
    name: "Tavily",
    href: "https://docs.tavily.com/welcome",
    accent: "bg-emerald-500",
  },
] as const satisfies readonly Product[];

const rows = [
  {
    criterion: "Best role",
    docpull:
      "Local-first source pipeline for known URLs, source lists, pack audits, exports, and MCP-ready context.",
    exa: "AI search, contents extraction, deep research, structured outputs, and monitors.",
    parallel:
      "LLM-optimized search and extract APIs plus repeatable task research with citations.",
    tavily:
      "Search, extract, crawl, map, and research API for agent web access.",
  },
  {
    criterion: "Starting point",
    docpull:
      "A URL, sitemap, explicit source list, existing pack, or provider-discovered candidates.",
    exa: "A query, URL, category, structured schema, monitor, or agent research task.",
    parallel:
      "A natural-language objective, search query set, URL list, or task spec.",
    tavily:
      "A search query, known URL, website root, crawl instruction, or research prompt.",
  },
  {
    criterion: "Primary output",
    docpull:
      "Markdown, NDJSON, SQLite, OKF, manifests, citations, entity maps, briefs, and local pack routes.",
    exa: "Ranked results, highlights, full text, summaries, grounded answers, and JSON fields.",
    parallel:
      "LLM-ready excerpts, clean markdown, structured task outputs, citations, and confidence signals.",
    tavily:
      "Search results, extracted page content, site maps, crawls, and research reports.",
  },
  {
    criterion: "Local corpus",
    docpull:
      "First-class: cache, resume, refresh, diff, audit, answer-pack, monitor, export, and serve.",
    exa: "API-first. Persist results yourself when you need a durable local corpus.",
    parallel:
      "API-first, with DocPull integration for local context packs from Parallel results.",
    tavily: "API-first. Store crawl, extract, or research results in your own pipeline.",
  },
  {
    criterion: "Agent surface",
    docpull:
      "CLI, Python SDK, MCP tools, pack server, and agent skill or rule exports.",
    exa: "SDKs, API docs, OpenAI compatibility, MCP, and coding-agent integration guidance.",
    parallel:
      "Python and TypeScript SDKs, API docs, MCP search tooling, and agent setup prompts.",
    tavily:
      "REST API, Python and JavaScript SDKs, CLI, LangChain integration, and agent skills.",
  },
  {
    criterion: "Choose it when",
    docpull:
      "You need repeatable local artifacts an agent can inspect, cite, diff, refresh, and reuse offline.",
    exa: "You need a high-quality AI search layer with token-dense contents or structured web research.",
    parallel:
      "You need web search or extraction shaped for model context, or long-running research tasks.",
    tavily:
      "You need a broad hosted web API that covers search, extraction, crawl, map, and research.",
  },
] as const satisfies readonly ComparisonRow[];

const sources = [
  {
    label: "Exa docs",
    href: "https://exa.ai/docs/reference/search-api-guide",
  },
  {
    label: "Parallel docs",
    href: "https://docs.parallel.ai/getting-started/overview",
  },
  {
    label: "Tavily docs",
    href: "https://docs.tavily.com/welcome",
  },
] as const;

export default function Comparison() {
  return (
    <LandingSection
      id="compare"
      title="DocPull vs hosted web APIs"
      description="Exa, Parallel, and Tavily are strong hosted web-intelligence layers. DocPull is the local artifact layer that turns selected sources into repeatable context packs, audits, exports, and MCP tools."
      containerClassName="max-w-6xl"
      headerClassName="mb-10"
      bordered={false}
    >
      <GlassPanel className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[980px] border-collapse text-left">
            <caption className="sr-only">
              Comparison of DocPull, Exa, Parallel, and Tavily across role,
              inputs, outputs, local corpus support, agent surfaces, and fit.
            </caption>
            <thead>
              <tr className="border-b border-foreground/10">
                <th
                  scope="col"
                  className="w-[150px] px-4 py-4 text-xs font-semibold uppercase leading-5 tracking-[0.12em] text-muted-foreground"
                >
                  Dimension
                </th>
                {products.map((product) => (
                  <th
                    key={product.name}
                    scope="col"
                    className="min-w-[205px] px-4 py-4 align-top"
                  >
                    <a
                      href={product.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-2 text-base font-semibold leading-6 transition-colors hover:text-muted-foreground"
                    >
                      <span
                        className={cn("h-2 w-2 rounded-full", product.accent)}
                        aria-hidden="true"
                      />
                      {product.name}
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden />
                    </a>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.criterion}
                  className="border-b border-foreground/10 last:border-b-0"
                >
                  <th
                    scope="row"
                    className="px-4 py-4 align-top text-sm font-semibold leading-6 text-foreground"
                  >
                    {row.criterion}
                  </th>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.docpull}
                  </td>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.exa}
                  </td>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.parallel}
                  </td>
                  <td className="px-4 py-4 align-top text-sm leading-6 text-muted-foreground">
                    {row.tavily}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="border-t border-foreground/10 px-4 py-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              Use hosted APIs to find, rank, enrich, or research the web. Use
              DocPull when those selected sources need to become a durable local
              corpus with file paths, manifests, citations, and repeatable
              audits.
            </p>
            <div className="flex flex-wrap gap-2">
              {sources.map((source) => (
                <a
                  key={source.href}
                  href={source.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex min-h-9 items-center gap-1.5 rounded-md border bg-background/55 px-3 py-1.5 text-xs font-semibold leading-4 text-muted-foreground transition-colors hover:text-foreground"
                >
                  {source.label}
                  <ExternalLink className="h-3 w-3" aria-hidden />
                </a>
              ))}
            </div>
          </div>
        </div>
      </GlassPanel>
    </LandingSection>
  );
}
