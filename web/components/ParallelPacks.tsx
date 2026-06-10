import Image from "next/image";

const workflows = [
  {
    title: "Discovery & research packs",
    command: "context-pack / discover-docs",
    description:
      "Parallel finds and extracts current web sources. docpull saves them locally as Markdown, structured records, source indexes, and an AGENT_CONTEXT.md load plan.",
  },
  {
    title: "API specs & entity research",
    command: "api-pack / entity-pack",
    description:
      "Turn llms.txt files and OpenAPI specs into local packs, or build dossiers on companies, vendors, and research targets from Parallel's entity search.",
  },
  {
    title: "Diffs & change briefs",
    command: "diff-brief / fallback-pack",
    description:
      "Compare two snapshots of a pack to see what changed, or fall back to Parallel Extract only for pages your local crawl missed.",
  },
] as const;

const decisionCards = [
  {
    title: "Use docpull for known docs",
    description:
      "Start here when you already have the URL and want a clean Markdown mirror — no browser, no API key.",
    points: [
      "static docs and API references",
      "search-ready or skill-ready Markdown",
      "repeatable, offline-friendly archives",
    ],
  },
  {
    title: "Add Parallel for web research",
    description:
      "Use the Parallel layer when you need to find sources first, extract live content, or run entity and batch research before writing local context.",
    points: [
      "research packs from search queries",
      "ranked docs discovery with crawl plans",
      "cited source bundles with a load plan",
      "API-doc and vendor comparison research",
      "diffs, entity dossiers, and batch workflows",
    ],
  },
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
          <p className="text-sm sm:text-base text-muted-foreground max-w-2xl">
            Parallel is an optional source-discovery layer. Use docpull when
            you already know the URL. Add Parallel when an agent needs to find
            sources, extract live content, and package everything into a local
            context pack before it starts work.
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
                    <span
                      aria-hidden="true"
                      className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-foreground/50"
                    />
                    <span>{point}</span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
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
