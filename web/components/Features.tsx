import {
  Bot,
  Database,
  FileText,
  GitBranch,
  RefreshCcw,
  ShieldCheck,
  Terminal,
  type LucideIcon,
} from "lucide-react";
import { GlassPanel, LandingSection } from "@/components/landing";
import { cn } from "@/lib/utils";

type FeatureGroup = {
  title: string;
  eyebrow: string;
  description: string;
  command: string;
  Icon: LucideIcon;
  features: readonly string[];
};

const featureGroups = [
  {
    title: "Declare context dependencies",
    eyebrow: "Lockfile",
    description:
      "Put source dependencies in a project file, lock them, and diff changes before agents rely on them.",
    command: "docpull init && docpull add stripe react && docpull sync",
    Icon: GitBranch,
    features: [
      "docpull.yaml plus lockfile workflow for repeatable source sets",
      "Sync, diff, status, history, review, release, watch, and refresh commands",
      "Changed-source review artifacts for pull requests and Context CI",
      "No external account or hosted registry required for the local workflow",
    ],
  },
  {
    title: "Fetch and render explicitly",
    eyebrow: "Capture",
    description:
      "Start from one public URL, a site section, a stored source list, or an explicit render request.",
    command: "docpull URL --profile rag",
    Icon: FileText,
    features: [
      "Static and server-rendered HTML to Markdown, NDJSON, SQLite, or OKF",
      "Profiles for RAG, mirrors, quick samples, LLM chunks, OKF, and SEC filings",
      "Depth, page, path, concurrency, per-host, proxy, retry, and tokenizer controls",
      "Optional agent-browser rendering with local, Vercel, and E2B runtimes",
    ],
  },
  {
    title: "Safety and repeatability",
    eyebrow: "Guardrails",
    description:
      "Designed for agent-driven fetches where URLs, credentials, and reruns need clear boundaries.",
    command: "docpull policy validate source_policy.json",
    Icon: ShieldCheck,
    features: [
      "HTTPS and SSRF validation, robots.txt handling, pinned-DNS checks, and strict TLS defaults",
      "Auth policy labels plus bearer, basic, cookie, and custom-header checks",
      "Cache, resume, conditional fetches, dry runs, and rerun controls",
      "Doctor diagnostics, policy checks, and CI-friendly regression tests",
    ],
  },
  {
    title: "Validate v3 packs",
    eyebrow: "Contract",
    description:
      "Prepare raw outputs into agent-ready or eval-grade packs with required sidecars.",
    command: "docpull pack validate ./pack --level eval",
    Icon: Database,
    features: [
      "Raw, agent, and eval validation levels with text and JSON output",
      "Context locks, coverage reports, citation indexes, pack scores, and audits",
      "Rights manifests, provenance graphs, basis artifacts, and PACK_CARD.md",
      "Precise source-level and chunk-level citation IDs for downstream use",
    ],
  },
  {
    title: "Export and enforce in CI",
    eyebrow: "Delivery",
    description:
      "Send validated packs to agent frameworks, data warehouses, local servers, or CI checks.",
    command: "docpull export ./pack --format openai-vector-jsonl",
    Icon: RefreshCcw,
    features: [
      "OpenAI, LangChain, LlamaIndex, DSPy, n8n, Vercel AI, CrewAI, Sheets, and warehouse exports",
      "Claude skill, Codex skill, and Cursor rules exports",
      "Context CI reports for stale, missing, or weak evidence before release",
      "Optional Parquet lane documented as docpull[parquet]",
    ],
  },
  {
    title: "Agent and developer surfaces",
    eyebrow: "Interfaces",
    description:
      "Aligned core workflows are available to humans, Python code, and MCP clients.",
    command: "docpull mcp",
    Icon: Bot,
    features: [
      "CLI commands for full operator workflows and file outputs",
      "Python SDK exports for fetch, render, chunk, refresh, audit, export, and serve",
      "MCP tools for fetch, render, ensure, list, search, read, packs, policy, and exports",
      "Local pack server plus JSONL, agent skill, and rule exports",
    ],
  },
] as const satisfies readonly FeatureGroup[];

