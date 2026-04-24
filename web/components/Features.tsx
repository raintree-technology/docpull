const features = [
  {
    title: "AI-Ready Output",
    description:
      "Clean Markdown with YAML frontmatter. No post-processing needed — drop it straight into your vector store.",
  },
  {
    title: "Streaming Dedup",
    description:
      "Duplicate pages detected as they're fetched, not after. O(1) lookups keep memory flat on huge sites.",
  },
  {
    title: "Secure by Default",
    description:
      "HTTPS-only, robots.txt compliant, SSRF-protected. Safe to run against untrusted URLs without extra hardening.",
  },
  {
    title: "Incremental Updates",
    description:
      "ETag-based caching skips unchanged pages on re-runs. Crashes resume from where they stopped — no restart cost.",
  },
  {
    title: "Content Filtering",
    description:
      "Filter by language or URL path with --language, --include-paths, --exclude-paths. Ship only what your model needs, not the entire site.",
  },
];

export default function Features() {
  return (
    <section id="features" className="pt-16 sm:pt-32 pb-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-12 text-center sm:text-left">
          <h2 className="text-2xl font-medium mb-3">
            <span className="bg-background/50 px-1 rounded">Features</span>
          </h2>
          <p className="text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Everything you need for production-grade doc ingestion.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
          {features.map((feature, index) => (
            <div key={index} className="p-4 rounded-xl glass">
              <h3 className="font-medium text-sm mb-1">{feature.title}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed">
                {feature.description}
              </p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
