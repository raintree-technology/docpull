import { Zap, Shield, Globe, FileText, RefreshCw, Filter } from "lucide-react";

const features = [
  {
    icon: FileText,
    title: "AI-Ready Output",
    description:
      "Clean Markdown with YAML frontmatter. Ready for RAG and LLM training.",
  },
  {
    icon: Zap,
    title: "Streaming Dedup",
    description: "Real-time duplicate detection. O(1) lookups, minimal memory.",
  },
  {
    icon: Globe,
    title: "JS Rendering",
    description: "Playwright support for SPAs and JavaScript-heavy sites.",
  },
  {
    icon: Shield,
    title: "Secure by Default",
    description: "HTTPS-only, robots.txt compliant, SSRF-protected.",
  },
  {
    icon: RefreshCw,
    title: "Incremental Updates",
    description: "ETag-based caching. Resume interrupted crawls automatically.",
  },
  {
    icon: Filter,
    title: "Content Filtering",
    description: "Filter by language, path, or size. Full control over output.",
  },
];

export default function Features() {
  return (
    <section id="features" className="pt-16 sm:pt-32 pb-24 border-t">
      <div className="mx-auto max-w-5xl px-6">
        <div className="mb-12 text-center sm:text-left">
          <h2 className="text-2xl font-medium mb-3">Features</h2>
          <p className="text-muted-foreground bg-background/50 py-1 rounded inline-block">
            Secure. Fast.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-x-8 gap-y-6">
          {features.map((feature, index) => (
            <div key={index} className="flex gap-3">
              <feature.icon className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
              <div>
                <h3 className="font-medium text-sm mb-1">{feature.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {feature.description}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