const surfaceRows = [
  {
    label: "CLI",
    Icon: Terminal,
    items: [
      "fetch",
      "render",
      "refresh",
      "pack",
      "ci",
      "export",
      "serve",
      "monitor",
      "openapi-pack",
      "typed packs",
    ],
  },
  {
    label: "SDK",
    Icon: FileText,
    items: [
      "Fetcher",
      "RenderConfig",
      "PolicyConfig",
      "refresh_pack",
      "audit_pack",
      "export_pack",
      "build_paper_pack",
      "load_pack",
      "create_pack_app",
    ],
  },
  {
    label: "MCP",
    Icon: Bot,
    items: [
      "fetch_url",
      "render_url",
      "ensure_docs",
      "grep_docs",
      "read_doc",
      "source aliases",
      "pack_diff",
      "audit_pack",
      "validate_policy",
      "export_pack",
    ],
  },
  {
    label: "Outputs",
    Icon: RefreshCcw,
    items: [
      "Markdown",
      "frontmatter",
      "NDJSON",
      "SQLite",
      "OKF",
      "chunks",
      "citations",
      "sidecars",
      "skills",
      "server routes",
    ],
  },
] as const;

export default function Features() {
  return (
    <LandingSection
      id="features"
      title="Capability map"
      description="DocPull is a local-first context dependency pipeline for capture, policy, v3 pack validation, exports, CI, and agent tools."
      headerClassName="mb-10"
      containerClassName="max-w-6xl"
    >
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-6">
        {featureGroups.map((group, index) => (
          <FeatureCard
            key={group.title}
            group={group}
            className={cn(index < 2 ? "lg:col-span-3" : "lg:col-span-2")}
          />
        ))}
      </div>

      <GlassPanel className="mt-4 p-4 sm:p-5">
        <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="font-mono text-xs font-semibold uppercase leading-5 tracking-[0.12em] text-muted-foreground">
              Surface index
            </p>
            <h3 className="text-base font-semibold leading-6">
              Core workflows are exposed where agents and developers need them.
            </h3>
          </div>
          <p className="max-w-md text-sm leading-6 text-muted-foreground">
            Names differ by surface, but the durable capabilities stay aligned.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {surfaceRows.map((surface) => (
            <div
              key={surface.label}
              className="grid grid-cols-[auto_minmax(0,1fr)] gap-3"
            >
              <div className="flex h-9 w-9 items-center justify-center rounded-md border bg-background/60">
                <surface.Icon className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <h4 className="mb-2 text-sm font-semibold leading-5">
                  {surface.label}
                </h4>
                <ul className="flex flex-wrap gap-1.5">
                  {surface.items.map((item) => (
                    <li
                      key={item}
                      className="rounded-md border bg-background/45 px-2 py-1 font-mono text-[11px] leading-4 text-muted-foreground"
                    >
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>
      </GlassPanel>
    </LandingSection>
  );
}

function FeatureCard({
  group,
  className,
}: {
  group: FeatureGroup;
  className?: string;
}) {
  return (
    <GlassPanel className={cn("flex h-full flex-col p-5", className)}>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="mb-1 font-mono text-xs font-semibold uppercase leading-5 tracking-[0.12em] text-muted-foreground">
            {group.eyebrow}
          </p>
          <h3 className="text-lg font-semibold leading-7">{group.title}</h3>
        </div>
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md border bg-background/60">
          <group.Icon className="h-5 w-5" aria-hidden="true" />
        </div>
      </div>

      <p className="text-[15px] leading-7 text-muted-foreground">
        {group.description}
      </p>

      <code className="mt-4 block overflow-x-auto rounded-md bg-background/60 px-3 py-2.5 font-mono text-[12px] leading-5 text-foreground/85">
        {group.command}
      </code>

      <ul className="mt-4 space-y-2">
        {group.features.map((feature) => (
          <li
            key={feature}
            className="grid grid-cols-[auto_minmax(0,1fr)] gap-2 text-sm leading-6 text-muted-foreground"
          >
            <span
              aria-hidden="true"
              className="mt-2.5 h-1.5 w-1.5 rounded-full bg-foreground/60"
            />
            <span>{feature}</span>
          </li>
        ))}
      </ul>
    </GlassPanel>
  );
}
