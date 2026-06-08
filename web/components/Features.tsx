const features = [
  {
    title: "Markdown Agents Can Use",
    description:
      "Every page includes clean Markdown plus frontmatter for title, source URL, headings, and description. Drop it into RAG, search, or a skill directory.",
  },
  {
    title: "No Duplicate Slop",
    description:
      "Pages are SHA-256 hashed while they stream in, so duplicates are caught before they hit disk instead of cleaned up later.",
  },
  {
    title: "Safe for Agent-Chosen URLs",
    description:
      "HTTPS-only, robots.txt compliant, SSRF-protected, and DNS-pinned at connect time. Use --require-pinned-dns when proxy settings weaken that guarantee.",
  },
  {
    title: "Cheap to Re-run",
    description:
      "Cached pages use If-None-Match and If-Modified-Since. Re-runs fetch what changed, and saved frontier state lets interrupted crawls resume.",
  },
  {
    title: "Crawl the Parts That Matter",
    description:
      "Include and exclude path globs during discovery, so your model gets the relevant docs instead of every route the site exposes.",
  },
  {
    title: "Parallel Pack Workflows",
    description:
      "Optional Parallel Search, Extract, Task, entity, batch, monitor, and API-spec workflows become local packs with AGENT_CONTEXT.md, source files, manifests, IDs, and usage metadata.",
  },
];

export default function Features() {
  return (
    <section id="features" className="pt-16 sm:pt-32 pb-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-12 text-center sm:text-left">
          <h2 className="text-2xl font-medium mb-3">
            <span>Features</span>
          </h2>
          <p className="text-muted-foreground">
            The boring pieces that make documentation ingestion dependable.
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
