const profiles = [
  {
    name: "RAG",
    description: "Deduped, metadata-rich output for LLMs and vector stores.",
    example: "docpull URL --profile rag",
  },
  {
    name: "Mirror",
    description: "Full archive with caching and resume support.",
    example: "docpull URL --profile mirror",
  },
  {
    name: "Quick",
    description: "50 pages, depth 2. For testing and sampling.",
    example: "docpull URL --profile quick",
  },
  {
    name: "Custom",
    description: "No presets. Full control over every parameter.",
    example: "docpull URL --max-pages 500 --depth 4",
  },
];

export default function Profiles() {
  return (
    <section id="profiles" className="py-16 sm:py-24">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-8 sm:mb-12 text-center sm:text-left">
          <h2 className="text-xl sm:text-2xl font-medium mb-2 sm:mb-3">
            <span className="bg-background/50 px-1 rounded">Profiles</span>
          </h2>
          <p className="text-sm sm:text-base text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Pick a profile for your use case.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
          {profiles.map((profile, index) => (
            <div key={index} className="p-4 rounded-xl glass">
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
