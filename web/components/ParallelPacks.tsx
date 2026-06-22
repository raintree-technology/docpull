import Image from "next/image";
import { InfoCard, LandingSection } from "@/components/landing";

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
    title: "Use docpull for known URLs",
    description:
      "Start here when you already have the URL and want a clean Markdown mirror — no browser, no API key.",
    points: [
      "static pages, blogs, docs, and API references",
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
      "ranked source discovery with crawl plans",
      "cited source bundles with a load plan",
      "API and vendor comparison research",
      "diffs, entity dossiers, and batch workflows",
    ],
  },
] as const;

export default function ParallelPacks() {
  return (
    <LandingSection
      id="parallel"
      title={
        <>
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
        </>
      }
      description={
        <>
          Parallel is an optional source-discovery layer. Use docpull when you
          already know the URL. Add Parallel when an agent needs to find
          sources, extract live content, and package everything into a local
          context pack before it starts work.
        </>
      }
      titleClassName="flex flex-col items-center gap-2 sm:flex-row sm:gap-3"
      descriptionClassName="max-w-2xl"
    >
      <div className="mb-4 grid grid-cols-1 gap-3 sm:mb-6 sm:gap-4 md:grid-cols-2">
        {decisionCards.map((card) => (
          <InfoCard
            key={card.title}
            title={card.title}
            description={card.description}
            descriptionClassName="mb-3"
            className="p-4 sm:p-5"
          >
            <ul className="space-y-1.5">
              {card.points.map((point) => (
                <li
                  key={point}
                  className="flex gap-2 text-sm leading-6 text-muted-foreground"
                >
                  <span
                    aria-hidden="true"
                    className="mt-2.5 h-1 w-1 shrink-0 rounded-full bg-foreground/60"
                  />
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </InfoCard>
        ))}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-4">
        {workflows.map((workflow) => (
          <InfoCard
            key={workflow.title}
            title={workflow.title}
            description={workflow.description}
            meta={workflow.command}
          />
        ))}
      </div>
    </LandingSection>
  );
}
