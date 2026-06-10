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
    <section id="profiles" className="py-16 sm:py-24">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 sm:mb-12 text-center sm:text-left">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            Profiles
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground">
            Choose the output shape before you crawl.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
          {profiles.map((profile) => (
            <div key={profile.name} className="p-4 rounded-xl glass">
              <h3 className="font-medium mb-2">{profile.name}</h3>
              <p className="text-sm text-muted-foreground mb-3">
                {profile.description}
              </p>
              <code className="block px-3 py-2 bg-background/60 rounded-md text-xs font-mono text-muted-foreground overflow-x-auto">
                {profile.example}
              </code>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
