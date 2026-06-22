import { InfoCard, LandingSection } from "@/components/landing";

const profiles = [
  {
    name: "RAG",
    description: "Clean Markdown with metadata and deduplication for search and retrieval.",
    example: "docpull URL --profile rag",
  },
  {
    name: "Mirror",
    description: "A full local archive with caching, resume on interrupt, and stable file paths.",
    example: "docpull URL --profile mirror",
  },
  {
    name: "Quick",
    description: "A 50-page sample when you want to inspect output before committing to a full crawl.",
    example: "docpull URL --profile quick",
  },
  {
    name: "LLM",
    description:
      "Chunked, streaming records sized for language model context windows. JavaScript-only pages are skipped unless strict mode is on.",
    example: "docpull URL --profile llm --stream | jq .",
  },
];

export default function Profiles() {
  return (
    <LandingSection
      id="profiles"
      title="Profiles"
      description="Choose the output shape before you crawl."
      bordered={false}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4">
        {profiles.map((profile) => (
          <InfoCard
            key={profile.name}
            title={profile.name}
            description={profile.description}
          >
            <code className="mt-4 block overflow-x-auto rounded-md bg-background/60 px-3 py-2.5 font-mono text-[13px] leading-6 text-foreground/80">
              {profile.example}
            </code>
          </InfoCard>
        ))}
      </div>
    </LandingSection>
  );
}
