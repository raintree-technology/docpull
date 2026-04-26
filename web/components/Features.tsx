const features = [
  {
    title: "AI-Ready Output",
    description:
      "Markdown with YAML frontmatter — title, source URL, heading outline, OpenGraph description. Drops into a vector store or a `.claude/skills/` directory.",
  },
  {
    title: "Streaming Dedup",
    description:
      "SHA-256-hashed at fetch time. Constant memory per page — duplicate pages are detected before they're written to disk, not after.",
  },
  {
    title: "Zero-Trust Networking",
    description:
      "HTTPS-only, robots.txt compliant, SSRF-protected with DNS pinning at connect time. Built for crawls where an agent picks the URLs — pass --require-pinned-dns to refuse weakened proxy configurations.",
  },
  {
    title: "Conditional Re-fetch",
    description:
      "If-None-Match / If-Modified-Since on every cached page. Re-runs only transfer what changed; the discovered URL list is persisted so a crash resumes instead of restarts.",
  },
  {
    title: "Path & Pattern Filters",
    description:
      "--include-paths and --exclude-paths glob filters at discovery time. Ship only the routes your model needs, not the entire site.",
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
