import Image from "next/image";

const workflows = [
  {
    title: "Discovery + Extract Packs",
    command: "context-pack / discover-docs",
    description:
      "Parallel discovers and extracts current web sources; docpull ranks candidates, writes crawl plans, AGENT_CONTEXT.md, Markdown, NDJSON chunks, source indexes, manifests, IDs, hashes, and usage metadata.",
  },
  {
    title: "Fallback + Diff Packs",
    command: "fallback-pack / diff-brief",
    description:
      "Try core docpull first, fall back to Parallel Extract only for misses, then score sources or send pack diffs through Parallel Task for change briefs.",
  },
  {
    title: "Entity Dossiers",
    command: "entity-pack / findall-pack",
    description:
      "Entity Search and FindAll become local candidate packs for companies, people, vendors, competitors, or research targets.",
  },
  {
    title: "Batch + Monitor Packs",
    command: "taskgroup-pack --wait / monitor-pack",
    description:
      "TaskGroup rows can wait for completed outputs, while Monitor create, list, retrieve, update, cancel, trigger, and event pages become reusable local artifacts.",
  },
  {
    title: "API Context Packs",
    command: "api-pack / pack score / pack sources",
    description:
      "Turn llms.txt and OpenAPI specs into docpull packs, then grade readiness, rank sources, or diff refreshed snapshots before agents load the context.",
  },
] as const;

const decisionCards = [
  {
    title: "Use core docpull for known docs",
    description:
      "Start with the local crawler when you already know the docs URL and want a same-domain Markdown mirror with no browser and no API key.",
    points: [
      "static docs and API references",
      "RAG or skill-ready Markdown",
      "repeatable site mirrors",
    ],
  },
  {
    title: "Use Parallel packs for web research",
    description:
      "Add the Parallel layer when discovery, extraction, research, entities, or monitoring should happen before docpull writes local context artifacts and a load plan.",
    points: [
      "research packs from search queries",
      "ranked docs discovery and crawl commands",
      "cited source bundles for agents",
      "AGENT_CONTEXT.md load plan",
      "repeatable NDJSON, manifests, and source files",
      "API-doc or vendor comparison research",
      "fallback, diff, task, entity, batch, and monitor workflows",
    ],
  },
] as const;

const keyFlow = [
  "pip install 'docpull[parallel]'",
  "docpull parallel init",
  "docpull parallel auth --json",
  "docpull parallel init --project",
  "docpull parallel context-pack ... --dry-run --max-estimated-cost 0.05",
] as const;

const controls = [
  "--dry-run",
  "--max-estimated-cost",
  "--include-domain / --exclude-domain",
  "--after-date",
  "--fetch-max-age-seconds",
  "--excerpt-chars-per-result",
  "--client-model",
  "pack sources",
] as const;

export default function ParallelPacks() {
  return (
    <section id="parallel" className="py-16 sm:py-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 sm:mb-12 text-center sm:text-left">
          <h2
            aria-label="Parallel context packs"
            className="mb-2 sm:mb-3 flex flex-col items-center gap-2 text-xl sm:text-2xl font-medium sm:flex-row sm:gap-3"
          >
            <Image
              src="/parallel-wordmark.svg"
              alt=""
              aria-hidden="true"
              width={149}
              height={46}
              unoptimized
              className="h-7 w-auto shrink-0 invert dark:invert-0"
            />
            <span>
              <span className="sr-only">Parallel </span>
              context packs
            </span>
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground max-w-3xl">
            Parallel is the optional source-discovery and research layer. Use
            core docpull to mirror a known docs site; use Parallel when an agent
            needs current web sources found, extracted, scored, and packaged
            into a local context pack before it starts work.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 sm:gap-4 mb-4 sm:mb-6">
          {decisionCards.map((card) => (
            <div key={card.title} className="p-4 sm:p-5 rounded-xl glass">
              <h3 className="font-medium text-sm mb-2">{card.title}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed mb-3">
                {card.description}
              </p>
              <ul className="space-y-1.5">
                {card.points.map((point) => (
                  <li
                    key={point}
                    className="flex gap-2 text-xs text-muted-foreground leading-relaxed"
                  >
                    <span aria-hidden="true" className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-foreground/50" />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[1.1fr_0.9fr] gap-4 sm:gap-6 mb-4 sm:mb-6">
          <div className="p-4 sm:p-5 rounded-xl glass">
            <h3 className="font-medium text-sm mb-3">API key flow</h3>
            <div className="space-y-2">
              {keyFlow.map((command) => (
                <code
                  key={command}
                  className="block px-3 py-2 bg-background/60 rounded-md text-xs font-mono text-muted-foreground overflow-x-auto"
                >
                  {command}
                </code>
              ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground leading-relaxed">
              Keys live in the environment, user config, or project .env.local.
              docpull does not echo{" "}
              <code className="font-mono text-[11px]">PARALLEL_API_KEY</code>,
              but pack artifacts can include source content, task inputs,
              outputs, and metadata.
            </p>
          </div>

          <div className="p-4 sm:p-5 rounded-xl glass">
            <h3 className="font-medium text-sm mb-3">Cost and source controls</h3>
            <div className="flex flex-wrap gap-2">
              {controls.map((control) => (
                <code
                  key={control}
                  className="px-2.5 py-1.5 bg-background/60 rounded-md text-[11px] font-mono text-muted-foreground"
                >
                  {control}
                </code>
              ))}
            </div>
            <p className="mt-3 text-xs text-muted-foreground leading-relaxed">
              Dry runs estimate spend before live calls, domain filters pin the
              source policy, and AGENT_CONTEXT.md gives agents a deterministic
              load order before they inspect deeper metadata.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
          {workflows.map((workflow) => (
            <div key={workflow.title} className="p-4 rounded-xl glass">
              <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
                <h3 className="font-medium text-sm">{workflow.title}</h3>
                <code className="text-[11px] font-mono text-muted-foreground">
                  {workflow.command}
                </code>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {workflow.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
