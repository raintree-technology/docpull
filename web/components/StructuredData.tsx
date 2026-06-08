import { faqs } from "./faq-content";

// Spec: SEO / Structured data (JSON-LD). A single @graph describing the site,
// the publishing organization, the software itself, and the FAQ. Rendered
// server-side so crawlers and AI agents see it in the initial HTML.

const baseUrl = "https://docpull.raintree.technology";

const graph = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": `${baseUrl}/#organization`,
      name: "Raintree Technology",
      url: "https://raintree.technology",
      sameAs: [
        "https://github.com/raintree-technology",
        "https://x.com/raintree_tech",
      ],
    },
    {
      "@type": "WebSite",
      "@id": `${baseUrl}/#website`,
      url: baseUrl,
      name: "docpull",
      description:
        "Local Python crawler that turns server-rendered documentation and Parallel-backed web intelligence into clean Markdown context packs with agent load plans.",
      inLanguage: "en",
      publisher: { "@id": `${baseUrl}/#organization` },
    },
    {
      "@type": "SoftwareApplication",
      "@id": `${baseUrl}/#software`,
      name: "docpull",
      applicationCategory: "DeveloperApplication",
      operatingSystem: "macOS, Linux, Windows",
      url: baseUrl,
      downloadUrl: "https://pypi.org/project/docpull/",
      softwareHelp: "https://github.com/raintree-technology/docpull#readme",
      description:
        "Security-hardened, browser-free crawler that turns static and server-rendered documentation into Markdown agents can read, cite, and reuse.",
      author: { "@id": `${baseUrl}/#organization` },
      license: "https://opensource.org/licenses/MIT",
      isAccessibleForFree: true,
      featureList: [
        "Documentation crawling to Markdown, JSON, NDJSON, and SQLite",
        "Token-aware chunks with stable document and chunk IDs",
        "MCP server for agent access to cached docs",
        "Parallel-backed context packs with AGENT_CONTEXT.md load plans",
        "Entity packs, batch packs, monitor packs, and API packs",
        "Pack scoring and diffing for local agent-readiness checks",
      ],
      offers: {
        "@type": "Offer",
        price: "0",
        priceCurrency: "USD",
      },
    },
    {
      "@type": "FAQPage",
      "@id": `${baseUrl}/#faq`,
      mainEntity: faqs.map((f) => ({
        "@type": "Question",
        name: f.q,
        acceptedAnswer: {
          "@type": "Answer",
          text: f.aText,
        },
      })),
    },
  ],
};

export default function StructuredData() {
  return (
    <script
      type="application/ld+json"
      // Escape "<" so the payload can never break out of the <script> element.
      dangerouslySetInnerHTML={{
        __html: JSON.stringify(graph).replace(/</g, "\\u003c"),
      }}
    />
  );
}
