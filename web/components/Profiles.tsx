const profiles = [
  {
    name: "RAG",
    description: "Deduped, metadata-rich output for LLMs and vector stores.",
    badge: "Default",
  },
  {
    name: "Mirror",
    description: "Full archive with caching and resume support.",
    badge: "Archival",
  },
  {
    name: "Quick",
    description: "50 pages, depth 2. For testing and sampling.",
    badge: "Fast",
  },
  {
    name: "Custom",
    description: "No presets. Full control over every parameter.",
    badge: "Advanced",
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
          <p className="text-sm sm:text-base text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Presets for common workflows.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
          {profiles.map((profile, index) => (
            <div key={index} className="p-4 rounded-xl glass">
              <div className="flex items-center justify-between mb-2">
                <h3 className="font-medium">{profile.name}</h3>
                <span className="text-xs text-muted-foreground px-2 py-0.5 glass rounded-md">
                  {profile.badge}
                </span>
              </div>
              <p className="text-sm text-muted-foreground">
                {profile.description}
              </p>
            </div>
          ))}
        </div>

        <p className="mt-4 sm:mt-6 text-sm text-muted-foreground text-center sm:text-left">
          Use with{" "}
          <code className="px-2 py-1 glass rounded-md text-xs">
            --profile rag
          </code>
        </p>
      </div>
    </section>
  );
}
