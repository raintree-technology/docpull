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
        "Python CLI, SDK, and MCP server that turns public static and server-rendered web pages into clean Markdown, NDJSON, and local context packs for AI agents and RAG.",
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
        "Security-hardened, browser-free crawler that turns static and server-rendered web pages into source-linked Markdown agents can read, cite, and reuse.",
      author: { "@id": `${baseUrl}/#organization` },
      license: "https://opensource.org/licenses/MIT",
      isAccessibleForFree: true,
      featureList: [
        "Public web-source crawling to Markdown, JSON, NDJSON, and SQLite",
        "Token-aware chunks with stable document and chunk IDs",
        "MCP server for agent access to cached Markdown sources",
        "Single-URL fetch path for tool calls",
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
