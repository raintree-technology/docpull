const REPO = "https://github.com/raintree-technology/docpull/blob/main";

const features = [
  {
    title: "Clean Markdown, ready to use",
    description:
      "Every page becomes Markdown with a frontmatter header — title, source URL, and description. Code blocks, tables, and images are preserved. Nav, footers, and cookie banners are stripped.",
    srcPath: "src/docpull/conversion/extractor.py",
    srcLabel: "extractor.py",
  },
  {
    title: "No duplicates",
    description:
      "Pages are content-hashed as they stream in — duplicates are caught before they touch disk.",
    srcPath: "src/docpull/pipeline/steps/dedup.py",
    srcLabel: "dedup.py",
  },
  {
    title: "Safe for AI agents",
    description:
      "HTTPS-only, robots.txt compliant, and protected against URL-based attacks — necessary when an AI agent is choosing which URLs to fetch.",
    srcPath: "src/docpull/security/url_validator.py",
    srcLabel: "url_validator.py",
  },
  {
    title: "Cheap to re-run",
    description:
      "Only re-fetches pages that changed since the last run. Interrupted crawls resume where they left off.",
    srcPath: "src/docpull/pipeline/steps/fetch.py",
    srcLabel: "fetch.py",
  },
  {
    title: "Crawl only what matters",
    description:
      "Include and exclude URL patterns during discovery so your agent gets the relevant pages instead of every route the site exposes.",
    srcPath: "src/docpull/discovery/filters.py",
    srcLabel: "filters.py",
  },
  {
    title: "Parallel search packs",
    description:
      "Optional integration with Parallel to find and extract live web sources, organized into a local pack with a load plan your agent can follow.",
    srcPath: "src/docpull/parallel_workflows.py",
    srcLabel: "parallel_workflows.py",
  },
] as const;

export default function Features() {
  return (
    <section id="features" className="pt-16 sm:pt-32 pb-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-12 text-center sm:text-left">
          <h2 className="text-2xl font-medium mb-3">Features</h2>
          <p className="text-muted-foreground">
            The pieces that make web-source fetching dependable.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 sm:gap-6">
          {features.map((feature) => (
            <div key={feature.title} className="p-4 rounded-xl glass flex flex-col gap-2">
              <h3 className="font-medium text-sm">{feature.title}</h3>
              <p className="text-sm text-muted-foreground leading-relaxed flex-1">
                {feature.description}
              </p>
              {feature.srcPath && (
                <a
                  href={`${REPO}/${feature.srcPath}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[11px] font-mono text-muted-foreground/50 hover:text-muted-foreground transition-colors w-fit"
                >
                  {feature.srcLabel}
                </a>
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
